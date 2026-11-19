import os
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from mortis.db.models import (
    DataType,
    Item,
    ItemExternalId,
    ItemSourceRecord,
    Library,
    MediaFile,
    utc_now,
)
from mortis.items.edit import ItemError, apply_edit, prepare_values
from mortis.items.identity import (
    ExternalRef,
    MovieRef,
    create_manual_work,
    find_by_external,
    find_by_tmdb,
    find_or_create_movie,
    find_or_create_work,
)
from mortis.items.seasons import bind_episodes, child
from mortis.organize.episodes import parse_episode
from mortis.organize.names import (
    ParsedName,
    clean_game_name,
    parse_folder_name,
    parse_name,
    stack_key,
    strip_stack,
    subtitle_belongs_to,
)
from mortis.services import KeyedLocks

_DIGITS = re.compile(r"([0-9]+)")


def is_series(session: Session, item_or_library: Item | Library) -> bool:
    data_type = session.get(DataType, item_or_library.data_type_id)
    return data_type is not None and data_type.structure == "series"


def _drop_seasons(session: Session, show: Item) -> None:
    """视频挂回剧，再删掉剧下面的季和集（集跟着季级联删除）。"""
    season_ids = select(Item.id).where(Item.parent_id == show.id, Item.kind == "season")
    episode_ids = select(Item.id).where(Item.parent_id.in_(season_ids))
    session.execute(
        update(MediaFile)
        .where(MediaFile.item_id.in_(episode_ids))
        .values(item_id=show.id, updated_at=utc_now())
    )
    session.execute(delete(Item).where(Item.parent_id == show.id, Item.kind == "season"))


def rematch(session: Session, item: Item, tmdb_id: int) -> None:
    other = find_by_tmdb(session, item.library_id, tmdb_id)
    if other is not None and other.id != item.id:
        raise ItemError(f"库里已经有《{other.title}》", status_code=409)
    if is_series(session, item):
        _drop_seasons(session, item)
    session.execute(
        delete(ItemExternalId).where(
            ItemExternalId.item_id == item.id,
            ItemExternalId.provider.in_(("tmdb", "imdb", "tvdb")),
        )
    )
    session.execute(
        delete(ItemSourceRecord).where(
            ItemSourceRecord.item_id == item.id, ItemSourceRecord.source == "tmdb"
        )
    )
    session.add(
        ItemExternalId(item_id=item.id, provider="tmdb", external_id=str(tmdb_id), is_primary=1)
    )
    item.updated_at = utc_now()
    session.commit()


def rematch_external(session: Session, item: Item, provider: str, external_id: str) -> None:
    """插件类型认错了条目：清掉所有外部编号和快照，用选中的来源编号重新刮。"""
    other = find_by_external(session, item.library_id, provider, external_id)
    if other is not None and other.id != item.id:
        raise ItemError(f"库里已经有《{other.title}》", status_code=409)
    session.execute(delete(ItemExternalId).where(ItemExternalId.item_id == item.id))
    session.execute(delete(ItemSourceRecord).where(ItemSourceRecord.item_id == item.id))
    session.add(
        ItemExternalId(item_id=item.id, provider=provider, external_id=external_id, is_primary=1)
    )
    item.updated_at = utc_now()
    session.commit()


def add_source(session: Session, item: Item, source: str, provider: str, external_id: str) -> None:
    """插件类型补一个来源：只换这个来源的编号并丢掉它的快照，主编号和其他来源不动。"""
    current = session.get(ItemExternalId, (item.id, provider))
    if current is not None and current.is_primary:
        raise ItemError("这是条目的主来源，要换请用「重新匹配」")
    if current is None:
        session.add(
            ItemExternalId(
                item_id=item.id, provider=provider, external_id=external_id, is_primary=0
            )
        )
    else:
        current.external_id = external_id
    session.execute(
        delete(ItemSourceRecord).where(
            ItemSourceRecord.item_id == item.id, ItemSourceRecord.source == source
        )
    )
    item.updated_at = utc_now()
    session.commit()


def _unbound_rows(
    session: Session,
    media_file: MediaFile,
    bound_message: str,
    collect: Callable[[], list[MediaFile]],
) -> list[MediaFile]:
    """持有 catalog-write 锁时调用：锁外读到的文件可能已被别处绑定或删除，重读再收集。"""
    try:
        session.refresh(media_file)
    except InvalidRequestError:
        raise ItemError("没有这个文件", status_code=404) from None
    if media_file.item_id is not None:
        raise ItemError(bound_message, status_code=409)
    rows = collect()
    if media_file not in rows:
        raise ItemError(bound_message, status_code=409)
    return rows


def bind_folder(
    session: Session, media_file: MediaFile, ref: ExternalRef, locks: KeyedLocks
) -> Item:
    if media_file.file_kind != "game":
        raise ItemError("只能给游戏目录按来源匹配")
    if media_file.item_id is not None:
        raise ItemError("这个目录已经匹配过了", status_code=409)
    library = session.get(Library, media_file.library_id)
    if library is None:
        raise ItemError("媒体库不存在，可能已被删除", status_code=404)
    with locks.hold("catalog-write"):
        _unbound_rows(session, media_file, "这个目录已经匹配过了", lambda: [media_file])
        item, _ = find_or_create_work(session, library, ref)
        media_file.item_id = item.id
        media_file.updated_at = utc_now()
        session.commit()
    return item


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def _is_root(library: Library, folder: str) -> bool:
    return any(_same_path(p.path, folder) for p in library.paths)


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def show_folder(library: Library, path: str) -> str | None:
    """文件所在的剧目录：库根目录下的第一层子目录。直接放在库根目录的返回 None。"""
    for root in library.paths:
        try:
            relative = os.path.relpath(path, root.path)
        except ValueError:
            continue
        parts = relative.split(os.sep)
        if parts[0] != os.pardir and len(parts) > 1:
            return os.path.join(root.path, parts[0])
    return None


def _in_folder(path: str, folder: str) -> bool:
    return os.path.normcase(path).startswith(os.path.normcase(folder) + os.sep)


def guess(library: Library, path: str, *, series: bool = False, folder: bool = False) -> ParsedName:
    if folder:
        title = clean_game_name(os.path.basename(path))
        return ParsedName(title=title, year=None, search_titles=(title,))
    if series:
        folder = show_folder(library, path)
        return parse_name(os.path.basename(folder) if folder else _stem(path))
    folder = os.path.dirname(path)
    if _is_root(library, folder):
        return parse_name(strip_stack(_stem(path))[0])
    return parse_folder_name(os.path.basename(folder), _stem(path))


def guess_file(session: Session, media_file: MediaFile) -> ParsedName:
    library = session.get(Library, media_file.library_id)
    if library is None:
        raise ItemError("媒体库不存在，可能已被删除", status_code=404)
    return guess(
        library,
        media_file.path,
        series=is_series(session, library),
        folder=media_file.file_kind == "game",
    )


def main_files(session: Session, item: Item, limit: int = 10) -> list[MediaFile]:
    """条目的视频或游戏目录；剧把各集的视频也算上。"""
    owners: list[int] = [item.id]
    if item.kind == "work" and is_series(session, item):
        seasons = list(session.scalars(select(Item.id).where(Item.parent_id == item.id)))
        episodes = list(session.scalars(select(Item.id).where(Item.parent_id.in_(seasons))))
        owners += seasons + episodes
    return list(
        session.scalars(
            select(MediaFile)
            .where(MediaFile.item_id.in_(owners), MediaFile.file_kind.in_(("video", "game")))
            .order_by(MediaFile.path, MediaFile.id)
            .limit(limit)
        )
    )


def _siblings(session: Session, library: Library, media_file: MediaFile) -> list[MediaFile]:
    """同目录、同分段组的视频，加上归它的字幕。不是库根目录且只有这一组时，同目录字幕全归它。"""
    folder = os.path.dirname(media_file.path)
    key = stack_key(_stem(media_file.path))
    rows = [
        row
        for row in session.scalars(
            select(MediaFile).where(
                MediaFile.library_id == media_file.library_id, MediaFile.item_id.is_(None)
            )
        )
        if os.path.dirname(row.path) == folder
    ]
    videos = [row for row in rows if row.file_kind == "video"]
    same = [row for row in videos if stack_key(_stem(row.path)) == key]
    base = strip_stack(_stem(media_file.path))[0].strip(" ._-")
    only_group = len(same) == len(videos) and not _is_root(library, folder)
    subtitles = [
        row
        for row in rows
        if row.file_kind == "subtitle"
        and (only_group or subtitle_belongs_to(os.path.basename(row.path), base))
    ]
    return same + subtitles


def _show_files(session: Session, library: Library, media_file: MediaFile) -> list[MediaFile]:
    """剧目录下所有未绑定的文件；直接放在库根目录的只取这一个。"""
    folder = show_folder(library, media_file.path)
    if folder is None:
        return [media_file]
    return [
        row
        for row in session.scalars(
            select(MediaFile).where(MediaFile.library_id == library.id, MediaFile.item_id.is_(None))
        )
        if _in_folder(row.path, folder)
    ]


def _rows_to_bind(
    session: Session, library: Library, media_file: MediaFile, *, game: bool, series: bool
) -> list[MediaFile]:
    if game:
        return [media_file]
    if series:
        return _show_files(session, library, media_file)
    return _siblings(session, library, media_file)


def _episode_numbers(paths: list[str]) -> dict[str, tuple[int, int]]:
    """视频路径 → (季号, 集号)。都解析得出且不重复就照文件名，没写季号的当第 1 季；
    否则全部按路径排序编为第 1 季第 1..N 集。一个文件多集时挂到第一集。"""
    parsed: dict[str, tuple[int, int]] = {}
    for path in paths:
        number = parse_episode(_stem(path), os.path.basename(os.path.dirname(path)))
        if number is None:
            break
        parsed[path] = (1 if number.season is None else number.season, number.episodes[0])
    if len(parsed) == len(paths) and len(set(parsed.values())) == len(parsed):
        return parsed
    ordered = sorted(paths, key=_natural_key)
    return {path: (1, index) for index, path in enumerate(ordered, start=1)}


def _natural_key(path: str) -> tuple[tuple[int, int, str], ...]:
    """数字段按整数比，其余按 casefold 比：P2 排在 P10 前面。"""
    return tuple(
        (1, int(part), "") if part.isascii() and part.isdigit() else (0, 0, part.casefold())
        for part in _DIGITS.split(path)
        if part
    )


def _build_episodes(session: Session, show: Item, files: list[MediaFile]) -> None:
    """按文件建季和集，集标题取文件名；视频挂到集，字幕跟同名视频走。"""
    videos = [row.path for row in files if row.file_kind == "video"]
    targets: dict[str, int] = {}
    for path, (season_number, episode_number) in _episode_numbers(videos).items():
        season = child(session, show, "season", season_number)
        episode = child(session, season, "episode", episode_number)
        episode.title = _stem(path)
        targets[path] = episode.id
    bind_episodes(session, files, targets, show.id)


def bind_manual(
    session: Session, media_file: MediaFile, values: dict[str, Any], locks: KeyedLocks
) -> Item:
    """未识别的视频或游戏目录建一个手动条目。值为空的键不写也不锁；类型里没有的键忽略。"""
    if media_file.file_kind not in ("video", "game"):
        raise ItemError("只能给视频文件或游戏目录建条目")
    if media_file.item_id is not None:
        raise ItemError("这个文件已经匹配过了", status_code=409)
    library = session.get(Library, media_file.library_id)
    if library is None:
        raise ItemError("媒体库不存在，可能已被删除", status_code=404)
    filled = {
        key: value
        for key, value in values.items()
        if value is not None and not (isinstance(value, str) and not value.strip())
    }
    title = str(filled.get("title") or "").strip()
    if not title:
        raise ItemError("标题不能为空")
    game = media_file.file_kind == "game"
    series = not game and is_series(session, library)
    prepared = prepare_values(session, library.data_type_id, "work", filled, skip_unknown=True)
    with locks.hold("catalog-write"):
        rows = _unbound_rows(
            session,
            media_file,
            "这个文件已经匹配过了",
            lambda: _rows_to_bind(session, library, media_file, game=game, series=series),
        )
        item = create_manual_work(session, library, title)
        apply_edit(session, item, prepared)
        for row in rows:
            row.item_id = item.id
            row.updated_at = utc_now()
        if series:
            _build_episodes(session, item, rows)
        session.commit()
    return item


def bind_file(session: Session, media_file: MediaFile, ref: MovieRef, locks: KeyedLocks) -> Item:
    if media_file.file_kind != "video":
        raise ItemError("只能给视频文件匹配电影")
    if media_file.item_id is not None:
        raise ItemError("这个文件已经匹配过了", status_code=409)
    library = session.get(Library, media_file.library_id)
    if library is None:
        raise ItemError("媒体库不存在，可能已被删除", status_code=404)
    series = is_series(session, library)
    with locks.hold("catalog-write"):
        rows = _unbound_rows(
            session,
            media_file,
            "这个文件已经匹配过了",
            lambda: _rows_to_bind(session, library, media_file, game=False, series=series),
        )
        item, _ = find_or_create_movie(session, library, ref)
        for row in rows:
            row.item_id = item.id
            row.updated_at = utc_now()
        session.commit()
    return item
