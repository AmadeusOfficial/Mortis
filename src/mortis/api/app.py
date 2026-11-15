import posixpath
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PureWindowsPath

import httpx2 as httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mortis.api.routes import data_types as data_type_routes
from mortis.api.routes import libraries as library_routes
from mortis.api.routes import settings as settings_routes
from mortis.catalog.install import ensure_builtin_types
from mortis.config.store import ConfigError, ConfigStore
from mortis.db.engine import create_db_engine, make_session_factory
from mortis.db.migrate import upgrade_database
from mortis.errors import UserError
from mortis.settings import Settings
from mortis.version import get_version

PRODUCT_NAME = "莫提斯"
DB_FILE = "mortis.db"


class Health(BaseModel):
    status: str


class SystemInfo(BaseModel):
    name: str
    version: str
    data_dir: str


def create_app(
    settings: Settings | None = None,
    *,
    http_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    resolved = settings if settings is not None else Settings()
    resolved.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = resolved.data_dir / DB_FILE
    upgrade_database(db_path)
    engine = create_db_engine(db_path)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        ensure_builtin_types(session)
        session.commit()
    # 启动安装用过的连接不留在池里；没进 lifespan 的调用方（只读静态页的测试）也不会漏连接。
    engine.dispose()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title=PRODUCT_NAME, version=get_version(), lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts(resolved.host))
    app.state.settings = resolved
    app.state.session_factory = session_factory
    app.state.config_store = ConfigStore(resolved.data_dir)
    app.state.http_transport = http_transport

    @app.exception_handler(UserError)
    async def _user_error(_request: Request, error: UserError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content={"detail": error.message})

    @app.exception_handler(ConfigError)
    async def _config_error(_request: Request, error: ConfigError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(error)})

    @app.get("/api/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok")

    @app.get("/api/system/info", response_model=SystemInfo)
    def system_info() -> SystemInfo:
        return SystemInfo(name=PRODUCT_NAME, version=get_version(), data_dir=str(resolved.data_dir))

    app.include_router(settings_routes.router)
    app.include_router(data_type_routes.router)
    app.include_router(library_routes.router)

    if resolved.web_dist is not None:
        _mount_web(app, resolved.web_dist)
    return app


def _allowed_hosts(configured: str) -> list[str]:
    """只认回环名和配置的监听地址，挡住 DNS 重绑定；通配监听地址不算。"""
    hosts = ["127.0.0.1", "localhost"]
    if configured not in {"0.0.0.0", "::"} and configured not in hosts:
        hosts.append(configured)
    return hosts


def _mount_web(app: FastAPI, dist: Path) -> None:
    """单页应用：存在的文件原样返回，其余路径交给前端路由。必须在所有 API 路由之后注册。"""
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"前端产物目录里没有 index.html：{root}")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        relative = _relative_web_path(path)
        if relative is None:
            return FileResponse(index)
        try:
            candidate = (root / relative).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
        except OSError, ValueError:
            pass
        return FileResponse(index)


def _relative_web_path(path: str) -> str | None:
    """纯字符串判断，不碰磁盘：带盘符、根或 UNC 的路径，以及规范化后跳出目录的路径都返回 None。

    拼上绝对路径再 resolve 会去访问别的盘或外部 SMB 共享。
    """
    unified = path.replace("\\", "/")
    if PureWindowsPath(unified).anchor or ":" in unified:
        return None
    normalized = posixpath.normpath(unified)
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized
