import os

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mortis.db.models import DataType, Item, Library, LibraryPath, MediaFile, utc_now
from mortis.errors import UserError

MAX_NAME_LENGTH = 50


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


def create_library(session: Session, *, name: str, data_type_id: str, paths: list[str]) -> Library:
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
    session.add(library)
    session.commit()
    return library


def update_library(
    session: Session,
    library_id: int,
    *,
    name: str | None = None,
    paths: list[str] | None = None,
    is_auto_scrape: bool | None = None,
) -> Library:
    library = get_library(session, library_id)
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
    library.updated_at = utc_now()
    session.commit()
    return library


def delete_library(session: Session, library_id: int) -> None:
    """连同库里的条目和文件记录一起删；硬盘上的文件不动。"""
    library = get_library(session, library_id)
    session.execute(delete(MediaFile).where(MediaFile.library_id == library.id))
    session.execute(delete(Item).where(Item.library_id == library.id))
    session.delete(library)
    session.commit()
