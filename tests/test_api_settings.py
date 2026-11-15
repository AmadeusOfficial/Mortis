from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.settings import Settings

DEFAULT_SETTINGS = {
    "network": {"proxy_host": "127.0.0.1", "proxy_port": 7890, "ignore_cert_errors": True}
}


def _app(data_dir: Path, transport: httpx.BaseTransport | None = None):  # noqa: ANN202
    return create_app(Settings(data_dir=data_dir), http_transport=transport)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    with TestClient(_app(data_dir)) as opened:
        yield opened


def test_settings_defaults(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json() == DEFAULT_SETTINGS


def test_settings_saved_and_survive_restart(client: TestClient, data_dir: Path) -> None:
    body = {"network": {"proxy_host": "", "proxy_port": 1080, "ignore_cert_errors": False}}
    assert client.put("/api/settings", json=body).json() == body
    assert (data_dir / "config.toml").is_file()
    with TestClient(_app(data_dir)) as again:
        assert again.get("/api/settings").json() == body


def test_invalid_port_rejected(client: TestClient) -> None:
    body = {"network": {"proxy_host": "x", "proxy_port": 0, "ignore_cert_errors": True}}
    assert client.put("/api/settings", json=body).status_code == 422


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

    with TestClient(_app(data_dir, httpx.MockTransport(handler))) as client:
        client.put("/api/secrets", json={"tmdb_api_key": "abc"})
        assert client.post("/api/settings/check-tmdb").json() == {
            "ok": True,
            "message": "连接成功",
        }
    assert seen[0].url.params["api_key"] == "abc"


def test_broken_config_file_reports_path(data_dir: Path) -> None:
    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text("[network\n", encoding="utf-8")
    with TestClient(_app(data_dir)) as client:
        response = client.get("/api/settings")
    assert response.status_code == 500
    assert "config.toml" in response.json()["detail"]


def test_data_types_lists_movie(client: TestClient) -> None:
    assert client.get("/api/data-types").json() == [
        {"id": "movie", "name": "电影", "structure": "item_group"}
    ]
