"""排除识别：把作品或未识别文件所在的目录加进这种数据类型的跳过目录，再从库里去掉记录。

磁盘上的文件一律不动。预览和执行用同一套计算，执行时重新算一遍，和用户确认过的不一样就拒绝。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from mortis.config.models import _clean_skip_folders
from mortis.config.store import ConfigStore
from mortis.config.types import type_config
from mortis.db.models import DataType, Item, Library, MediaFile, Task
from mortis.items.delete import (
    BUSY_MESSAGE,
    _check_tasks,
    _drop_orphan_images,
    _images_of,
    _tree_ids,
)
from mortis.items.edit import ItemError
from mortis.items.matching import is_series, show_folder
from mortis.organize.scan import norm_path
from mortis.scrape.service import drop_empty_collections
from mortis.services import KeyedLocks
from mortis.tasks import repo as task_repo

PLAYABLE = ("video", "game")


@dataclass
class ExcludePlan:
    library_id: int
    data_type_id: str
    type_name: str
    entries: list[str]
    """要加进跳过目录的完整路径：目录，或者放在共用目录里的视频文件。"""
    file_ids: list[int] = field(default_factory=list)
    item: Item | None = None
    title: str | None = None
    reasons: list[str] = field(default_factory=list)


def _under(path: str, folder: str) -> bool:
    key, base = norm_path(path), norm_path(folder)
    return key == base or key.startswith(base.rstrip(os.sep) + os.sep)


def _is_root(folder: str, roots: list[str]) -> bool:
    return any(norm_path(folder) == norm_path(root) for root in roots)


def _inside_roots(path: str, roots: list[str]) -> bool:
    return any(_under(path, root) and not _is_root(path, [root]) for root in roots)


def _library(session: Session, library_id: int) -> tuple[Library, list[str], str]:
    library = session.get(Library, library_id)
    if library is None:
        raise ItemError("这个库已经不在了", status_code=404)
    data_type = session.get(DataType, library.data_type_id)
    name = data_type.name if data_type is not None else library.data_type_id
    return library, [row.path for row in library.paths], name


def _rows_under(session: Session, library_id: int, folder: str) -> list[MediaFile]:
    return [
        row
        for row in session.scalars(select(MediaFile).where(MediaFile.library_id == library_id))
        if _under(row.path, folder)
    ]


def _check_folder(folder: str, roots: list[str]) -> None:
    if _is_root(folder, roots):
        raise ItemError(f"这是库目录本身，不能排除：{folder}", status_code=400)
    if not _inside_roots(folder, roots):
        raise ItemError(f"这个路径不在库目录里：{folder}", status_code=400)


def plan_item(session: Session, item: Item) -> ExcludePlan:
    if item.kind == "collection":
        raise ItemError("系列不能排除，请排除其中的作品", status_code=400)
    if item.kind != "work":
        raise ItemError("季和集不能单独排除，请排除整部剧", status_code=400)
    library, roots, type_name = _library(session, item.library_id)
    owned = _tree_ids(session, item)
    rows = list(
        session.scalars(
            select(MediaFile).where(MediaFile.item_id.in_(owned)).order_by(MediaFile.path)
        )
    )
    playable = [row for row in rows if row.file_kind in PLAYABLE]
    if not playable:
        raise ItemError("这部作品没有文件，没有目录可以排除", status_code=400)

    def foreign(folder: str) -> bool:
        return any(
            row.item_id not in owned and (row.item_id is not None or row.file_kind in PLAYABLE)
            for row in _rows_under(session, library.id, folder)
        )

    games = sorted({row.path for row in playable if row.file_kind == "game"})
    whole_folder = bool(games) or is_series(session, library)
    if games:
        folders = games
        kind = "游戏目录"
    elif whole_folder:
        shows = {show_folder(library, row.path) for row in playable}
        if None in shows:
            raise ItemError("这部剧有视频直接放在库目录里，没有剧目录可以排除", status_code=400)
        folders = sorted(folder for folder in shows if folder is not None)
        kind = "剧目录"
    else:
        folders = sorted({os.path.dirname(row.path) for row in playable})
        kind = "所在目录"

    plan = ExcludePlan(library.id, library.data_type_id, type_name, [], item=item, title=item.title)
    for folder in folders:
        if whole_folder:
            _check_folder(folder, roots)
            if foreign(folder):
                raise ItemError(f"这个目录里还有别的条目或未识别的文件，不能排除：{folder}")
            plan.entries.append(folder)
            plan.reasons.append(f"排除{kind}「{folder}」")
            continue
        if not _is_root(folder, roots) and _inside_roots(folder, roots) and not foreign(folder):
            plan.entries.append(folder)
            plan.reasons.append(f"排除{kind}「{folder}」")
            continue
        files = sorted(
            row.path
            for row in playable
            if os.path.dirname(row.path) == folder and row.file_kind == "video"
        )
        plan.entries.extend(files)
        plan.reasons.append(f"「{folder}」里还有别的内容，只排除这部作品的 {len(files)} 个视频文件")
    plan.file_ids = [row.id for row in rows]
    return plan


def plan_media_file(session: Session, media_file: MediaFile) -> ExcludePlan:
    if media_file.item_id is not None:
        raise ItemError("这个文件已经识别了，请到作品详情页排除", status_code=400)
    if media_file.file_kind not in PLAYABLE:
        raise ItemError("只有视频和游戏目录能排除", status_code=400)
    library, roots, type_name = _library(session, media_file.library_id)
    plan = ExcludePlan(library.id, library.data_type_id, type_name, [])
    if media_file.file_kind == "game":
        target = media_file.path
    elif is_series(session, library):
        folder = show_folder(library, media_file.path)
        if folder is None:
            raise ItemError("视频直接放在库目录里，没有剧目录可以排除", status_code=400)
        target = folder
    else:
        target = os.path.dirname(media_file.path)
    series = media_file.file_kind == "video" and is_series(session, library)
    rows = _rows_under(session, library.id, target)
    # 未识别页把同一个剧目录的视频合成一行，它们一起排除。
    others = [
        row
        for row in rows
        if row.id != media_file.id
        and (row.item_id is not None or (row.file_kind in PLAYABLE and not series))
    ]
    shared = _is_root(target, roots) or bool(others)
    if shared and media_file.file_kind == "video" and not series:
        plan.entries = [media_file.path]
        plan.file_ids = [media_file.id]
        plan.reasons.append(f"「{target}」里还有别的内容，只排除这个视频文件")
        return plan
    _check_folder(target, roots)
    if others:
        raise ItemError(f"这个目录里还有别的条目或未识别的文件，不能排除：{target}")
    plan.entries = [target]
    plan.file_ids = [row.id for row in rows]
    plan.reasons.append(f"排除「{target}」")
    return plan


def _library_busy(session: Session, library_id: int) -> None:
    busy = session.scalar(
        select(
            exists().where(
                Task.status.in_(task_repo.ACTIVE),
                func.json_extract(Task.payload, "$.library_id") == library_id,
            )
        )
    )
    if busy:
        raise ItemError(BUSY_MESSAGE, status_code=409)


def _add_entries(config_store: ConfigStore, data_type_id: str, entries: list[str]) -> None:
    section = type_config(config_store.load_config(), data_type_id)
    merged = _clean_skip_folders([*section.skip_folders, *entries])
    config_store.save_section(
        f"types.{data_type_id}", section.model_copy(update={"skip_folders": merged})
    )


def _same(plan: ExcludePlan, expected: list[str]) -> None:
    if sorted(plan.entries) != sorted(expected):
        raise ItemError("要排除的目录变了，请重新确认", status_code=409)


def exclude_item(
    session: Session,
    item: Item,
    expected: list[str],
    *,
    config_store: ConfigStore,
    locks: KeyedLocks,
    data_dir: Path,
) -> int:
    """写进跳过目录，再删掉作品（含季和集）和它的文件记录。返回所在库的编号。"""
    plan_item(session, item)
    _check_tasks(session, item)
    library_id = item.library_id
    with locks.hold(f"library:{library_id}"):
        queued = _check_tasks(session, item)
        plan = plan_item(session, item)
        _same(plan, expected)
        if queued:
            _cancelled, running = task_repo.cancel(session, queued)
            if running:
                raise ItemError(BUSY_MESSAGE, status_code=409)
        _add_entries(config_store, plan.data_type_id, plan.entries)
        owned = _tree_ids(session, item)
        with locks.hold("catalog-write"):
            images: set[str] = set()
            for owned_id in owned:
                owned_item = session.get(Item, owned_id)
                if owned_item is not None:
                    images |= _images_of(session, owned_item)
            parent = session.get(Item, item.parent_id) if item.parent_id is not None else None
            if parent is not None and parent.kind == "collection":
                others = session.scalar(
                    select(exists().where(Item.parent_id == parent.id, Item.id != item.id))
                )
                if not others:
                    images |= _images_of(session, parent)
            session.execute(delete(MediaFile).where(MediaFile.item_id.in_(owned)))
            session.execute(delete(Item).where(Item.id == item.id))
            drop_empty_collections(session, library_id)
            session.commit()
            _drop_orphan_images(session, data_dir, images)
    return library_id


def exclude_media_file(
    session: Session,
    media_file: MediaFile,
    expected: list[str],
    *,
    config_store: ConfigStore,
    locks: KeyedLocks,
) -> int:
    """写进跳过目录，再删掉这个目录下未识别的文件记录。返回所在库的编号。"""
    plan_media_file(session, media_file)
    library_id = media_file.library_id
    _library_busy(session, library_id)
    with locks.hold(f"library:{library_id}"):
        _library_busy(session, library_id)
        plan = plan_media_file(session, media_file)
        _same(plan, expected)
        _add_entries(config_store, plan.data_type_id, plan.entries)
        with locks.hold("catalog-write"):
            session.execute(delete(MediaFile).where(MediaFile.id.in_(plan.file_ids)))
            session.commit()
    return library_id
