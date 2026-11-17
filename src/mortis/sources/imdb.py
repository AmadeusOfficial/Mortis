"""IMDb 评分：官方 title.ratings 数据集整份导进数据目录下的缓存文件，刮削时按 IMDb 编号查。

缓存文件不是片库的表，删了重新下载即可。刮削里不下载，下载由「更新 IMDb 评分数据」任务做。
"""

import contextlib
import gzip
import os
import sqlite3
import tempfile
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.config.models import AppConfig, Secrets
from mortis.db.models import Task
from mortis.tasks import repo as task_repo
from mortis.tasks.base import TaskContext, TaskError, TaskResult

if TYPE_CHECKING:
    # builtin.py 会登记这里的 FETCHER，运行时导入会循环。
    from mortis.sources.builtin import FetchRequest

SOURCE = "imdb"
DATASET_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
HEADER = ["tconst", "averageRating", "numVotes"]
CACHE_FILE = Path("cache") / "imdb-ratings.sqlite"
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
LOOKUP_CHUNK = 500
REPLACE_TRIES = 10
"""Windows 上目标文件正被查询打开时 os.replace 会失败，稍等重试。"""


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    refresh_days: int = Field(default=7, ge=1, le=90)
    """数据集导入超过这么多天就该更新。"""


class SecretSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")


def settings_of(config: AppConfig) -> Settings:
    try:
        return Settings.model_validate(config.sources.get(SOURCE, {}))
    except ValidationError:
        return Settings()


def secrets_of(secrets: Secrets) -> SecretSettings:
    try:
        return SecretSettings.model_validate(secrets.sources.get(SOURCE, {}))
    except ValidationError:
        return SecretSettings()


def check(config: AppConfig, _secrets: Secrets) -> str | None:
    return None if settings_of(config).enabled else "已关闭"


@dataclass(frozen=True)
class DatasetInfo:
    imported_at: datetime
    count: int


def cache_path(data_dir: Path) -> Path:
    return data_dir / CACHE_FILE


def rating_entry(rating: float, votes: int) -> dict[str, Any]:
    return {"source": SOURCE, "rating": float(rating), "max_rating": 10, "vote_count": int(votes)}


def _open_read(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def dataset_info(data_dir: Path) -> DatasetInfo | None:
    """已导入数据集的导入时间和条数；还没下载或文件坏了时返回 None。"""
    path = cache_path(data_dir)
    if not path.is_file():
        return None
    try:
        with contextlib.closing(_open_read(path)) as conn:
            meta = dict(conn.execute("select key, value from meta").fetchall())
        return DatasetInfo(datetime.fromisoformat(meta["imported_at"]), int(meta["count"]))
    except sqlite3.Error, KeyError, ValueError:
        return None


def is_stale(data_dir: Path, max_age_days: int) -> bool:
    """还没下载，或导入时间超过 max_age_days 天。"""
    info = dataset_info(data_dir)
    if info is None:
        return True
    return datetime.now(UTC) - info.imported_at > timedelta(days=max_age_days)


def lookup(data_dir: Path, imdb_ids: list[str]) -> dict[str, tuple[float, int]]:
    """一次查多个 IMDb 编号，返回 {编号: (评分, 票数)}；查不到的编号不出现。"""
    path = cache_path(data_dir)
    wanted = list(dict.fromkeys(i.strip() for i in imdb_ids if i and i.strip()))
    if not wanted or not path.is_file():
        return {}
    found: dict[str, tuple[float, int]] = {}
    with contextlib.closing(_open_read(path)) as conn:
        for start in range(0, len(wanted), LOOKUP_CHUNK):
            chunk = wanted[start : start + LOOKUP_CHUNK]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"select tconst, rating, votes from ratings where tconst in ({marks})", chunk
            )
            for tconst, rating, votes in rows:
                found[tconst] = (rating, votes)
    return found


def refresh_dataset(http: httpx.Client, data_dir: Path, log: Callable[[str], None]) -> int:
    """下载数据集并整份替换缓存文件，返回导入的条数。失败时旧文件保留，抛 TaskError。"""
    target = cache_path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    gz_path = _temp_path(target.parent, ".tsv.gz.part")
    db_path = _temp_path(target.parent, ".sqlite.part")
    try:
        log("下载 IMDb 评分数据")
        _download(http, gz_path)
        log("导入 IMDb 评分数据")
        count = _import(gz_path, db_path)
        gz_path.unlink()
        _replace(db_path, target)
    except BaseException:
        for path in (gz_path, db_path):
            path.unlink(missing_ok=True)
        raise
    log(f"IMDb 评分数据已更新，共 {count} 条")
    return count


def _temp_path(folder: Path, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix="imdb-ratings-", suffix=suffix, dir=folder)
    os.close(fd)
    return Path(name)


def _download(http: httpx.Client, path: Path) -> None:
    # 数据集有几十 MB，用宿主客户端的流式请求边收边写，不整份读进内存。
    try:
        with http.stream("GET", DATASET_URL, timeout=DOWNLOAD_TIMEOUT) as response:
            if response.status_code != 200:
                raise TaskError(f"下载 IMDb 评分数据失败：HTTP {response.status_code}")
            with path.open("wb") as out:
                for chunk in response.iter_bytes():
                    out.write(chunk)
    except httpx.HTTPError as exc:
        raise TaskError(f"下载 IMDb 评分数据失败：{type(exc).__name__}") from exc


def _import(gz_path: Path, db_path: Path) -> int:
    db_path.unlink(missing_ok=True)
    try:
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute("pragma journal_mode = off")
            conn.execute("pragma synchronous = off")
            conn.execute(
                "create table ratings (tconst text primary key, rating real, votes integer)"
                " without rowid"
            )
            conn.execute("create table meta (key text primary key, value text)")
            with gzip.open(gz_path, "rt", encoding="utf-8", newline="") as lines:
                conn.executemany("insert or replace into ratings values (?, ?, ?)", _rows(lines))
            count = conn.execute("select count(*) from ratings").fetchone()[0]
            if not count:
                raise TaskError("IMDb 评分数据是空的")
            conn.executemany(
                "insert into meta values (?, ?)",
                [("imported_at", datetime.now(UTC).isoformat()), ("count", str(count))],
            )
            conn.commit()
        return count
    except (OSError, EOFError, zlib.error, UnicodeDecodeError, sqlite3.Error) as exc:
        raise TaskError(f"IMDb 评分数据损坏，导入失败：{type(exc).__name__}") from exc


def _rows(lines: Iterator[str]) -> Iterator[tuple[str, float, int]]:
    if next(lines, "").rstrip("\r\n").split("\t") != HEADER:
        raise TaskError("IMDb 评分数据格式不对：表头不是 tconst、averageRating、numVotes")
    for line in lines:
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) != 3:
            continue
        try:
            yield parts[0], float(parts[1]), int(parts[2])
        except ValueError:
            continue


def _replace(source: Path, target: Path) -> None:
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise TaskError("IMDb 评分缓存文件正被占用，替换失败") from None
            time.sleep(0.2)


class _ImdbFetcher:
    def fetch(self, request: FetchRequest) -> dict[str, Any] | None:
        imdb_id = (request.ids.get(SOURCE) or "").strip()
        if not imdb_id:
            request.log("没有 IMDb 编号，跳过 IMDb 评分")
            return None
        data_dir = request.services.data_dir
        info = dataset_info(data_dir)
        if info is None:
            request.log("IMDb 评分数据还没下载")
            return None
        found = lookup(data_dir, [imdb_id]).get(imdb_id)
        if found is None:
            return None
        rating, votes = found
        return {
            "imdb_id": imdb_id,
            "rating": rating,
            "votes": votes,
            "dataset_date": info.imported_at.date().isoformat(),
        }

    def candidates(self, raw: dict[str, Any], _request: FetchRequest) -> dict[str, Any]:
        if not raw.get("votes"):
            return {}
        return {"ratings": [rating_entry(raw["rating"], raw["votes"])]}


FETCHER = _ImdbFetcher()

REFRESH_TASK = "refresh_imdb"
NOT_DOWNLOADED = "评分数据还没下载"


def ready(data_dir: Path) -> str | None:
    return None if cache_path(data_dir).is_file() else NOT_DOWNLOADED


def handle_refresh(_payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    with ctx.services.http_client() as http:
        count = refresh_dataset(http, ctx.services.data_dir, ctx.log)
    return TaskResult(result={"count": count})


def queue_refresh(session: Session) -> Task:
    """已经有排队或正在跑的更新任务时不再排一个。"""
    active = session.scalar(
        select(Task).where(
            Task.task_type == REFRESH_TASK, Task.status.in_((task_repo.QUEUED, task_repo.RUNNING))
        )
    )
    return active or task_repo.enqueue(session, REFRESH_TASK, {})


def queue_refresh_if_stale(session: Session, config: AppConfig, data_dir: Path) -> Task | None:
    settings = settings_of(config)
    if not settings.enabled or not is_stale(data_dir, settings.refresh_days):
        return None
    return queue_refresh(session)
