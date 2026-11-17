from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.api.deps import RunnerDep, ServicesDep, SessionDep
from mortis.config.models import AppConfig, validation_problems
from mortis.db.models import Task
from mortis.errors import UserError
from mortis.plugin_api import SourceError
from mortis.plugins.host import open_host
from mortis.services import Services
from mortis.sources import imdb
from mortis.sources.builtin import BY_ID, SOURCES, BuiltinSource, source_status
from mortis.tasks import repo as task_repo

router = APIRouter(prefix="/api/sources")


class SourceOut(BaseModel):
    id: str
    name: str
    data_type: str
    builtin: bool = False
    available: bool = True
    reason: str | None = None
    """不可用的原因。内置来源不走 /search，搜索 TMDB 用 /api/tmdb。"""


class HitOut(BaseModel):
    external_id: str
    title: str
    original_title: str | None
    year: int | None
    overview: str | None
    poster_url: str | None


@router.get("", response_model=list[SourceOut])
def list_sources(services: ServicesDep, data_type: str = Query(min_length=1)) -> list[SourceOut]:
    builtin = [
        SourceOut(
            id=source.id,
            name=source.name,
            data_type=data_type,
            builtin=True,
            available=reason is None,
            reason=reason,
        )
        for source, reason in source_status(services, data_type)
    ]
    return builtin + [
        SourceOut(id=plugin.id, name=plugin.name, data_type=plugin.data_type)
        for plugin in services.plugins.sources(data_type)
    ]


class BuiltinSourceOut(BaseModel):
    id: str
    name: str
    data_types: list[str]
    available: bool
    reason: str | None
    settings: dict[str, Any]
    secrets: dict[str, bool]
    """每个密钥是否已填，不含原文。"""
    provider: str | None = None


class BuiltinSourceUpdate(BaseModel):
    """settings 只改给出的键；secrets 里不出现的键不改，空字符串表示清除。"""

    model_config = ConfigDict(extra="forbid")

    settings: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None


def _configurable(source_id: str) -> BuiltinSource:
    source = BY_ID.get(source_id)
    if source is None or source.settings_model is None or source.secrets_model is None:
        raise UserError("没有这个内置来源，或它没有单独的设置", status_code=404)
    return source


def _validated[M: BaseModel](model: type[M], values: dict[str, Any]) -> M:
    try:
        return model.model_validate(values)
    except ValidationError as error:
        raise UserError(f"设置不合法：{validation_problems(error)}") from None


def _current_settings(source: BuiltinSource, config: AppConfig) -> BaseModel:
    """设置文件里手改坏的值按默认值算，和来源模块读设置时一样。"""
    assert source.settings_model is not None
    try:
        return source.settings_model.model_validate(config.sources.get(source.id, {}))
    except ValidationError:
        return source.settings_model()


def _builtin_out(services: Services, source: BuiltinSource) -> BuiltinSourceOut:
    assert source.secrets_model is not None
    store = services.config_store
    config = store.load_config()
    secrets = store.load_secrets()
    settings = _current_settings(source, config)
    stored = secrets.sources.get(source.id, {})
    reason = source.disabled_reason(config, secrets, services.data_dir)
    return BuiltinSourceOut(
        id=source.id,
        name=source.name,
        data_types=sorted(source.data_types),
        available=reason is None,
        reason=reason,
        settings=settings.model_dump(mode="json"),
        secrets={
            key: isinstance(stored.get(key), str) and bool(stored[key].strip())
            for key in source.secrets_model.model_fields
        },
        provider=source.provider(secrets) if source.provider is not None else None,
    )


@router.get("/builtin", response_model=list[BuiltinSourceOut])
def list_builtin_sources(services: ServicesDep) -> list[BuiltinSourceOut]:
    """有单独设置的内置来源；可用状态不分数据类型。"""
    return [
        _builtin_out(services, source)
        for source in SOURCES
        if source.settings_model is not None and source.secrets_model is not None
    ]


@router.get("/builtin/{source_id}", response_model=BuiltinSourceOut)
def read_builtin_source(source_id: str, services: ServicesDep) -> BuiltinSourceOut:
    return _builtin_out(services, _configurable(source_id))


@router.put("/builtin/{source_id}", response_model=BuiltinSourceOut)
def update_builtin_source(
    source_id: str, update: BuiltinSourceUpdate, services: ServicesDep
) -> BuiltinSourceOut:
    source = _configurable(source_id)
    assert source.settings_model is not None and source.secrets_model is not None
    store = services.config_store
    changes: dict[str, str] = {}
    if update.secrets is not None:
        unknown = sorted(set(update.secrets) - set(source.secrets_model.model_fields))
        if unknown:
            raise UserError(f"没有这些密钥：{'、'.join(unknown)}")
        changes = {key: value.strip() for key, value in update.secrets.items()}
    values: dict[str, Any] | None = None
    if update.settings is not None:
        unknown = sorted(set(update.settings) - set(source.settings_model.model_fields))
        if unknown:
            raise UserError(f"没有这些设置：{'、'.join(unknown)}")
        current = _current_settings(source, store.load_config()).model_dump(mode="json")
        values = _validated(source.settings_model, {**current, **update.settings}).model_dump(
            mode="json"
        )
    # 两边都先校验完再写，免得只写进去一半。
    if values is not None:
        store.save_plugin(source.id, values=values)
    if changes:
        store.update_plugin_secrets(source.id, changes)
    return _builtin_out(services, source)


class ImdbDatasetOut(BaseModel):
    imported_at: datetime | None
    count: int | None
    stale: bool
    task_id: int | None
    """正在排队或运行的更新任务。"""


class Enqueued(BaseModel):
    task_id: int


def _active_refresh(session: Session) -> int | None:
    return session.scalar(
        select(Task.id).where(
            Task.task_type == imdb.REFRESH_TASK,
            Task.status.in_((task_repo.QUEUED, task_repo.RUNNING)),
        )
    )


@router.get("/imdb/dataset", response_model=ImdbDatasetOut)
def imdb_dataset(services: ServicesDep, session: SessionDep) -> ImdbDatasetOut:
    settings = imdb.settings_of(services.config_store.load_config())
    info = imdb.dataset_info(services.data_dir)
    return ImdbDatasetOut(
        imported_at=info.imported_at if info else None,
        count=info.count if info else None,
        stale=imdb.is_stale(services.data_dir, settings.refresh_days),
        task_id=_active_refresh(session),
    )


@router.post("/imdb/dataset/refresh", response_model=Enqueued)
def refresh_imdb_dataset(session: SessionDep, runner: RunnerDep) -> Enqueued:
    task = imdb.queue_refresh(session)
    session.commit()
    runner.wake()
    return Enqueued(task_id=task.id)


@router.get("/{source_id}/search", response_model=list[HitOut])
def search_source(
    source_id: str, services: ServicesDep, query: str = Query(min_length=1, max_length=200)
) -> list[HitOut]:
    plugin = services.plugins.source(source_id)
    if plugin is None:
        raise UserError("来源插件不存在或没有启用", status_code=404)
    cleaned = query.strip()
    if not cleaned:
        raise UserError("搜索词不能为空")
    with open_host(services, plugin, lambda _message: None) as host:
        if not plugin.enabled(host):
            raise UserError(f"{plugin.name} 没有启用，先在设置页填好它需要的设置")
        try:
            hits = plugin.search(host, cleaned)
        except SourceError as error:
            raise UserError(error.message, status_code=502) from None
    return [
        HitOut(
            external_id=hit.external_id,
            title=hit.title,
            original_title=hit.original_title,
            year=hit.year,
            overview=hit.overview,
            poster_url=hit.poster_url,
        )
        for hit in hits
    ]
