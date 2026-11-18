import json
from collections.abc import Callable, Iterator
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
        "ai_api_key": False,
    }
    response = client.put("/api/secrets", json={"tmdb_api_key": "  k-123  "})
    assert response.json() == {
        "tmdb_api_key": True,
        "proxy_username": False,
        "proxy_password": False,
        "ai_api_key": False,
    }
    assert "k-123" not in response.text
    assert "k-123" not in client.get("/api/secrets").text
    assert 'api_key = "k-123"' in (data_dir / "secrets.toml").read_text(encoding="utf-8")
    assert "[sources.tmdb]" in (data_dir / "secrets.toml").read_text(encoding="utf-8")
    assert not (data_dir / "config.toml").exists()


def test_secrets_partial_update_and_clear(client: TestClient) -> None:
    client.put("/api/secrets", json={"proxy_username": "me", "tmdb_api_key": "k"})
    status = client.put("/api/secrets", json={"tmdb_api_key": None}).json()
    assert status == {
        "tmdb_api_key": False,
        "proxy_username": True,
        "proxy_password": False,
        "ai_api_key": False,
    }
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


AI_KEY = "sk-ai-secret-123"


def test_ai_settings_saved(client: TestClient, data_dir: Path) -> None:
    body = {"base_url": "https://api.example.com/v1/", "model": "qwen-plus", "max_rounds": 12}
    saved = client.put("/api/settings/ai", json=body).json()
    assert saved["ai"] == {**body, "base_url": "https://api.example.com/v1"}
    assert client.get("/api/settings").json()["ai"] == saved["ai"]
    assert "[ai]" in (data_dir / "config.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "body",
    [
        {"max_rounds": 0},
        {"max_rounds": 21},
        {"base_url": "api.deepseek.com"},
        {"model": ""},
    ],
)
def test_ai_settings_rejects_bad_values(client: TestClient, body: dict[str, object]) -> None:
    assert client.put("/api/settings/ai", json=body).status_code == 422
    assert client.get("/api/settings").json()["ai"] == DEFAULT_SETTINGS["ai"]


def test_ai_key_saved_but_never_returned(client: TestClient, data_dir: Path) -> None:
    status = client.put("/api/secrets", json={"ai_api_key": f"  {AI_KEY}  "}).json()
    assert status["ai_api_key"] is True
    for path in ("/api/settings", "/api/secrets"):
        assert AI_KEY not in client.get(path).text
    text = (data_dir / "secrets.toml").read_text(encoding="utf-8")
    assert "[ai]" in text
    assert f'api_key = "{AI_KEY}"' in text
    assert not (data_dir / "config.toml").exists()


def test_check_ai_without_key(client: TestClient) -> None:
    assert client.post("/api/settings/check-ai").json() == {
        "ok": False,
        "message": "还没有填 AI 的 key",
    }


def _ai_client(data_dir: Path, handler: Callable[[httpx.Request], httpx.Response]) -> TestClient:
    return TestClient(_app(data_dir, httpx.MockTransport(handler)), base_url=LOCAL)


def test_check_ai_success_uses_saved_key_and_draft(data_dir: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "好"}}]})

    with _ai_client(data_dir, handler) as client:
        client.put("/api/secrets", json={"ai_api_key": AI_KEY})
        assert client.post("/api/settings/check-ai").json() == {
            "ok": True,
            "message": "连接成功，模型 deepseek-flash 可用",
        }
        draft = {"base_url": "https://llm.example.com/v1/", "model": "qwen-plus"}
        response = client.post("/api/settings/check-ai", json=draft)
        assert response.json()["ok"] is True
        assert client.get("/api/settings").json()["ai"] == DEFAULT_SETTINGS["ai"]

    first, second = seen
    assert str(first.url) == "https://api.deepseek.com/chat/completions"
    assert first.headers["Authorization"] == f"Bearer {AI_KEY}"
    payload = json.loads(first.content)
    assert payload["model"] == "deepseek-flash"
    assert payload["stream"] is False
    assert 0 < payload["max_tokens"] <= 8
    assert str(second.url) == "https://llm.example.com/v1/chat/completions"
    assert json.loads(second.content)["model"] == "qwen-plus"


@pytest.mark.parametrize("status", [401, 403])
def test_check_ai_rejected_key(data_dir: Path, status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": f"bad key {AI_KEY}"}})

    with _ai_client(data_dir, handler) as client:
        client.put("/api/secrets", json={"ai_api_key": AI_KEY})
        body = client.post("/api/settings/check-ai").json()
    assert body["ok"] is False
    assert body["message"].startswith("key 不对")
    assert AI_KEY not in body["message"]


def test_check_ai_timeout(data_dir: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with _ai_client(data_dir, handler) as client:
        client.put("/api/secrets", json={"ai_api_key": AI_KEY})
        body = client.post("/api/settings/check-ai").json()
    assert body["ok"] is False
    assert body["message"].startswith("连不上")
    assert "超时" in body["message"]


def test_check_ai_other_status_gives_reason(data_dir: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "Model Not Exist"}})

    with _ai_client(data_dir, handler) as client:
        client.put("/api/secrets", json={"ai_api_key": AI_KEY})
        body = client.post("/api/settings/check-ai").json()
    assert body == {"ok": False, "message": "接口返回 HTTP 404：Model Not Exist，检查接口地址"}


def test_check_ai_shares_status_messages(data_dir: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})

    with _ai_client(data_dir, handler) as client:
        client.put("/api/secrets", json={"ai_api_key": AI_KEY})
        body = client.post("/api/settings/check-ai").json()
    assert body == {"ok": False, "message": "余额不足：接口返回 HTTP 402：Insufficient Balance"}


def test_check_ai_rejects_bad_draft(client: TestClient) -> None:
    response = client.post("/api/settings/check-ai", json={"base_url": "nope"})
    assert response.status_code == 422


def test_broken_config_file_reports_path(data_dir: Path) -> None:
    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text("[network\n", encoding="utf-8")
    with TestClient(_app(data_dir), base_url=LOCAL) as client:
        response = client.get("/api/settings")
    assert response.status_code == 500
    assert "config.toml" in response.json()["detail"]


def test_data_types_lists_movie(client: TestClient) -> None:
    assert client.get("/api/data-types").json() == [
        {"id": "anime", "name": "动漫", "structure": "series", "plugin_id": None},
        {"id": "galgame", "name": "Galgame", "structure": "item", "plugin_id": "galgame"},
        {"id": "movie", "name": "电影", "structure": "item_group", "plugin_id": None},
        {"id": "tv", "name": "电视剧", "structure": "series", "plugin_id": None},
    ]


def test_old_whole_put_is_gone(client: TestClient) -> None:
    assert client.put("/api/settings", json={}).status_code == 405


def test_each_section_saves_alone(client: TestClient) -> None:
    client.put(
        "/api/settings/network",
        json={"proxy_host": "", "proxy_port": 1080, "ignore_cert_errors": False},
    )
    body = client.put("/api/settings/types/movie", json={"language": "en-US"}).json()
    assert body["types"]["movie"]["language"] == "en-US"
    assert body["network"]["proxy_port"] == 1080
    body = client.put(
        "/api/settings/files", json={"video_extensions": ["mkv"], "subtitle_extensions": ["srt"]}
    ).json()
    assert body["files"]["video_extensions"] == [".mkv"]
    body = client.put("/api/settings/tasks", json={"concurrency": 2, "keep_days": 7}).json()
    assert body["tasks"] == {"concurrency": 2, "keep_days": 7}
    assert client.get("/api/settings").json() == body


def test_section_validation_rejects_bad_threshold(client: TestClient) -> None:
    assert client.put("/api/settings/types/movie", json={"match_threshold": 5}).status_code == 422


def test_tv_and_anime_sections_save_separately(client: TestClient) -> None:
    config = client.get("/api/settings").json()
    anime = {**config["types"]["anime"], "language": "ja-JP"}
    saved = client.put("/api/settings/types/anime", json=anime).json()
    assert (saved["types"]["anime"]["language"], saved["types"]["tv"]["language"]) == (
        "ja-JP",
        "zh-CN",
    )
    tv = {**saved["types"]["tv"], "match_threshold": 0.6}
    assert client.put("/api/settings/types/tv", json=tv).json()["types"]["tv"][
        "match_threshold"
    ] == (0.6)


def test_old_type_routes_are_gone(client: TestClient) -> None:
    for name in ("movie", "tv", "anime"):
        assert client.put(f"/api/settings/{name}", json={}).status_code in (404, 405)


def test_unknown_type_is_404(client: TestClient) -> None:
    assert client.put("/api/settings/types/nope", json={}).status_code == 404


def test_plugin_type_settings_save_flat(client: TestClient, data_dir: Path) -> None:
    # galgame 是自带的插件类型，启动时已经装进 data_types 表，接口据此判断类型存在。
    types = client.get("/api/settings").json()["types"]
    assert "galgame" not in types or types["galgame"] == {}
    saved = client.put("/api/settings/types/galgame", json={"match_threshold": 0.5}).json()
    assert saved["types"]["galgame"]["match_threshold"] == 0.5
    assert client.get("/api/settings").json()["types"]["galgame"]["match_threshold"] == 0.5
    text = (data_dir / "config.toml").read_text(encoding="utf-8")
    assert "[types.galgame]" in text
    assert "match_threshold = 0.5" in text


def test_plugin_type_settings_reject_bad_values(client: TestClient) -> None:
    response = client.put("/api/settings/types/galgame", json={"match_threshold": 5})
    assert response.status_code == 422
    assert "设置不合法" in response.json()["detail"]
