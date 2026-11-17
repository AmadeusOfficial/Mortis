import logging

import httpx2 as httpx
import pytest

from mortis.config.models import NetworkConfig, Secrets
from mortis.net.http import build_client, proxy_url
from mortis.sources.tmdb.check import TMDB_API, check_tmdb_key, tmdb_auth
from mortis.version import get_version


def _client(handler) -> httpx.Client:  # noqa: ANN001
    return build_client(NetworkConfig(), Secrets(), transport=httpx.MockTransport(handler))


def test_proxy_url_empty_host_means_no_proxy() -> None:
    assert proxy_url(NetworkConfig(proxy_host=""), Secrets()) is None


def test_proxy_url_without_credentials() -> None:
    assert proxy_url(NetworkConfig(proxy_host="127.0.0.1"), Secrets()) == "http://127.0.0.1:7890"


def test_proxy_url_quotes_credentials() -> None:
    secrets = Secrets(network={"proxy_username": "a@b", "proxy_password": "p:w/x"})
    assert proxy_url(NetworkConfig(proxy_host="127.0.0.1", proxy_port=1080), secrets) == (
        "http://a%40b:p%3Aw%2Fx@127.0.0.1:1080"
    )


def test_proxy_url_brackets_ipv6_host() -> None:
    url = proxy_url(NetworkConfig(proxy_host="[::1]", proxy_port=1080), Secrets())
    assert url == "http://[::1]:1080"
    assert httpx.URL(url).host == "::1"
    assert httpx.URL(url).port == 1080


def test_build_client_accepts_ipv6_proxy_host() -> None:
    with build_client(NetworkConfig(proxy_host="[::1]"), Secrets()) as client:
        assert client._mounts


def test_client_sends_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    with _client(handler) as client:
        client.get("https://example.invalid/")
    assert seen[0].headers["User-Agent"] == f"Mortis/{get_version()}"


def test_tmdb_auth_v3_and_v4() -> None:
    assert tmdb_auth(" abc ") == ({}, {"api_key": "abc"})
    assert tmdb_auth("eyJhbGciOi") == ({"Authorization": "Bearer eyJhbGciOi"}, {})


def test_check_without_key_sends_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不应该发请求")

    with _client(handler) as client:
        result = check_tmdb_key(client, "  ")
    assert (result.ok, result.message) == (False, "还没有填 TMDB API Key")


def test_check_success_uses_configuration_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"images": {}})

    with _client(handler) as client:
        result = check_tmdb_key(client, "abc")
    assert (result.ok, result.message) == (True, "连接成功")
    assert str(seen[0].url) == f"{TMDB_API}/configuration?api_key=abc"


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "TMDB 拒绝了这个 API Key"), (503, "TMDB 返回 HTTP 503")],
)
def test_check_reports_http_errors(status: int, message: str) -> None:
    with _client(lambda request: httpx.Response(status)) as client:
        result = check_tmdb_key(client, "abc")
    assert (result.ok, result.message) == (False, message)


def test_check_reports_network_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("代理拒绝连接", request=request)

    with _client(handler) as client:
        result = check_tmdb_key(client, "abc")
    assert result.ok is False
    assert result.message.startswith("连不上 TMDB：")
    assert "代理拒绝连接" in result.message


def test_check_empty_error_message_falls_back_to_type_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("", request=request)

    with _client(handler) as client:
        result = check_tmdb_key(client, "abc")
    assert result.ok is False
    assert result.message == "连不上 TMDB：ReadTimeout"


def test_check_redacts_api_key_from_error_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"无法连接 {request.url}", request=request)

    with _client(handler) as client:
        result = check_tmdb_key(client, "secretkey123")
    assert result.ok is False
    assert "secretkey123" not in result.message
    assert "api_key=***" in result.message


def test_tmdb_key_never_reaches_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with _client(lambda request: httpx.Response(200, json={})) as client:
        result = check_tmdb_key(client, "secretkey123")
    assert result.ok is True
    assert "secretkey123" not in caplog.text


def test_build_client_defaults_without_transport() -> None:
    with build_client(NetworkConfig(), Secrets()) as client:
        assert client.timeout.read == 15.0
        assert client.timeout.connect == 15.0
        assert client.trust_env is False
        assert client.headers["User-Agent"] == f"Mortis/{get_version()}"
        assert not client._mounts  # 默认不用代理


def test_build_client_with_proxy_host_mounts_proxy() -> None:
    with build_client(NetworkConfig(proxy_host="127.0.0.1"), Secrets()) as client:
        assert client._mounts


def test_build_client_without_proxy_host_has_no_proxy_mounts() -> None:
    with build_client(NetworkConfig(proxy_host=""), Secrets()) as client:
        assert not client._mounts
