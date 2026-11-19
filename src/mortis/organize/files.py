import os
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.db.models import Library, MediaFile, utc_now
from mortis.organize.scan import FoundMovie


@dataclass
class FoundGroup:
    movie: FoundMovie
    files: list[MediaFile]
    item_id: int | None


@dataclass
class SyncReport:
    groups: list[FoundGroup]
    added: int
    removed: int
    grown: set[int]
    """这次扫描新挂上视频的条目。"""


def _size(path: str) -> int | None:
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def _top_folder(path: str, roots: list[str]) -> str | None:
    """库根下的第一层目录。直接放在库根里的文件没有这一层。"""
    for root in roots:
        try:
            relative = os.path.relpath(path, root)
        except ValueError:
            continue
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            continue
        head, _, _rest = relative.partition(os.sep)
        if not head or head == os.pardir:
            continue
        return os.path.join(root, head)
    return None


def _follow_renames(
    existing: dict[str, MediaFile],
    roots: list[str],
    movies: list[FoundMovie],
    now: str,
    main_kind: str,
) -> None:
    """文件夹改名后，相对路径和大小都没变的文件仍算原来那一条，不当成新文件。"""
    scanned = {path for movie in movies for path in (*movie.videos, *movie.subtitles)}
    missing: dict[tuple[str, int | None, str], list[tuple[str, MediaFile]]] = {}
    for path, row in existing.items():
        if path in scanned or row.file_kind not in (main_kind, "subtitle"):
            continue
        folder = _top_folder(path, roots)
        if folder is None:
            continue
        relative = os.path.normcase(os.path.relpath(path, folder))
        missing.setdefault((relative, row.size, row.file_kind), []).append((folder, row))

    for movie in movies:
        folders: dict[str, list[tuple[str, MediaFile]]] = {}
        ambiguous = False
        for kind, paths in ((main_kind, movie.videos), ("subtitle", movie.subtitles)):
            for path in paths:
                if path in existing:
                    continue
                relative = os.path.normcase(os.path.relpath(path, movie.folder))
                found = missing.get((relative, _size(path), kind), [])
                if len(found) != 1:
                    ambiguous = ambiguous or len(found) > 1
                    continue
                folder, row = found[0]
                folders.setdefault(folder, []).append((path, row))
        if ambiguous or len(folders) != 1:
            continue
        (old_folder, pairs), *_rest = folders.items()
        moved: set[int] = set()
        for path, row in pairs:
            old = row.path
            row.path = path
            row.updated_at = now
            existing.pop(old, None)
            existing[path] = row
            moved.add(id(row))
            relative = os.path.normcase(os.path.relpath(path, movie.folder))
            missing.pop((relative, row.size, row.file_kind), None)
        for path, row in list(existing.items()):
            if row.file_kind in (main_kind, "subtitle") or id(row) in moved:
                continue
            if _top_folder(path, roots) != old_folder:
                continue
            target = os.path.join(movie.folder, os.path.relpath(path, old_folder))
            if target in existing or not os.path.exists(target):
                continue
            row.path = target
            row.updated_at = now
            existing.pop(path, None)
            existing[target] = row


def sync_media_files(
    session: Session,
    library: Library,
    movies: list[FoundMovie],
    owner: Callable[[int], int] | None = None,
    main_kind: str = "video",
) -> SyncReport:
    """把扫到的文件写进 media_files。只 flush，调用方提交。

    owner 把已绑定的条目换成这一组该挂的条目（剧集的视频挂在集上，新文件要挂到剧上）。
    main_kind 是 movie.videos 记成的种类：游戏目录是 game。
    """
    existing = {
        row.path: row
        for row in session.scalars(select(MediaFile).where(MediaFile.library_id == library.id))
    }
    seen: set[str] = set()
    groups: list[FoundGroup] = []
    added = 0
    grown: set[int] = set()
    now = utc_now()
    _follow_renames(existing, [row.path for row in library.paths], movies, now, main_kind)
    for movie in movies:
        bound = next(
            (
                existing[path].item_id
                for path in movie.videos
                if path in existing and existing[path].item_id is not None
            ),
            None,
        )
        if bound is not None and owner is not None:
            bound = owner(bound)
        rows: list[MediaFile] = []
        for kind, paths in ((main_kind, movie.videos), ("subtitle", movie.subtitles)):
            for path in paths:
                if path in seen:
                    continue
                seen.add(path)
                size = _size(path)
                row = existing.get(path)
                if row is None:
                    row = MediaFile(
                        library_id=library.id,
                        item_id=bound,
                        path=path,
                        file_kind=kind,
                        size=size,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                    added += 1
                    if kind == "video" and bound is not None:
                        grown.add(bound)
                else:
                    if row.size != size:
                        row.size = size
                        row.updated_at = now
                    if row.item_id is None and bound is not None:
                        row.item_id = bound
                        row.updated_at = now
                rows.append(row)
        groups.append(FoundGroup(movie, rows, bound))
    removed = 0
    for path, row in existing.items():
        if path not in seen and row.file_kind in (main_kind, "subtitle"):
            session.delete(row)
            removed += 1
    session.flush()
    return SyncReport(groups, added, removed, grown)
