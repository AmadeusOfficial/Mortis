"""按模板算出一个条目的目录和文件该叫什么。只算不动，冲突时整条跳过。"""

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.config.models import MovieOutputConfig
from mortis.db.models import Item, LibraryPath, MediaFile
from mortis.organize.names import strip_stack, subtitle_belongs_to
from mortis.organize.scan import DISC_DIR_NAMES, SUBTITLE_DIR_NAMES
from mortis.rename.template import render

_EDGE = " ._-"


@dataclass
class Move:
    file_id: int
    source: str
    target: str


@dataclass
class RenamePlan:
    item_id: int
    folder_from: str | None = None
    folder_to: str | None = None
    make_folder: bool = False
    moves: list[Move] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def changes(self) -> bool:
        return bool(self.folder_to or any(m.source != m.target for m in self.moves))


def tokens_of(item: Item) -> dict[str, str | None]:
    return {
        "title": item.title,
        "originalTitle": item.original_title,
        "year": (item.release_date or "")[:4] or None,
        "edition": None,
        "videoFormat": None,
        "audioCodec": None,
    }


def _inside(path: Path, folder: Path) -> bool:
    return path.is_relative_to(folder) or str(path).casefold().startswith(
        str(folder).casefold() + "\\"
    )


def _same(a: Path, b: Path) -> bool:
    return str(a).casefold() == str(b).casefold()


def _movie_folder(video: Path) -> tuple[Path, bool]:
    """返回电影所在目录和视频是不是光盘结构里的文件。"""
    if video.parent.name.casefold() in DISC_DIR_NAMES:
        return video.parent.parent, True
    return video.parent, False


def plan_rename(session: Session, item: Item, output: MovieOutputConfig) -> RenamePlan:
    plan = RenamePlan(item.id)
    rows = list(session.scalars(select(MediaFile).where(MediaFile.item_id == item.id)))
    videos = sorted((r for r in rows if r.file_kind == "video"), key=lambda r: r.path)
    if not videos:
        plan.problems.append("没有视频文件")
        return plan
    folders = {_movie_folder(Path(v.path)) for v in videos}
    if len({str(f).casefold() for f, _ in folders}) > 1:
        plan.problems.append("视频分散在多个目录，不重命名")
        return plan
    (folder, is_disc), *_ = folders
    tokens = tokens_of(item)
    try:
        folder_name = render(
            output.folder_template,
            tokens,
            space=output.folder_space,
            colon=output.colon_replacement,
        )
        file_stem = render(
            output.file_template, tokens, space=output.file_space, colon=output.colon_replacement
        )
    except ValueError as error:
        plan.problems.append(str(error))
        return plan

    roots = [
        Path(p)
        for p in session.scalars(
            select(LibraryPath.path).where(LibraryPath.library_id == item.library_id)
        )
    ]
    at_root = any(_same(folder, root) for root in roots)
    others = [
        Path(r.path)
        for r in session.scalars(
            select(MediaFile).where(
                MediaFile.library_id == item.library_id,
                MediaFile.file_kind == "video",
                (MediaFile.item_id != item.id) | MediaFile.item_id.is_(None),
            )
        )
    ]
    alone = not at_root and not any(_inside(path, folder) for path in others)

    if at_root:
        new_folder = folder / folder_name
        plan.make_folder = True
        plan.folder_to = str(new_folder)
    elif alone:
        new_folder = folder.parent / folder_name
        if folder.name != folder_name:
            plan.folder_from, plan.folder_to = str(folder), str(new_folder)
    else:
        new_folder = folder

    stems: list[tuple[str, str]] = []
    for video in videos:
        path = Path(video.path)
        if is_disc:
            target = new_folder / path.relative_to(folder)
        else:
            base, part = strip_stack(path.stem)
            base = base.rstrip(_EDGE) if part else path.stem
            stems.append((base, file_stem))
            target = new_folder / f"{file_stem}{path.stem[len(base) :]}{path.suffix}"
        plan.moves.append(Move(video.id, video.path, str(target)))
    for sub in (r for r in rows if r.file_kind == "subtitle"):
        path = Path(sub.path)
        name = path.name
        for old, new in stems:
            if subtitle_belongs_to(name, old):
                name = new + name[len(old) :]
                break
        if path.parent.name.casefold() in SUBTITLE_DIR_NAMES and _inside(path, folder):
            target = new_folder / path.parent.relative_to(folder) / name
        elif alone or at_root:
            target = new_folder / path.relative_to(folder).parent / name
        else:
            target = path.parent / name
        plan.moves.append(Move(sub.id, sub.path, str(target)))
    if plan.folder_to and not plan.make_folder:
        for row in rows:
            if row.file_kind in ("nfo", "image"):
                target = new_folder / Path(row.path).relative_to(folder)
                plan.moves.append(Move(row.id, row.path, str(target)))
    _check_conflicts(plan)
    return plan


def staged_path(plan: RenamePlan, target: str) -> Path:
    """改目录名时先在原目录里改文件名，再改目录名；这里给出中间那一步的路径。"""
    if plan.folder_from and plan.folder_to:
        return Path(plan.folder_from) / Path(target).relative_to(plan.folder_to)
    return Path(target)


def _check_conflicts(plan: RenamePlan) -> None:
    if plan.folder_from and plan.folder_to:
        target = Path(plan.folder_to)
        if target.exists() and not _same(target, Path(plan.folder_from)):
            plan.problems.append(f"目标目录已存在：{target}")
    sources = {m.source.casefold() for m in plan.moves}
    seen: set[str] = set()
    for move in plan.moves:
        if move.target.casefold() in seen:
            plan.problems.append(f"两个文件会重名：{move.target}")
        seen.add(move.target.casefold())
        staged = staged_path(plan, move.target)
        if staged.exists() and str(staged).casefold() not in sources:
            plan.problems.append(f"目标文件已存在：{move.target}")
