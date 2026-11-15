import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.db.models import Library, MediaFile, utc_now
from mortis.organize.scan import FoundMovie

SCANNED_KINDS = ("video", "subtitle")


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


def _size(path: str) -> int | None:
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def sync_media_files(session: Session, library: Library, movies: list[FoundMovie]) -> SyncReport:
    """把扫到的文件写进 media_files。只 flush，调用方提交。"""
    existing = {
        row.path: row
        for row in session.scalars(select(MediaFile).where(MediaFile.library_id == library.id))
    }
    seen: set[str] = set()
    groups: list[FoundGroup] = []
    added = 0
    now = utc_now()
    for movie in movies:
        bound = next(
            (
                existing[path].item_id
                for path in movie.videos
                if path in existing and existing[path].item_id is not None
            ),
            None,
        )
        rows: list[MediaFile] = []
        for kind, paths in (("video", movie.videos), ("subtitle", movie.subtitles)):
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
        if path not in seen and row.file_kind in SCANNED_KINDS:
            session.delete(row)
            removed += 1
    session.flush()
    return SyncReport(groups, added, removed)
