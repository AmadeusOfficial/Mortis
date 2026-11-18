import posixpath
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path, PureWindowsPath

import httpx2 as httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mortis.agent.client import make_chat_client
from mortis.agent.loop import ApprovalStore
from mortis.api.routes import agent as agent_routes
from mortis.api.routes import data_types as data_type_routes
from mortis.api.routes import files as file_routes
from mortis.api.routes import images as image_routes
from mortis.api.routes import items as item_routes
from mortis.api.routes import libraries as library_routes
from mortis.api.routes import output as output_routes
from mortis.api.routes import play as play_routes
from mortis.api.routes import plugins as plugin_routes
from mortis.api.routes import schedules as schedule_routes
from mortis.api.routes import settings as settings_routes
from mortis.api.routes import sources as source_routes
from mortis.api.routes import tasks as task_routes
from mortis.api.routes import tmdb as tmdb_routes
from mortis.api.routes import user_tags as user_tag_routes
from mortis.api.routes.agent import ChatClientFactory
from mortis.catalog.install import ensure_builtin_types
from mortis.config.models import AppConfig
from mortis.config.store import ConfigError, ConfigStore
from mortis.db.engine import create_db_engine, make_session_factory
from mortis.db.migrate import upgrade_database
from mortis.errors import UserError
from mortis.files.trash import SystemTrash, Trash
from mortis.organize import watch
from mortis.plugins.launch import Launcher, SystemLauncher
from mortis.plugins.registry import PluginRegistry, plugin_folders
from mortis.services import Services
from mortis.settings import Settings
from mortis.sources import imdb
from mortis.sources.builtin import seed_builtin_orders
from mortis.tasks import repo as task_repo
from mortis.tasks import schedule
from mortis.tasks.registry import builtin_handlers
from mortis.tasks.runner import TaskRunner
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
    start_worker: bool = True,
    launcher: Launcher | None = None,
    trash: Trash | None = None,
    start_automation: bool | None = None,
    watch_debounce: float = watch.DEBOUNCE_SECONDS,
    watch_retry: float = watch.RETRY_SECONDS,
    schedule_interval: float = schedule.CHECK_SECONDS,
    chat_client_factory: ChatClientFactory | None = None,
) -> FastAPI:
    """start_automation 不给时跟着 start_worker：定时检查和目录监听一起开关。"""
    resolved = settings if settings is not None else Settings()
    resolved.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = resolved.data_dir / DB_FILE
    upgrade_database(db_path)
    engine = create_db_engine(db_path)
    session_factory = make_session_factory(engine)
    config_store = ConfigStore(resolved.data_dir)
    startup_config = _startup_config(config_store)
    plugins = PluginRegistry(plugin_folders(resolved))
    plugins.load(startup_config.plugins.disabled)
    with session_factory() as session:
        ensure_builtin_types(session)
        seed_builtin_orders(session)
        plugins.sync_catalog(session)
        imdb.queue_refresh_if_stale(session, startup_config, resolved.data_dir)
        session.commit()
        task_repo.recover_running(session)
        task_repo.prune(
            session, keep_days=startup_config.tasks.keep_days, data_dir=resolved.data_dir
        )
    # 启动安装用过的连接不留在池里；没进 lifespan 的调用方（只读静态页的测试）也不会漏连接。
    engine.dispose()
    services = Services(
        resolved.data_dir,
        config_store,
        http_transport,
        plugins=plugins,
        launcher=launcher if launcher is not None else SystemLauncher(),
        trash=trash if trash is not None else SystemTrash(),
    )
    runner = TaskRunner(
        session_factory,
        services,
        builtin_handlers(),
        concurrency=startup_config.tasks.concurrency,
    )
    scheduler = schedule.Scheduler(session_factory, runner.wake, interval=schedule_interval)
    watcher = watch.WatchManager(
        session_factory, config_store, runner.wake, debounce=watch_debounce, retry=watch_retry
    )
    automation = start_worker if start_automation is None else start_automation

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_worker:
            runner.start()
        if automation:
            scheduler.start()
            watcher.start()
        try:
            yield
        finally:
            watcher.stop()
            scheduler.stop()
            runner.stop()
            engine.dispose()

    app = FastAPI(title=PRODUCT_NAME, version=get_version(), lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts(resolved.host))
    app.state.settings = resolved
    app.state.session_factory = session_factory
    app.state.config_store = config_store
    app.state.http_transport = http_transport
    app.state.services = services
    app.state.task_runner = runner
    app.state.scheduler = scheduler
    app.state.watch_manager = watcher
    app.state.chat_client_factory = (
        chat_client_factory
        if chat_client_factory is not None
        else partial(make_chat_client, transport=http_transport)
    )
    app.state.approvals = ApprovalStore()

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
    app.include_router(schedule_routes.router)
    app.include_router(task_routes.router)
    app.include_router(item_routes.router)
    app.include_router(user_tag_routes.router)
    app.include_router(output_routes.router)
    app.include_router(play_routes.router)
    app.include_router(image_routes.router)
    app.include_router(tmdb_routes.router)
    app.include_router(plugin_routes.router)
    app.include_router(source_routes.router)
    app.include_router(file_routes.router)
    app.include_router(agent_routes.router)

    if resolved.web_dist is not None:
        _mount_web(app, resolved.web_dist)
    return app


def _startup_config(store: ConfigStore) -> AppConfig:
    """设置文件坏了也要能启动，好让用户在设置页看到错误；这时任务设置用默认值。"""
    try:
        return store.load_config()
    except ConfigError:
        return AppConfig()


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
