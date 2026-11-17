"""插件运行时拿到的宿主：设置、密钥、网络请求和 OAuth 令牌。"""

import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx2 as httpx
from pydantic import BaseModel, ValidationError

from mortis.config.models import AppConfig, Secrets
from mortis.config.store import ConfigStore
from mortis.plugin_api import PlayerPlugin, SourceAuthError, SourceError, SourcePlugin
from mortis.services import Services

REFRESH_BEFORE_SECONDS = 24 * 3600
_SENSITIVE = re.compile(r"(token|key|secret|password|code)$", re.IGNORECASE)

_pace_guard = threading.Lock()
_next_slot: dict[str, float] = {}


def plugin_settings(config: AppConfig, plugin: SourcePlugin | PlayerPlugin) -> BaseModel:
    try:
        return plugin.Settings.model_validate(config.sources.get(plugin.id, {}))
    except ValidationError:
        return plugin.Settings()


def plugin_secrets(secrets: Secrets, plugin: SourcePlugin) -> BaseModel:
    try:
        return plugin.Secrets.model_validate(secrets.sources.get(plugin.id, {}))
    except ValidationError:
        return plugin.Secrets()


def mask_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    query = [
        (key, "***" if _SENSITIVE.search(key) else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _pace(plugin: SourcePlugin) -> None:
    """同一来源的请求按 requests_per_second 排队，跨任务共用。"""
    interval = 1.0 / plugin.requests_per_second if plugin.requests_per_second > 0 else 0.0
    with _pace_guard:
        now = time.monotonic()
        slot = max(now, _next_slot.get(plugin.id, now))
        _next_slot[plugin.id] = slot + interval
    if slot > now:
        time.sleep(slot - now)


class PluginHost:
    def __init__(
        self,
        plugin: SourcePlugin,
        client: httpx.Client,
        store: ConfigStore,
        log: Callable[[str], None],
    ) -> None:
        self.plugin = plugin
        self.client = client
        self.store = store
        self._log = log
        self.settings = plugin_settings(store.load_config(), plugin)
        self.secrets = plugin_secrets(store.load_secrets(), plugin)

    def log(self, message: str) -> None:
        self._log(f"[{self.plugin.name}] {message}")

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        _pace(self.plugin)
        params = kwargs.get("params")
        shown = mask_url(f"{url}?{urlencode(params)}" if params else url)
        self.log(f"{method} {shown}")
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise SourceError(f"{self.plugin.name} 请求失败：{type(error).__name__}") from None
        if response.status_code in (401, 403):
            raise SourceAuthError(f"{self.plugin.name} 拒绝了令牌（HTTP {response.status_code}）")
        if response.status_code >= 400:
            raise SourceError(f"{self.plugin.name} 返回 HTTP {response.status_code}")
        return response

    @staticmethod
    def _json(response: httpx.Response, name: str) -> Any:
        try:
            return response.json()
        except json.JSONDecodeError, UnicodeDecodeError:
            raise SourceError(f"{name} 返回的不是 JSON") from None

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._json(self._send("GET", url, params=params, headers=headers), self.plugin.name)

    def post_json(
        self,
        url: str,
        *,
        json: Any = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = self._send("POST", url, json=json, data=data, headers=headers)
        return self._json(response, self.plugin.name)

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        return self._send("GET", url, params=params, headers=headers).text

    def access_token(self) -> str | None:
        values = self.store.load_secrets().sources.get(self.plugin.id, {})
        token = str(values.get("access_token") or "")
        oauth = self.plugin.oauth
        if oauth is None or not values.get("refresh_token"):
            return token or None
        expires_at = int(values.get("expires_at") or 0)
        if expires_at and expires_at - time.time() < REFRESH_BEFORE_SECONDS:
            try:
                token = refresh_token(self, values) or token
            except SourceError as error:
                self.log(f"续期令牌失败：{error.message}")
        return token or None


def save_tokens(store: ConfigStore, plugin_id: str, payload: Any, **extra: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise SourceError("登录没有拿到令牌")
    changes: dict[str, Any] = {"access_token": str(payload["access_token"]), **extra}
    if payload.get("refresh_token"):
        changes["refresh_token"] = str(payload["refresh_token"])
    if payload.get("expires_in"):
        changes["expires_at"] = int(time.time()) + int(payload["expires_in"])
    return store.update_plugin_secrets(plugin_id, changes)


def refresh_token(host: PluginHost, values: dict[str, Any]) -> str | None:
    oauth = host.plugin.oauth
    assert oauth is not None
    payload = host.post_json(
        oauth.token_url,
        data={
            "grant_type": "refresh_token",
            "client_id": str(values.get(oauth.client_id_key) or ""),
            "client_secret": str(values.get(oauth.client_secret_key) or ""),
            "refresh_token": str(values.get("refresh_token") or ""),
            "redirect_uri": str(values.get("redirect_uri") or ""),
        },
    )
    saved = save_tokens(host.store, host.plugin.id, payload)
    host.log("令牌已续期")
    return saved.get("access_token")


@contextmanager
def open_host(
    services: Services, plugin: SourcePlugin, log: Callable[[str], None]
) -> Iterator[PluginHost]:
    with services.http_client() as client:
        yield PluginHost(plugin, client, services.config_store, log)
