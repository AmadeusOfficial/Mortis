from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from mortis.api.deps import ConfigStoreDep
from mortis.config.models import SECRET_KEYS, AppConfig, Secrets
from mortis.net.http import build_client
from mortis.sources.tmdb.check import TmdbCheck, check_tmdb_key

router = APIRouter(prefix="/api")


class SecretsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tmdb_api_key: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None


def _status(secrets: Secrets) -> dict[str, bool]:
    return {key: bool(getattr(secrets, key)) for key in SECRET_KEYS}


@router.get("/settings", response_model=AppConfig)
def read_settings(store: ConfigStoreDep) -> AppConfig:
    return store.load_config()


@router.put("/settings", response_model=AppConfig)
def write_settings(config: AppConfig, store: ConfigStoreDep) -> AppConfig:
    store.save_config(config)
    return config


@router.get("/secrets")
def read_secrets(store: ConfigStoreDep) -> dict[str, bool]:
    return _status(store.load_secrets())


@router.put("/secrets")
def write_secrets(update: SecretsUpdate, store: ConfigStoreDep) -> dict[str, bool]:
    values = store.load_secrets().model_dump()
    for key in update.model_fields_set:
        values[key] = (getattr(update, key) or "").strip()
    secrets = Secrets.model_validate(values)
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
