from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.api.deps import RunnerDep, ServicesDep, SessionDep
from mortis.db.models import Task
from mortis.errors import UserError
from mortis.plugin_api import SourceError
from mortis.plugins.host import open_host
from mortis.sources import imdb
from mortis.sources.builtin import source_status
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
