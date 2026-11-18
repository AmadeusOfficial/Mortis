import os
import stat
from pathlib import Path

from fastapi import APIRouter, Response
from pydantic import BaseModel

from mortis.api.deps import ServicesDep, SessionDep
from mortis.db.models import MediaFile
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


@router.post("/media-files/{file_id}/reveal", status_code=204)
def reveal_media_file(file_id: int, session: SessionDep, services: ServicesDep) -> Response:
    """在资源管理器里选中文件；文件没了时打开它原来所在的目录。"""
    media_file = session.get(MediaFile, file_id)
    if media_file is None:
        raise UserError("没有这个文件", status_code=404)
    path = Path(media_file.path)
    try:
        if path.exists():
            services.launcher.reveal(str(path), select=True)
        elif path.parent.is_dir():
            services.launcher.reveal(str(path.parent), select=False)
        else:
            raise UserError(f"文件和所在目录都不在了：{path}", status_code=404)
    except OSError as error:
        raise UserError(f"打不开资源管理器：{error.strerror or error}", status_code=502) from None
    return Response(status_code=204)
