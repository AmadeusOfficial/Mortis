from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.config.models import AppConfig
from mortis.settings import Settings

LOCAL = "http://127.0.0.1"  # TestClient 默认的 testserver 不在主机白名单里

DEFAULT_SETTINGS = AppConfig().model_dump(mode="json")


def _app(data_dir: Path, transport: httpx.BaseTransport | None = None):  # noqa: ANN202
    return create_app(Settings(data_dir=data_dir), http_transport=transport)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    with TestClient(_app(data_dir), base_url=LOCAL) as opened:
        yield opened


def test_settings_defaults(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json() == DEFAULT_SETTINGS


def test_settings_saved_and_survive_restart(client: TestClient, data_dir: Path) -> None:
    network = {"proxy_host": "", "proxy_port": 1080, "ignore_cert_errors": False}
    body = client.put("/api/settings/network", json=network).json()
    assert body == {**DEFAULT_SETTINGS, "network": network}
    assert (data_dir / "config.toml").is_file()
    with TestClient(_app(data_dir), base_url=LOCAL) as again:
        assert again.get("/api/settings").json() == body


def test_invalid_port_rejected(client: TestClient) -> None:
    body = {"proxy_host": "x", "proxy_port": 0, "ignore_cert_errors": True}
    assert client.put("/api/settings/network", json=body).status_code == 422


@pytest.mark.parametrize("host", ["http://127.0.0.1", "127.0.0.1:7890", "user@proxy", "::1"])
def test_invalid_proxy_host_rejected_with_message(client: TestClient, host: str) -> None:
    body = {"proxy_host": host, "proxy_port": 7890, "ignore_cert_errors": True}
    response = client.put("/api/settings/network", json=body)
    assert response.status_code == 422
    assert "代理主机只填主机名或 IP" in response.json()["detail"][0]["msg"]
    assert client.get("/api/settings").json() == DEFAULT_SETTINGS


def test_ipv6_proxy_host_saved(client: TestClient) -> None:
    body = {"proxy_host": "[::1]", "proxy_port": 7890, "ignore_cert_errors": True}
    assert client.put("/api/settings/network", json=body).json()["network"] == body


def test_secrets_status_never_returns_values(client: TestClient, data_dir: Path) -> None:
    assert client.get("/api/secrets").json() == {
        "tmdb_api_key": False,
        "proxy_username": False,
        "proxy_password": False,
    }
    response = client.put("/api/secrets", json={"tmdb_api_key": "  k-123  "})
    assert response.json() == {
        "tmdb_api_key": True,
        "proxy_username": False,
        "proxy_password": False,
    }
    assert "k-123" not in response.text
    assert "k-123" not in client.get("/api/secrets").text
    assert 'tmdb_api_key = "k-123"' in (data_dir / "secrets.toml").read_text(encoding="utf-8")
    assert not (data_dir / "config.toml").exists()


def test_secrets_partial_update_and_clear(client: TestClient) -> None:
    client.put("/api/secrets", json={"proxy_username": "me", "tmdb_api_key": "k"})
    status = client.put("/api/secrets", json={"tmdb_api_key": None}).json()
    assert status == {"tmdb_api_key": False, "proxy_username": True, "proxy_password": False}
    status = client.put("/api/secrets", json={"proxy_username": ""}).json()
    assert status["proxy_username"] is False


def test_unknown_secret_key_rejected(client: TestClient) -> None:
    assert client.put("/api/secrets", json={"password": "x"}).status_code == 422


def test_check_tmdb_without_key(client: TestClient) -> None:
    assert client.post("/api/settings/check-tmdb").json() == {
        "ok": False,
        "message": "还没有填 TMDB API Key",
    }


def test_check_tmdb_uses_saved_key(data_dir: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    with TestClient(_app(data_dir, httpx.MockTransport(handler)), base_url=LOCAL) as client:
        client.put("/api/secrets", json={"tmdb_api_key": "abc"})
        assert client.post("/api/settings/check-tmdb").json() == {
            "ok": True,
            "message": "连接成功",
        }
    assert seen[0].url.params["api_key"] == "abc"


def test_broken_config_file_reports_path(data_dir: Path) -> None:
    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text("[network\n", encoding="utf-8")
    with TestClient(_app(data_dir), base_url=LOCAL) as client:
        response = client.get("/api/settings")
    assert response.status_code == 500
    assert "config.toml" in response.json()["detail"]


def test_data_types_lists_movie(client: TestClient) -> None:
    assert client.get("/api/data-types").json() == [
        {"id": "anime", "name": "动漫", "structure": "series"},
        {"id": "galgame", "name": "Galgame", "structure": "item"},
        {"id": "movie", "name": "电影", "structure": "item_group"},
        {"id": "tv", "name": "电视剧", "structure": "series"},
    ]


def test_old_whole_put_is_gone(client: TestClient) -> None:
    assert client.put("/api/settings", json={}).status_code == 405


def test_each_section_saves_alone(client: TestClient) -> None:
    client.put(
        "/api/settings/network",
        json={"proxy_host": "", "proxy_port": 1080, "ignore_cert_errors": False},
    )
    body = client.put("/api/settings/movie", json={"language": "en-US"}).json()
    assert body["movie"]["language"] == "en-US"
    assert body["network"]["proxy_port"] == 1080
    body = client.put(
        "/api/settings/files", json={"video_extensions": ["mkv"], "subtitle_extensions": ["srt"]}
    ).json()
    assert body["files"]["video_extensions"] == [".mkv"]
    body = client.put("/api/settings/tasks", json={"concurrency": 2, "keep_days": 7}).json()
    assert body["tasks"] == {"concurrency": 2, "keep_days": 7}
    assert client.get("/api/settings").json() == body


def test_section_validation_rejects_bad_threshold(client: TestClient) -> None:
    assert client.put("/api/settings/movie", json={"match_threshold": 5}).status_code == 422


def test_tv_and_anime_sections_save_separately(client: TestClient) -> None:
    config = client.get("/api/settings").json()
    anime = {**config["anime"], "language": "ja-JP"}
    saved = client.put("/api/settings/anime", json=anime).json()
    assert (saved["anime"]["language"], saved["tv"]["language"]) == ("ja-JP", "zh-CN")
    tv = {**saved["tv"], "match_threshold": 0.6}
    assert client.put("/api/settings/tv", json=tv).json()["tv"]["match_threshold"] == 0.6
