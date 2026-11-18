import os

from sqlalchemy import delete, select, update
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
from mortis.items.edit import ItemError
from mortis.items.identity import (
    ExternalRef,
    MovieRef,
    find_by_external,
    find_by_tmdb,
    find_or_create_movie,
    find_or_create_work,
)
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


def bind_file(session: Session, media_file: MediaFile, ref: MovieRef, locks: KeyedLocks) -> Item:
    if media_file.file_kind != "video":
        raise ItemError("只能给视频文件匹配电影")
    if media_file.item_id is not None:
        raise ItemError("这个文件已经匹配过了", status_code=409)
    library = session.get(Library, media_file.library_id)
    if library is None:
        raise ItemError("媒体库不存在，可能已被删除", status_code=404)
    with locks.hold("catalog-write"):
        item, _ = find_or_create_movie(session, library, ref)
        if is_series(session, library):
            folder = show_folder(library, media_file.path)
            rows = (
                [
                    row
                    for row in session.scalars(
                        select(MediaFile).where(
                            MediaFile.library_id == library.id, MediaFile.item_id.is_(None)
                        )
                    )
                    if _in_folder(row.path, folder)
                ]
                if folder
                else [media_file]
            )
        else:
            rows = _siblings(session, library, media_file)
        for row in rows:
            row.item_id = item.id
            row.updated_at = utc_now()
        session.commit()
    return item
