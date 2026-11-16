"""宿主替来源插件做 OAuth 授权码登录，插件不自带页面。"""

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from mortis.plugin_api import SourceError, SourcePlugin
from mortis.plugins.host import PluginHost, save_tokens

STATE_SECONDS = 600


@dataclass(frozen=True)
class _Pending:
    plugin_id: str
    redirect_uri: str
    expires: float


class OAuthStates:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._pending: dict[str, _Pending] = {}

    def issue(self, plugin_id: str, redirect_uri: str) -> str:
        state = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._guard:
            self._pending = {k: v for k, v in self._pending.items() if v.expires > now}
            self._pending[state] = _Pending(plugin_id, redirect_uri, now + STATE_SECONDS)
        return state

    def take(self, plugin_id: str, state: str) -> str | None:
        """一个 state 只能用一次；返回发起登录时的回调地址。"""
        with self._guard:
            pending = self._pending.pop(state, None)
        if pending is None or pending.plugin_id != plugin_id:
            return None
        if pending.expires < time.monotonic():
            return None
        return pending.redirect_uri


def client_values(plugin: SourcePlugin, values: dict[str, Any]) -> tuple[str, str]:
    oauth = plugin.oauth
    assert oauth is not None
    return (
        str(values.get(oauth.client_id_key) or "").strip(),
        str(values.get(oauth.client_secret_key) or "").strip(),
    )


def authorize_url(plugin: SourcePlugin, client_id: str, redirect_uri: str, state: str) -> str:
    oauth = plugin.oauth
    assert oauth is not None
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if oauth.scope:
        query["scope"] = oauth.scope
    return f"{oauth.authorize_url}?{urlencode(query)}"


def exchange_code(host: PluginHost, code: str, redirect_uri: str) -> dict[str, Any]:
    plugin = host.plugin
    oauth = plugin.oauth
    if oauth is None:
        raise SourceError(f"{plugin.name} 不需要登录")
    values = host.store.load_secrets().sources.get(plugin.id, {})
    client_id, client_secret = client_values(plugin, values)
    payload = host.post_json(
        oauth.token_url,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    return save_tokens(host.store, plugin.id, payload, redirect_uri=redirect_uri)
