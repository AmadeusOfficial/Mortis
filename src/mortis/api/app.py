from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from mortis.settings import Settings
from mortis.version import get_version

PRODUCT_NAME = "莫提斯"


class Health(BaseModel):
    status: str


class SystemInfo(BaseModel):
    name: str
    version: str
    data_dir: str


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else Settings()
    app = FastAPI(title=PRODUCT_NAME, version=get_version())

    @app.get("/api/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok")

    @app.get("/api/system/info", response_model=SystemInfo)
    def system_info() -> SystemInfo:
        return SystemInfo(name=PRODUCT_NAME, version=get_version(), data_dir=str(resolved.data_dir))

    if resolved.web_dist is not None:
        _mount_web(app, resolved.web_dist)
    return app


def _mount_web(app: FastAPI, dist: Path) -> None:
    """单页应用：存在的文件原样返回，其余路径交给前端路由。必须在所有 API 路由之后注册。"""
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"前端产物目录里没有 index.html：{root}")

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (root / path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
