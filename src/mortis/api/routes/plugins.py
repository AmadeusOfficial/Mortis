from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from mortis.api.deps import ServicesDep
from mortis.config.models import validation_problems
from mortis.errors import UserError
from mortis.plugin_api import Account, PlayerPlugin, SourceAuthError, SourceError, SourcePlugin
from mortis.plugins.host import (
    EXECUTABLES,
    PluginHost,
    open_host,
    plugin_secrets,
    plugin_settings,
)
from mortis.plugins.oauth import OAuthStates, authorize_url, client_values, exchange_code
from mortis.plugins.player import player_host, unavailable_reason
from mortis.plugins.registry import PluginInfo
from mortis.services import Services

router = APIRouter(prefix="/api")
_states = OAuthStates()
TOKEN_KEYS = frozenset({"refresh_token", "expires_at", "redirect_uri"})
"""宿主登录时写的键，设置页不显示也不能改。"""
PLAYER_HOST_KEYS = frozenset({EXECUTABLES})
"""播放插件设置里由宿主写的键，设置页不显示，保存设置时保留原值。"""


class PluginOut(BaseModel):
    id: str
    name: str
    version: str
    kind: str
    origin: str
    enabled: bool
    error: str | None
    parent: str | None = None
    data_type: str | None = None
    settings_schema: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    secrets_schema: dict[str, Any] | None = None
    secrets: dict[str, bool] | None = None
    oauth: bool = False
    # OAuth 应用的 App ID、App Secret 存在哪两个密钥键里。
    oauth_client_keys: list[str] = []
    oauth_register_url: str = ""
    oauth_token_page_url: str = ""
    logged_in: bool = False
    expires_at: int | None = None
    notice: str | None = None
    # 只有播放插件有：能不能用，以及实际会启动的程序路径。
    available: bool | None = None
    detected_path: str | None = None
    file_kinds: list[str] | None = None


class AccountOut(BaseModel):
    account: Account | None = None
    error: str | None = None


class PluginUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    settings: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None


def _secret_keys(plugin: SourcePlugin) -> list[str]:
    return [key for key in plugin.Secrets.model_fields if key not in TOKEN_KEYS]


def _out(services: Services, info: PluginInfo) -> PluginOut:
    out = PluginOut(
        id=info.id,
        name=info.name,
        version=info.version,
        kind=info.kind,
        origin=info.origin,
        enabled=info.enabled,
        error=info.error,
        parent=info.parent,
    )
    plugin = info.plugin
    if isinstance(plugin, PlayerPlugin):
        return _player_out(services, plugin, out)
    if not isinstance(plugin, SourcePlugin):
        return out
    store = services.config_store
    secrets = store.load_secrets()
    values = secrets.sources.get(plugin.id, {})
    keys = _secret_keys(plugin)
    schema = plugin.Secrets.model_json_schema()
    schema["properties"] = {
        key: value for key, value in schema.get("properties", {}).items() if key in keys
    }
    out.data_type = plugin.data_type
    out.settings_schema = plugin.Settings.model_json_schema()
    out.settings = plugin_settings(store.load_config(), plugin).model_dump(mode="json")
    out.secrets_schema = schema
    out.secrets = {key: bool(getattr(plugin_secrets(secrets, plugin), key)) for key in keys}
    out.oauth = plugin.oauth is not None
    if plugin.oauth is not None:
        out.oauth_client_keys = [plugin.oauth.client_id_key, plugin.oauth.client_secret_key]
        out.oauth_register_url = plugin.oauth.register_url
        out.oauth_token_page_url = plugin.oauth.token_page_url
    out.logged_in = bool(values.get("access_token"))
    out.expires_at = int(values["expires_at"]) if values.get("expires_at") else None
    with services.http_client() as client:
        host = PluginHost(plugin, client, store, lambda _message: None)
        out.notice = plugin.disabled_reason(host)
    return out


def _player_out(services: Services, plugin: PlayerPlugin, out: PluginOut) -> PluginOut:
    host = player_host(plugin, services.launcher, services.config_store)
    schema = plugin.Settings.model_json_schema()
    schema["properties"] = {
        key: value
        for key, value in schema.get("properties", {}).items()
        if key not in PLAYER_HOST_KEYS
    }
    if "required" in schema:
        schema["required"] = [key for key in schema["required"] if key not in PLAYER_HOST_KEYS]
    out.settings_schema = schema
    out.settings = {
        key: value
        for key, value in host.settings.model_dump(mode="json").items()
        if key not in PLAYER_HOST_KEYS
    }
    out.file_kinds = list(plugin.file_kinds)
    out.notice = unavailable_reason(plugin, host)
    out.available = out.notice is None
    try:
        out.detected_path = plugin.detected_path(host)
    except Exception:
        out.detected_path = None
    return out


def _reload(services: Services, request: Request) -> None:
    services.plugins.load(services.config_store.load_config().plugins.disabled)
    with services.locks.hold("catalog-write"), request.app.state.session_factory() as session:
        services.plugins.sync_catalog(session)
        session.commit()


def _source(services: Services, plugin_id: str) -> SourcePlugin:
    plugin = services.plugins.source(plugin_id)
    if plugin is None:
        raise UserError("插件不存在或没有启用", status_code=404)
    return plugin


@router.get("/plugins", response_model=list[PluginOut])
def list_plugins(services: ServicesDep) -> list[PluginOut]:
    return [_out(services, info) for info in services.plugins.all()]


@router.post("/plugins/reload", response_model=list[PluginOut])
def reload_plugins(services: ServicesDep, request: Request) -> list[PluginOut]:
    _reload(services, request)
    return list_plugins(services)


@router.put("/plugins/{plugin_id}", response_model=PluginOut)
def update_plugin(
    plugin_id: str, update: PluginUpdate, services: ServicesDep, request: Request
) -> PluginOut:
    info = services.plugins.get(plugin_id)
    if info is None:
        raise UserError("插件不存在", status_code=404)
    store = services.config_store
    plugin = info.plugin
    if update.settings is not None and not isinstance(plugin, SourcePlugin | PlayerPlugin):
        raise UserError("这个插件没有可改的设置")
    if update.secrets is not None and not isinstance(plugin, SourcePlugin):
        raise UserError("这个插件没有密钥")
    if update.settings is not None:
        assert isinstance(plugin, SourcePlugin | PlayerPlugin)
        submitted = update.settings
        if isinstance(plugin, PlayerPlugin):
            submitted = {k: v for k, v in submitted.items() if k not in PLAYER_HOST_KEYS}
        try:
            values = plugin.Settings.model_validate(submitted).model_dump(mode="json")
        except ValidationError as error:
            raise UserError(f"设置不合法：{validation_problems(error)}") from None
        if isinstance(plugin, PlayerPlugin):
            kept = PLAYER_HOST_KEYS & set(values)

            def keep_host_keys(current: dict[str, Any]) -> dict[str, Any]:
                mine = {k: v for k, v in values.items() if k not in kept}
                return {**mine, **{k: current[k] for k in kept if k in current}}

            store.update_plugin_settings(plugin_id, keep_host_keys)
        else:
            store.save_plugin(plugin_id, values=values)
    if update.secrets is not None:
        assert isinstance(plugin, SourcePlugin)
        allowed = set(_secret_keys(plugin))
        unknown = sorted(set(update.secrets) - allowed)
        if unknown:
            raise UserError(f"没有这些密钥：{'、'.join(unknown)}")
        changes: dict[str, Any] = {key: value.strip() for key, value in update.secrets.items()}
        if "access_token" in changes and plugin.oauth is not None:
            # 手填的令牌没有续期信息，旧的续期令牌和到期时间不能留着去覆盖它。
            changes.update({"refresh_token": "", "expires_at": 0})
        store.update_plugin_secrets(plugin_id, changes)
    if update.enabled is not None and update.enabled != info.enabled:
        store.save_plugin(plugin_id, disabled=not update.enabled)
        _reload(services, request)
    refreshed = services.plugins.get(plugin_id)
    assert refreshed is not None
    return _out(services, refreshed)


@router.get("/plugins/{plugin_id}/oauth/login")
def oauth_login(plugin_id: str, services: ServicesDep, request: Request) -> RedirectResponse:
    plugin = _source(services, plugin_id)
    if plugin.oauth is None:
        raise UserError(f"{plugin.name} 不需要登录")
    values = services.config_store.load_secrets().sources.get(plugin.id, {})
    client_id, client_secret = client_values(plugin, values)
    if not client_id or not client_secret:
        raise UserError(f"先在设置页填 {plugin.name} 的 App ID 和 App Secret")
    redirect_uri = str(request.url_for("oauth_callback", plugin_id=plugin_id))
    state = _states.issue(plugin_id, redirect_uri)
    return RedirectResponse(authorize_url(plugin, client_id, redirect_uri, state))


def _back(plugin_id: str, reason: str | None = None) -> RedirectResponse:
    """授权页跳回来时浏览器是整页导航，失败也跳回设置页，由页面显示原因。"""
    query = {"plugin": plugin_id, "login": "ok" if reason is None else "failed"}
    if reason is not None:
        query["reason"] = reason
    return RedirectResponse(f"/settings?{urlencode(query)}")


@router.get("/plugins/{plugin_id}/oauth/callback", name="oauth_callback")
def oauth_callback(
    plugin_id: str,
    services: ServicesDep,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> RedirectResponse:
    plugin = _source(services, plugin_id)
    redirect_uri = _states.take(plugin_id, state)
    if redirect_uri is None:
        return _back(plugin_id, "登录已过期，请重新点登录")
    if error or not code:
        return _back(plugin_id, "在授权页取消了授权" if error else "授权页没有返回授权码")
    with open_host(services, plugin, lambda _message: None) as host:
        try:
            exchange_code(host, code, redirect_uri)
        except SourceError as failure:
            return _back(plugin_id, f"换取令牌失败：{failure.message}")
    return _back(plugin_id)


@router.get("/plugins/{plugin_id}/account", response_model=AccountOut)
def plugin_account(plugin_id: str, services: ServicesDep) -> AccountOut:
    plugin = _source(services, plugin_id)
    with open_host(services, plugin, lambda _message: None) as host:
        try:
            return AccountOut(account=plugin.account(host))
        except SourceAuthError:
            return AccountOut(error="令牌已失效，请重新登录")
        except SourceError as failure:
            return AccountOut(error=failure.message)


@router.delete("/plugins/{plugin_id}/oauth", response_model=PluginOut)
def oauth_logout(plugin_id: str, services: ServicesDep) -> PluginOut:
    plugin = _source(services, plugin_id)
    services.config_store.update_plugin_secrets(
        plugin.id, {"access_token": "", "refresh_token": "", "expires_at": 0}
    )
    info = services.plugins.get(plugin_id)
    assert info is not None
    return _out(services, info)
