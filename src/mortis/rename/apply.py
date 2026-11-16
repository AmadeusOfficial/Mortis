"""照重命名计划改磁盘上的名字。中途失败就按相反顺序改回去。"""

import os
from pathlib import Path

from sqlalchemy.orm import Session

from mortis.db.models import MediaFile, utc_now
from mortis.errors import UserError
from mortis.rename.plan import RenamePlan, staged_path


class RenameError(UserError):
    pass


def _move(source: Path, target: Path, done: list[tuple[Path, Path]]) -> None:
    if str(source) == str(target):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, target)
    done.append((source, target))


def _rollback(done: list[tuple[Path, Path]], made: Path | None) -> None:
    for source, target in reversed(done):
        try:
            os.rename(target, source)
        except OSError:
            pass
    if made is not None:
        for folder in sorted(made.rglob("*"), reverse=True) + [made]:
            try:
                folder.rmdir()
            except OSError:
                pass


def apply_rename(session: Session, plan: RenamePlan) -> None:
    """只 flush，调用方提交。"""
    if plan.problems:
        raise RenameError("；".join(plan.problems))
    done: list[tuple[Path, Path]] = []
    made: Path | None = None
    try:
        if plan.make_folder and plan.folder_to:
            target = Path(plan.folder_to)
            if not target.exists():
                target.mkdir(parents=True)
                made = target
        for move in plan.moves:
            _move(Path(move.source), staged_path(plan, move.target), done)
        if plan.folder_from and plan.folder_to:
            _move(Path(plan.folder_from), Path(plan.folder_to), done)
    except OSError as error:
        _rollback(done, made)
        raise RenameError(f"重命名失败，已改回原样：{error.strerror or error}") from None
    for folder in plan.empty_dirs:
        try:
            Path(folder).rmdir()
        except OSError:
            pass
    now = utc_now()
    for move in plan.moves:
        row = session.get(MediaFile, move.file_id)
        if row is not None and row.path != move.target:
            row.path, row.updated_at = move.target, now
    session.flush()
