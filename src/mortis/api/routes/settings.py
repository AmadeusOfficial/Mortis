from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from mortis.api.deps import ConfigStoreDep, ServicesDep, SessionDep
from mortis.config.models import (
    SECRET_KEYS,
    AppConfig,
    FilesConfig,
    MovieScrapeConfig,
    NetworkConfig,
    PlaybackConfig,
    PluginTypeConfig,
    Secrets,
    TasksConfig,
    TvScrapeConfig,
    secret_value,
    set_secret,
    validation_problems,
)
from mortis.db.models import DataType
from mortis.errors import UserError
from mortis.net.http import build_client
from mortis.plugins.player import video_player_problem
from mortis.sources.tmdb.check import TmdbCheck, check_tmdb_key

router = APIRouter(prefix="/api")


class SecretsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tmdb_api_key: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None


# 内置类型的节用各自的模型，其余都是插件类型。
TYPE_SECTIONS: dict[str, type[BaseModel]] = {
    "movie": MovieScrapeConfig,
    "tv": TvScrapeConfig,
    "anime": TvScrapeConfig,
}


def _status(secrets: Secrets) -> dict[str, bool]:
    return {key: bool(secret_value(secrets, key)) for key in SECRET_KEYS}


@router.get("/settings", response_model=AppConfig)
def read_settings(store: ConfigStoreDep) -> AppConfig:
    return store.load_config()


@router.put("/settings/network", response_model=AppConfig)
def write_network(section: NetworkConfig, store: ConfigStoreDep) -> AppConfig:
    return store.save_section("network", section)


@router.put("/settings/files", response_model=AppConfig)
def write_files(section: FilesConfig, store: ConfigStoreDep) -> AppConfig:
    return store.save_section("files", section)


@router.put("/settings/tasks", response_model=AppConfig)
def write_tasks(section: TasksConfig, store: ConfigStoreDep) -> AppConfig:
    return store.save_section("tasks", section)


@router.put("/settings/playback", response_model=AppConfig)
def write_playback(
    section: PlaybackConfig, store: ConfigStoreDep, services: ServicesDep
) -> AppConfig:
    """留空表示自动选；选了的插件必须存在、已启用并且能播放视频。"""
    if section.player:
        problem = video_player_problem(services.plugins, section.player)
        if problem is not None:
            raise UserError(problem[0], status_code=400)
    return store.save_section("playback", section)


@router.put("/settings/types/{type_id}", response_model=AppConfig)
def write_type(
    type_id: str, body: dict[str, Any], session: SessionDep, store: ConfigStoreDep
) -> AppConfig:
    """一整个类型的设置。类型不存在时 404，设置不合法时 422。"""
    if session.get(DataType, type_id) is None:
        raise UserError(f"没有这种数据类型：{type_id}", status_code=404)
    model = TYPE_SECTIONS.get(type_id, PluginTypeConfig)
    try:
        section = model.model_validate(body)
    except ValidationError as error:
        raise UserError(f"设置不合法：{validation_problems(error)}") from None
    return store.save_section(f"types.{type_id}", section)


@router.get("/secrets")
def read_secrets(store: ConfigStoreDep) -> dict[str, bool]:
    return _status(store.load_secrets())


@router.put("/secrets")
def write_secrets(update: SecretsUpdate, store: ConfigStoreDep) -> dict[str, bool]:
    secrets = store.load_secrets()
    for key in update.model_fields_set:
        set_secret(secrets, key, (getattr(update, key) or "").strip())
    store.save_secrets(secrets)
    return _status(secrets)


@router.post("/settings/check-tmdb", response_model=TmdbCheck)
def check_tmdb(request: Request, store: ConfigStoreDep) -> TmdbCheck:
    config = store.load_config()
    secrets = store.load_secrets()
    with build_client(
        config.network, secrets, transport=request.app.state.http_transport
    ) as client:
        return check_tmdb_key(client, secrets.tmdb_api_key)
