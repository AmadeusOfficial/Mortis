import gzip
import sqlite3
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2 as httpx
import pytest
from pydantic import ValidationError

from mortis.config.models import AppConfig, MovieScrapeConfig, NetworkConfig, Secrets
from mortis.config.store import ConfigStore
from mortis.net.http import build_client
from mortis.services import Services
from mortis.sources import imdb
from mortis.sources.builtin import FetchRequest
from mortis.tasks.base import TaskError

SAMPLE = [
    ("tt0000001", 5.7, 2100),
    ("tt0111161", 9.3, 3000000),
    ("tt0468569", 9.0, 2900000),
    ("tt9999999", 6.1, 12),
]


def tsv(rows: list[tuple[str, float, int]], header: str = "tconst\taverageRating\tnumVotes") -> str:
    lines = [header] + [f"{tconst}\t{rating}\t{votes}" for tconst, rating, votes in rows]
    return "\n".join(lines) + "\n"


def gz(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"), compresslevel=1)


class FakeDatasets:
    def __init__(self, body: bytes | httpx.SyncByteStream, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.body, bytes):
            return httpx.Response(self.status, content=self.body)
        return httpx.Response(self.status, stream=self.body)

    def client(self) -> httpx.Client:
        return build_client(NetworkConfig(), Secrets(), transport=httpx.MockTransport(self))


class BrokenStream(httpx.SyncByteStream):
    """先给一半数据再断开。"""

    def __init__(self, head: bytes) -> None:
        self.head = head

    def __iter__(self) -> Iterator[bytes]:
        yield self.head
        raise httpx.ReadError("连接断开")


def refresh(data_dir: Path, body: bytes | httpx.SyncByteStream, status: int = 200) -> int:
    fake = FakeDatasets(body, status)
    with fake.client() as http:
        return imdb.refresh_dataset(http, data_dir, lambda _message: None)


def cache_files(data_dir: Path) -> list[str]:
    return sorted(path.name for path in (data_dir / "cache").iterdir())


def make_request(data_dir: Path, ids: dict[str, str], logs: list[str]) -> FetchRequest:
    services = Services(data_dir=data_dir, config_store=ConfigStore(data_dir))
    return FetchRequest(services, "movie", MovieScrapeConfig(), ids, httpx.Client(), logs.append)


def test_refresh_imports_into_cache_file(tmp_path: Path) -> None:
    fake = FakeDatasets(gz(tsv(SAMPLE) + "坏行\n\ntt1\tx\t1\n"))
    logs: list[str] = []
    with fake.client() as http:
        count = imdb.refresh_dataset(http, tmp_path, logs.append)

    assert count == 4
    assert [str(r.url) for r in fake.requests] == [imdb.DATASET_URL]
    assert cache_files(tmp_path) == ["imdb-ratings.sqlite"]
    info = imdb.dataset_info(tmp_path)
    assert info is not None
    assert info.count == 4
    assert datetime.now(UTC) - info.imported_at < timedelta(minutes=1)
    assert logs[-1] == "IMDb 评分数据已更新，共 4 条"


def test_lookup_many_ids(tmp_path: Path) -> None:
    refresh(tmp_path, gz(tsv(SAMPLE)))

    found = imdb.lookup(tmp_path, ["tt0111161", "tt0468569", "tt0111161", "tt404", " ", ""])

    assert found == {"tt0111161": (9.3, 3000000), "tt0468569": (9.0, 2900000)}
    assert imdb.lookup(tmp_path, []) == {}


def test_lookup_without_dataset(tmp_path: Path) -> None:
    assert imdb.dataset_info(tmp_path) is None
    assert imdb.lookup(tmp_path, ["tt0111161"]) == {}


def test_is_stale(tmp_path: Path) -> None:
    assert imdb.is_stale(tmp_path, 7)
    refresh(tmp_path, gz(tsv(SAMPLE)))
    assert not imdb.is_stale(tmp_path, 7)

    old = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    with sqlite3.connect(imdb.cache_path(tmp_path)) as conn:
        conn.execute("update meta set value = ? where key = 'imported_at'", (old,))
    conn.close()

    assert imdb.is_stale(tmp_path, 7)
    assert not imdb.is_stale(tmp_path, 9)


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b"", 503),
        (BrokenStream(gz(tsv(SAMPLE * 50))[:200]), 200),
        (gz(tsv([("tt7", 1.0, 1)] * 2000))[:-40], 200),
        (b"not gzip at all", 200),
        (gz(tsv(SAMPLE, header="id\trating\tvotes")), 200),
        (gz(tsv([])), 200),
    ],
    ids=["http-error", "broken-stream", "truncated-gz", "not-gzip", "bad-header", "empty"],
)
def test_failed_refresh_keeps_old_file(
    tmp_path: Path, body: bytes | httpx.SyncByteStream, status: int
) -> None:
    refresh(tmp_path, gz(tsv(SAMPLE[:2])))
    before = imdb.dataset_info(tmp_path)

    with pytest.raises(TaskError):
        refresh(tmp_path, body, status)

    assert imdb.dataset_info(tmp_path) == before
    assert imdb.lookup(tmp_path, ["tt0111161"]) == {"tt0111161": (9.3, 3000000)}
    assert cache_files(tmp_path) == ["imdb-ratings.sqlite"]


def test_failed_first_refresh_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(TaskError):
        refresh(tmp_path, b"not gzip at all")

    assert imdb.dataset_info(tmp_path) is None
    assert cache_files(tmp_path) == []


def test_fetch_returns_snapshot(tmp_path: Path) -> None:
    refresh(tmp_path, gz(tsv(SAMPLE)))
    logs: list[str] = []
    request = make_request(tmp_path, {"tmdb": "278", "imdb": "tt0111161"}, logs)

    raw = imdb.FETCHER.fetch(request)

    assert raw == {
        "imdb_id": "tt0111161",
        "rating": 9.3,
        "votes": 3000000,
        "dataset_date": datetime.now(UTC).date().isoformat(),
    }
    assert imdb.FETCHER.candidates(raw, request) == {
        "ratings": [{"source": "imdb", "rating": 9.3, "max_rating": 10, "vote_count": 3000000}]
    }


def test_fetch_without_imdb_id(tmp_path: Path) -> None:
    refresh(tmp_path, gz(tsv(SAMPLE)))
    logs: list[str] = []

    assert imdb.FETCHER.fetch(make_request(tmp_path, {"tmdb": "278"}, logs)) is None
    assert logs == ["没有 IMDb 编号，跳过 IMDb 评分"]


def test_fetch_before_dataset_downloaded(tmp_path: Path) -> None:
    logs: list[str] = []
    request = make_request(tmp_path, {"imdb": "tt0111161"}, logs)

    assert imdb.FETCHER.fetch(request) is None
    assert logs == ["IMDb 评分数据还没下载"]
    assert not (tmp_path / "cache").exists()


def test_fetch_id_not_in_dataset(tmp_path: Path) -> None:
    refresh(tmp_path, gz(tsv(SAMPLE)))
    logs: list[str] = []

    assert imdb.FETCHER.fetch(make_request(tmp_path, {"imdb": "tt0000404"}, logs)) is None


def test_settings_and_check() -> None:
    config = AppConfig()
    assert imdb.settings_of(config) == imdb.Settings(enabled=True, refresh_days=7)
    assert imdb.check(config, Secrets()) is None
    assert imdb.secrets_of(Secrets()) == imdb.SecretSettings()

    config.sources["imdb"] = {"enabled": False, "refresh_days": 30, "other": 1}
    assert imdb.settings_of(config).refresh_days == 30
    assert imdb.check(config, Secrets()) == "已关闭"

    for days in (0, 91):
        with pytest.raises(ValidationError):
            imdb.Settings(refresh_days=days)


def test_import_and_lookup_are_fast(tmp_path: Path) -> None:
    rows = [(f"tt{n:07d}", (n % 100) / 10, n) for n in range(200_000)]
    body = gz(tsv(rows))

    started = time.perf_counter()
    assert refresh(tmp_path, body) == 200_000
    imported = time.perf_counter() - started

    started = time.perf_counter()
    found = imdb.lookup(tmp_path, ["tt0123456", "tt0199999", "tt9999999"])
    looked_up = time.perf_counter() - started

    assert found == {"tt0123456": (5.6, 123456), "tt0199999": (9.9, 199999)}
    assert imported < 3
    assert looked_up < 0.05
