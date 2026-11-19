import os
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mortis.db.models import (
    DataType,
    Item,
    ItemSourceRecord,
    Library,
    LibraryPath,
    MediaFile,
    utc_now,
)
from mortis.errors import UserError
from mortis.items.detail import image_url
from mortis.items.identity import manual_clause
from mortis.tasks import repo as task_repo
from mortis.tasks import schedule

MAX_NAME_LENGTH = 50
SAMPLE_POSTERS = 8
AUTOMATIONS = ("manual", "watch", "schedule")
WALL_MOVIES = "movies"
WALL_BOTH = "both"
WALL_COLLAPSE = "collapse"
WALL_COLLECTIONS = (WALL_MOVIES, WALL_BOTH, WALL_COLLAPSE)
DEFAULT_WALL_COLLECTIONS = WALL_COLLAPSE


class LibraryError(UserError):
    pass


def _inside(child: str, parent: str) -> bool:
    return child.startswith(parent.rstrip("\\/") + os.sep)


def _overlaps(a: str, b: str) -> bool:
    key_a, key_b = os.path.normcase(a), os.path.normcase(b)
    return key_a == key_b or _inside(key_a, key_b) or _inside(key_b, key_a)


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise LibraryError("库名不能为空", status_code=422)
    if len(cleaned) > MAX_NAME_LENGTH:
        raise LibraryError(f"库名不能超过 {MAX_NAME_LENGTH} 个字", status_code=422)
    return cleaned


def normalize_paths(raw_paths: list[str]) -> list[str]:
    result: list[str] = []
    for raw in raw_paths:
        path = raw.strip()
        if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
            path = path[1:-1].strip()
        if not path:
            continue
        if not os.path.isabs(path):
            raise LibraryError(f"目录必须是绝对路径：{path}", status_code=422)
        path = os.path.normpath(path)
        if not os.path.isdir(path):
            raise LibraryError(f"目录不存在：{path}", status_code=422)
        for earlier in result:
            if _overlaps(path, earlier):
                raise LibraryError(f"目录重复或互相包含：{earlier} 和 {path}", status_code=422)
        result.append(path)
    if not result:
        raise LibraryError("至少要选一个目录", status_code=422)
    return result


def _check_other_libraries(session: Session, paths: list[str], library_id: int | None) -> None:
    query = select(LibraryPath.path, Library.name).join(
        Library, Library.id == LibraryPath.library_id
    )
    if library_id is not None:
        query = query.where(LibraryPath.library_id != library_id)
    others = session.execute(query).all()
    for path in paths:
        for other_path, other_name in others:
            if _overlaps(path, other_path):
                raise LibraryError(
                    f"目录 {path} 和库「{other_name}」的目录 {other_path} 重复或互相包含",
                    status_code=409,
                )


def get_library(session: Session, library_id: int) -> Library:
    library = session.get(Library, library_id)
    if library is None:
        raise LibraryError("没有这个媒体库", status_code=404)
    return library


def list_libraries(session: Session) -> list[Library]:
    return list(session.scalars(select(Library).order_by(Library.id)))


def library_counts(session: Session, library_ids: list[int]) -> dict[int, tuple[int, int]]:
    """每个库的（作品数，主文件数）。系列电影那一层不算；主文件是视频或游戏文件夹。"""
    items = {
        library_id: count
        for library_id, count in session.execute(
            select(Item.library_id, func.count())
            .where(Item.library_id.in_(library_ids), Item.kind == "work")
            .group_by(Item.library_id)
        )
    }
    files = {
        library_id: count
        for library_id, count in session.execute(
            select(MediaFile.library_id, func.count())
            .where(
                MediaFile.library_id.in_(library_ids),
                MediaFile.file_kind.in_(("video", "game")),
            )
            .group_by(MediaFile.library_id)
        )
    }
    return {lid: (items.get(lid, 0), files.get(lid, 0)) for lid in library_ids}


@dataclass(frozen=True)
class LibraryStats:
    item_count: int = 0
    file_count: int = 0
    scraped_count: int = 0
    manual_count: int = 0
    issue_count: int = 0
    sample_posters: list[str] = field(default_factory=list)


def _scraped_counts(session: Session, library_ids: list[int]) -> dict[int, int]:
    """有来源快照的作品数。判定和海报墙的 is_scraped 一样：有一条 item_source_records 就算。"""
    return {
        library_id: count
        for library_id, count in session.execute(
            select(Item.library_id, func.count(func.distinct(Item.id)))
            .join(ItemSourceRecord, ItemSourceRecord.item_id == Item.id)
            .where(Item.library_id.in_(library_ids), Item.kind == "work")
            .group_by(Item.library_id)
        )
    }


def _manual_counts(session: Session, library_ids: list[int]) -> dict[int, int]:
    """手动条目数。它们没有来源编号，不刮削，也不算待处理。"""
    return {
        library_id: count
        for library_id, count in session.execute(
            select(Item.library_id, func.count())
            .where(Item.library_id.in_(library_ids), manual_clause())
            .group_by(Item.library_id)
        )
    }


def _issue_counts(session: Session, works: list[Any]) -> dict[int, int]:
    """最近一次 scrape 任务失败的作品数，按库分组。"""
    latest = task_repo.latest_scrape_tasks(session, [row.id for row in works])
    result: dict[int, int] = {}
    for row in works:
        task = latest.get(row.id)
        if task is not None and task.status == task_repo.FAILED:
            result[row.library_id] = result.get(row.library_id, 0) + 1
    return result


def _sample_posters(session: Session, library_ids: list[int]) -> dict[int, list[str]]:
    """每个库最新的几个作品主图，跳过没有主图的。"""
    ranked = (
        select(
            Item.library_id.label("library_id"),
            Item.poster_path.label("poster_path"),
            func.row_number()
            .over(
                partition_by=Item.library_id,
                order_by=(Item.created_at.desc(), Item.id.desc()),
            )
            .label("rank"),
        )
        .where(
            Item.library_id.in_(library_ids),
            Item.kind == "work",
            Item.poster_path.is_not(None),
            Item.poster_path != "",
        )
        .subquery()
    )
    result: dict[int, list[str]] = {}
    for library_id, path in session.execute(
        select(ranked.c.library_id, ranked.c.poster_path)
        .where(ranked.c.rank <= SAMPLE_POSTERS)
        .order_by(ranked.c.library_id, ranked.c.rank)
    ):
        url = image_url(path)
        if url is not None:
            result.setdefault(library_id, []).append(url)
    return result


def library_stats(session: Session, library_ids: list[int]) -> dict[int, LibraryStats]:
    """每个库的作品、文件、已刮削、有问题的数量和最新的几张贴图。固定几条查询算完。"""
    counts = library_counts(session, library_ids)
    scraped = _scraped_counts(session, library_ids)
    manual = _manual_counts(session, library_ids)
    works = session.execute(
        select(Item.id, Item.library_id).where(
            Item.library_id.in_(library_ids), Item.kind == "work"
        )
    ).all()
    issues = _issue_counts(session, list(works))
    posters = _sample_posters(session, library_ids)
    return {
        library_id: LibraryStats(
            item_count=counts[library_id][0],
            file_count=counts[library_id][1],
            scraped_count=scraped.get(library_id, 0),
            manual_count=manual.get(library_id, 0),
            issue_count=issues.get(library_id, 0),
            sample_posters=posters.get(library_id, []),
        )
        for library_id in library_ids
    }


def _apply_automation(
    session: Session,
    library: Library,
    *,
    automation: str | None,
    schedule_cron: str | None,
    refresh_running: bool | None,
) -> None:
    """没传的项沿用库现在的计划；改成别的方式时删掉计划行。"""
    if automation is not None:
        if automation not in AUTOMATIONS:
            raise LibraryError(f"没有这种扫描方式：{automation}", status_code=422)
        library.automation = automation
    current = schedule.library_schedules(session, library.id)
    if schedule_cron is not None:
        try:
            cron = schedule.check_cron(schedule_cron)
        except schedule.CronError as error:
            raise LibraryError(f"定时规则不对：{error}", status_code=422) from None
    elif schedule.SCAN in current:
        cron = current[schedule.SCAN].cron
    else:
        cron = schedule.DEFAULT_CRON
    refresh = schedule.REFRESH_RUNNING in current if refresh_running is None else refresh_running
    if refresh and library.data_type.structure != "series":
        if refresh_running:
            raise LibraryError("只有剧和动漫库能刷新连载中的剧", status_code=422)
        refresh = False
    schedule.sync_library_schedules(session, library, cron=cron, refresh_running=refresh)


def _apply_wall_collections(library: Library, wall_collections: str | None) -> None:
    if wall_collections is None:
        return
    if wall_collections not in WALL_COLLECTIONS:
        raise LibraryError(f"没有这种系列显示方式：{wall_collections}", status_code=422)
    if library.data_type.structure != "item_group" and wall_collections != DEFAULT_WALL_COLLECTIONS:
        raise LibraryError("只有电影库能设置海报墙里的系列电影", status_code=422)
    library.wall_collections = wall_collections


def create_library(
    session: Session,
    *,
    name: str,
    data_type_id: str,
    paths: list[str],
    automation: str = "manual",
    schedule_cron: str | None = None,
    refresh_running: bool = False,
    wall_collections: str = DEFAULT_WALL_COLLECTIONS,
) -> Library:
    cleaned_name = _clean_name(name)
    data_type = session.get(DataType, data_type_id)
    if data_type is None or not data_type.is_enabled:
        raise LibraryError(f"没有这种数据类型：{data_type_id}", status_code=422)
    normalized = normalize_paths(paths)
    _check_other_libraries(session, normalized, None)

    now = utc_now()
    library = Library(
        name=cleaned_name,
        data_type_id=data_type_id,
        automation="manual",
        created_at=now,
        updated_at=now,
    )
    library.paths = [LibraryPath(path=path) for path in normalized]
    library.data_type = data_type
    _apply_wall_collections(library, wall_collections)
    session.add(library)
    session.flush()
    _apply_automation(
        session,
        library,
        automation=automation,
        schedule_cron=schedule_cron,
        refresh_running=refresh_running,
    )
    session.commit()
    return library


def update_library(
    session: Session,
    library_id: int,
    *,
    name: str | None = None,
    paths: list[str] | None = None,
    is_auto_scrape: bool | None = None,
    automation: str | None = None,
    schedule_cron: str | None = None,
    refresh_running: bool | None = None,
    wall_collections: str | None = None,
    show_missing_episodes: bool | None = None,
) -> Library:
    library = get_library(session, library_id)
    _apply_wall_collections(library, wall_collections)
    if name is not None:
        library.name = _clean_name(name)
    if paths is not None:
        normalized = normalize_paths(paths)
        _check_other_libraries(session, normalized, library.id)
        # 先删旧行再插新行，否则保留同一目录时会撞上 path 的唯一约束。
        library.paths.clear()
        session.flush()
        library.paths.extend(LibraryPath(path=path) for path in normalized)
    if is_auto_scrape is not None:
        library.is_auto_scrape = int(is_auto_scrape)
    if show_missing_episodes is not None:
        if library.data_type.structure != "series":
            raise LibraryError("只有剧和动漫库能设置是否显示缺少的集", status_code=422)
        library.show_missing_episodes = int(show_missing_episodes)
    _apply_automation(
        session,
        library,
        automation=automation,
        schedule_cron=schedule_cron,
        refresh_running=refresh_running,
    )
    library.updated_at = utc_now()
    session.commit()
    return library


def delete_library(session: Session, library_id: int) -> None:
    """连同库里的条目、文件记录和定时计划一起删；硬盘上的文件不动。"""
    library = get_library(session, library_id)
    schedule.delete_library_schedules(session, library.id)
    session.execute(delete(MediaFile).where(MediaFile.library_id == library.id))
    session.execute(delete(Item).where(Item.library_id == library.id))
    session.delete(library)
    session.commit()
