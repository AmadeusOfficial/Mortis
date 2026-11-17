import os
import stat
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from mortis.errors import UserError

router = APIRouter(prefix="/api")


class DirEntry(BaseModel):
    name: str
    path: str


class DirListing(BaseModel):
    path: str
    parent: str | None
    """空字符串表示回到盘符列表；null 表示已经在盘符列表。"""
    dirs: list[DirEntry]
    files: list[DirEntry] = []
    """只有请求带 files=true 时才列。"""


def _roots() -> list[DirEntry]:
    if hasattr(os, "listdrives"):
        return [DirEntry(name=drive, path=drive) for drive in os.listdrives()]
    return [DirEntry(name="/", path="/")]


def _hidden(entry: os.DirEntry[str]) -> bool:
    if entry.name.startswith((".", "$")):
        return True
    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


def _parent(path: Path) -> str:
    parent = path.parent
    return "" if parent == path else str(parent)


@router.get("/files/dirs", response_model=DirListing)
def list_dirs(path: str = "", files: bool = False) -> DirListing:
    """同步路由由 FastAPI 放进线程池跑，网络盘慢时不卡别的请求。"""
    if not path.strip():
        return DirListing(path="", parent=None, dirs=_roots())
    folder = Path(path.strip())
    if not folder.is_absolute():
        raise UserError("请填完整路径，例如 D:\\ 或 \\\\server\\share")
    if not folder.is_dir():
        raise UserError("目录不存在", status_code=404)
    dirs: list[DirEntry] = []
    found: list[DirEntry] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if _hidden(entry):
                        continue
                    if entry.is_dir():
                        dirs.append(DirEntry(name=entry.name, path=entry.path))
                    elif files and entry.is_file():
                        found.append(DirEntry(name=entry.name, path=entry.path))
                except OSError:
                    continue
    except PermissionError:
        raise UserError("没有权限读取这个目录", status_code=403) from None
    except OSError as error:
        raise UserError(f"读取目录失败：{error.strerror or error}") from None
    dirs.sort(key=lambda entry: entry.name.casefold())
    found.sort(key=lambda entry: entry.name.casefold())
    return DirListing(path=str(folder), parent=_parent(folder), dirs=dirs, files=found)
