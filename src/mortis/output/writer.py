"""把库里的 NFO 和图片写到视频旁边。只删自己写过的文件，别人的文件不动。"""

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.config.models import MovieScrapeConfig
from mortis.db.models import Item, ItemImage, ItemPerson, MediaFile, Person, utc_now
from mortis.organize.names import strip_stack
from mortis.output.nfo import build_movie_nfo
from mortis.scrape.images import ImageStore

WRITTEN_KINDS = ("nfo", "image")
WORK_IMAGES = {
    "poster": "poster",
    "fanart": "fanart",
    "banner": "banner",
    "clearlogo": "clearlogo",
    "clearart": "clearart",
    "discart": "disc",
    "keyart": "keyart",
    "thumb": "thumb",
}
_EDGE = " ._-"
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class WriteReport:
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class _Target:
    path: Path
    kind: str
    content: bytes | None = None
    source: Path | None = None


def actor_file_name(name: str) -> str:
    return _UNSAFE.sub("", name).strip().replace(" ", "_")


def _video_groups(videos: list[MediaFile]) -> list[tuple[Path, str]]:
    """每个目录、每组分段只取一个：(目录, 去掉分段后缀的文件名)。"""
    groups: dict[tuple[str, str], tuple[Path, str]] = {}
    for video in sorted(videos, key=lambda row: row.path):
        path = Path(video.path)
        base, part = strip_stack(path.stem)
        stem = base.rstrip(_EDGE) if part else path.stem
        groups.setdefault((str(path.parent).casefold(), stem.casefold()), (path.parent, stem))
    return list(groups.values())


def _shared_folders(session: Session, item: Item, folders: set[Path]) -> set[Path]:
    """同一目录里还有别的条目或未识别的视频，就不写 movie.nfo 这类不带文件名的文件。"""
    shared: set[Path] = set()
    for row in session.scalars(
        select(MediaFile).where(
            MediaFile.library_id == item.library_id,
            MediaFile.file_kind == "video",
            (MediaFile.item_id != item.id) | MediaFile.item_id.is_(None),
        )
    ):
        folder = Path(row.path).parent
        if folder in folders:
            shared.add(folder)
    return shared


def _first_images(session: Session, item_id: int) -> dict[str, str]:
    first: dict[str, str] = {}
    for kind, rel in session.execute(
        select(ItemImage.image_kind, ItemImage.file_path)
        .where(ItemImage.item_id == item_id, ItemImage.file_path.is_not(None))
        .order_by(ItemImage.position)
    ):
        first.setdefault(kind, rel)
    return first


def _targets(
    session: Session, store: ImageStore, item: Item, config: MovieScrapeConfig
) -> tuple[list[_Target], list[str]]:
    output = config.output
    videos = list(
        session.scalars(
            select(MediaFile).where(MediaFile.item_id == item.id, MediaFile.file_kind == "video")
        )
    )
    groups = _video_groups(videos)
    shared = _shared_folders(session, item, {folder for folder, _ in groups})
    nfo = build_movie_nfo(
        session,
        item,
        certification_country=config.certification_country,
        write_lockdata=output.nfo_write_lockdata,
        single_studio=output.nfo_single_studio,
    )
    images: dict[str, str] = {**_first_images(session, item.id)}
    if item.poster_path:
        images["poster"] = item.poster_path
    parent = session.get(Item, item.parent_id) if item.parent_id else None
    collection: dict[str, str] = {}
    if parent is not None and output.collection_image_names:
        collection = {k: v for k, v in _first_images(session, parent.id).items() if k == "fanart"}
        if parent.poster_path:
            collection["poster"] = parent.poster_path
    actors = (
        list(
            session.execute(
                select(Person.name, Person.photo_path)
                .join(ItemPerson, ItemPerson.person_id == Person.id)
                .where(
                    ItemPerson.item_id == item.id,
                    ItemPerson.role == "actor",
                    Person.photo_path.is_not(None),
                )
                .order_by(ItemPerson.position)
            )
        )
        if config.images.actor_images
        else []
    )

    targets: list[_Target] = []
    missing: list[str] = []

    def image(path: Path, rel: str) -> None:
        source = store.resolve(rel)
        if source is None:
            missing.append(str(path))
        else:
            targets.append(_Target(path.with_suffix(source.suffix), "image", source=source))

    for folder, stem in groups:
        alone = folder not in shared
        for name in output.nfo_names:
            if name == "filename":
                targets.append(_Target(folder / f"{stem}.nfo", "nfo", content=nfo))
            elif alone:
                targets.append(_Target(folder / "movie.nfo", "nfo", content=nfo))
        plain = output.image_style == "plain" and alone
        for kind, file_kind in WORK_IMAGES.items():
            if kind in images:
                name = file_kind if plain else f"{stem}-{file_kind}"
                image(folder / f"{name}.jpg", images[kind])
        if alone:
            for kind, rel in collection.items():
                image(folder / f"movieset-{kind}.jpg", rel)
        for name, rel in actors:
            file_name = actor_file_name(name)
            if file_name:
                image(folder / output.actor_images_folder / f"{file_name}.jpg", rel)
    unique: dict[str, _Target] = {}
    for target in targets:
        unique.setdefault(str(target.path).casefold(), target)
    return list(unique.values()), missing


def _same(path: Path, target: _Target) -> bool:
    try:
        if target.source is not None:
            if path.stat().st_size != target.source.stat().st_size:
                return False
            return path.read_bytes() == target.source.read_bytes()
        return path.read_bytes() == target.content
    except OSError:
        return False


def _write(target: _Target) -> None:
    target.path.parent.mkdir(parents=True, exist_ok=True)
    data = target.content if target.content is not None else target.source.read_bytes()
    fd, temp = tempfile.mkstemp(dir=target.path.parent, prefix=".mortis-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, target.path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def write_item_files(
    session: Session, store: ImageStore, item: Item, config: MovieScrapeConfig
) -> WriteReport:
    """写出一个条目的文件并记进 media_files。只 flush，调用方提交。"""
    report = WriteReport()
    targets, report.skipped = _targets(session, store, item, config)
    tracked = {
        row.path.casefold(): row
        for row in session.scalars(
            select(MediaFile).where(
                MediaFile.item_id == item.id, MediaFile.file_kind.in_(WRITTEN_KINDS)
            )
        )
    }
    now = utc_now()
    for target in targets:
        path = str(target.path)
        if _same(target.path, target):
            report.unchanged.append(path)
        else:
            _write(target)
            report.written.append(path)
        size = target.path.stat().st_size
        row = tracked.pop(path.casefold(), None) or session.scalar(
            select(MediaFile).where(MediaFile.path == path)
        )
        if row is None:
            session.add(
                MediaFile(
                    library_id=item.library_id,
                    item_id=item.id,
                    path=path,
                    file_kind=target.kind,
                    size=size,
                    created_at=now,
                    updated_at=now,
                )
            )
        elif row.file_kind in WRITTEN_KINDS:
            if (row.item_id, row.size) != (item.id, size):
                row.item_id, row.size, row.updated_at = item.id, size, now
    for row in tracked.values():
        Path(row.path).unlink(missing_ok=True)
        _remove_empty_dir(Path(row.path).parent, config.output.actor_images_folder)
        report.removed.append(row.path)
        session.delete(row)
    session.flush()
    return report


def _remove_empty_dir(folder: Path, actor_folder: str) -> None:
    if folder.name == actor_folder:
        try:
            folder.rmdir()
        except OSError:
            pass
