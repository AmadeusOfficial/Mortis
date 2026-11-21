"""清理库目录里的空文件夹：整个子树一个文件都没有的目录，删掉。

只删目录，不删任何文件。以 . 开头的顶层目录（如 .deletedByTMM）、下载工具的临时目录
（如 @DownloadStationTempFiles）默认跳过，它们不是整理留下的。
"""

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from mortis import libraries
from mortis.api.deps import SessionDep
from mortis.errors import UserError

router = APIRouter(prefix="/api/libraries")

# 顶层这些目录整棵跳过：隐藏目录是工具自己的（回收站、缓存），@ 开头的是下载站的临时目录。
SKIP_PREFIXES = (".", "@")


class CleanupError(UserError):
    pass


@dataclass
class EmptyFolder:
    path: Path
    """这个空目录下原本还有多少个空子目录（含自己）。"""
    nested: int


class EmptyFolderOut(BaseModel):
    path: str
    nested: int


class EmptyFolderList(BaseModel):
    folders: list[EmptyFolderOut]
    total: int
    """整棵树里一个文件都没有的目录总数，含预览里没列出的子目录。"""


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 只删这些目录（以及它们下面的空目录）；为空时删整个库里找到的所有空目录。
    paths: list[str] = []


class CleanupResult(BaseModel):
    removed: int
    failed: list[str]


def _is_link(path: Path) -> bool:
    return path.is_symlink() or os.path.islink(path)


def _hollow(path: Path) -> list[Path] | None:
    """子树里一个文件都没有时，返回要删的目录（由深到浅）；否则 None。"""
    if _is_link(path):
        return None
    dirs: list[Path] = []
    try:
        entries = list(path.iterdir())
    except OSError:
        return None
    for entry in entries:
        if entry.is_dir() and not _is_link(entry):
            below = _hollow(entry)
            if below is None:
                return None
            dirs.extend(below)
        elif entry.is_file() or _is_link(entry):
            return None
    dirs.append(path)
    return dirs


def scan(roots: list[str], skip_names: set[str] | None = None) -> list[Path]:
    """列出要删的空目录，由深到浅排序：同一个子树里子目录排在父目录前面。"""
    found: list[Path] = []
    skip = skip_names or set()
    for raw in roots:
        root = Path(raw)
        if not root.is_dir() or _is_link(root):
            continue
        for entry in root.iterdir():
            if not entry.is_dir() or _is_link(entry):
                continue
            if entry.name.startswith(SKIP_PREFIXES) or entry.name in skip:
                continue
            below = _hollow(entry)
            if below is not None:
                found.extend(below)
    return found


def library_roots(session: Session, library_id: int) -> list[str]:
    library = libraries.get_library(session, library_id)
    return [row.path for row in library.paths]


def preview(session: Session, library_id: int, wanted: list[str] | None = None) -> EmptyFolderList:
    folders = scan(library_roots(session, library_id))
    if wanted:
        keep = [str(f) for f in folders if any(_same_or_inside(f, w) for w in wanted)]
        folders = [Path(p) for p in keep]
    # 只列出最上层的空目录，子目录用 nested 表示，界面上不用铺开。
    tops: list[EmptyFolder] = []
    listed: set[str] = set()
    for folder in sorted(folders, key=lambda p: len(p.parts)):
        if any(_inside(folder, Path(parent)) for parent in listed):
            continue
        nested = sum(1 for other in folders if other == folder or _inside(other, folder))
        tops.append(EmptyFolder(folder, nested))
        listed.add(str(folder))
    return EmptyFolderList(
        folders=[EmptyFolderOut(path=str(f.path), nested=f.nested) for f in tops],
        total=len(folders),
    )


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _inside(child: Path, parent: Path) -> bool:
    return _key(child) != _key(parent) and _key(child).startswith(_key(parent) + os.sep)


def _same_or_inside(child: Path, parent: str) -> bool:
    return _key(child) == _key(parent) or _key(child).startswith(_key(parent) + os.sep)


def remove(session: Session, library_id: int, wanted: list[str]) -> CleanupResult:
    roots = library_roots(session, library_id)
    folders = scan(roots)
    if wanted:
        folders = [f for f in folders if any(_same_or_inside(f, w) for w in wanted)]
    removed = 0
    failed: list[str] = []
    for folder in sorted(folders, key=lambda p: -len(p.parts)):
        try:
            folder.rmdir()
            removed += 1
        except OSError:
            # 期间有文件写进来就删不掉，跳过它。
            failed.append(str(folder))
    return CleanupResult(removed=removed, failed=failed)


@router.get("/{library_id}/empty-folders", response_model=EmptyFolderList)
def list_empty_folders(library_id: int, session: SessionDep) -> EmptyFolderList:
    """预览要删的空文件夹。只读，不改磁盘。"""
    return preview(session, library_id)


@router.post("/{library_id}/empty-folders", response_model=CleanupResult)
def delete_empty_folders(
    library_id: int, body: CleanupRequest, session: SessionDep
) -> CleanupResult:
    """删掉空文件夹。只删整个子树没有文件的目录，不碰任何文件。"""
    return remove(session, library_id, body.paths)
