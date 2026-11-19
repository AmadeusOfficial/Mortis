"""设置页的钩子：/api/plugins 里每个钩子的熔断状态、设置和密钥，重新启用和保存设置的接口。"""

import tomllib
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mortis.tasks.logs import task_log_path
from tests.test_hooks_dispatch import (
    H,
    dumped,
    enqueue_scrape,
    hook_tasks_of,
    movie_app,
    movie_item,
    run,
)

RAISE = 'raise RuntimeError("炸了")'


def hooks_of(client: TestClient, plugin_id: str) -> dict[str, dict[str, Any]]:
    listed = {p["id"]: p for p in client.get("/api/plugins").json()}
    return {h["id"]: h for h in listed[plugin_id]["hooks"]}


def blow_fuse(app: FastAPI, tmp_path: Path) -> None:
    item_id = movie_item(app, tmp_path)
    for _ in range(3):
        enqueue_scrape(app, item_id, force=True)
        run(app)


def test_hook_status_fields_in_plugin_list(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {"t.bad": [H("t.boom", "scrape.finished", RAISE)]})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        fresh = hooks_of(client, "t.bad")["t.boom"]
        assert fresh["event"] == "scrape.finished" and fresh["writes"] == []
        assert fresh["disabled"] is False and fresh["reason"] is None
        assert (fresh["failures"], fresh["last_error"], fresh["runs"]) == (0, None, 0)
        item_id = movie_item(app, tmp_path)
        enqueue_scrape(app, item_id, force=True)
        run(app)
        one = hooks_of(client, "t.bad")["t.boom"]
        assert one["disabled"] is False
        assert (one["failures"], one["runs"]) == (1, 1)
        assert "RuntimeError: 炸了" in one["last_error"]


def test_tripped_hook_reports_reason_and_enable_restores_it(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {"t.bad": [H("t.boom", "scrape.finished", RAISE)]})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        blow_fuse(app, tmp_path)
        state = hooks_of(client, "t.bad")["t.boom"]
        assert state["disabled"] is True and state["failures"] == 3
        assert "连续失败 3 次" in state["reason"]
        assert "t.boom" in app.state.config_store.load_config().plugins.disabled

        response = client.post("/api/plugins/t.bad/hooks/t.boom/enable")
        assert response.status_code == 200, response.text
        again = {h["id"]: h for h in response.json()["hooks"]}["t.boom"]
        assert again["disabled"] is False and again["failures"] == 0
        assert again["reason"] is None
        # 持久化：设置文件里的停用名单去掉了，重载后仍然启用。
        assert "t.boom" not in app.state.config_store.load_config().plugins.disabled
        client.post("/api/plugins/reload")
        assert hooks_of(client, "t.bad")["t.boom"]["disabled"] is False
        assert app.state.services.plugins.hooks() != []


def test_restart_keeps_generic_reason_only(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {"t.bad": [H("t.boom", "scrape.finished", RAISE)]})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        blow_fuse(app, tmp_path)
        registry = app.state.services.plugins
        registry.health.reset("t.boom")  # 模拟重启：内存里的原因丢了
        registry.load(app.state.config_store.load_config().plugins.disabled)
        state = hooks_of(client, "t.bad")["t.boom"]
        assert state["disabled"] is True
        assert "连续失败 3 次" not in state["reason"]
        assert state["failures"] == 0 and state["last_error"] is None


def test_enable_unknown_hook_or_wrong_plugin_is_404(tmp_path: Path) -> None:
    app = movie_app(
        tmp_path,
        {"t.a": [H("t.one", "scrape.finished")], "t.b": [H("t.two", "scrape.finished")]},
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/plugins/t.a/hooks/nope/enable").status_code == 404
        assert client.post("/api/plugins/t.a/hooks/t.two/enable").status_code == 404
        assert client.post("/api/plugins/nope/hooks/t.one/enable").status_code == 404


# ---- 钩子的设置和密钥 ----

CONFIGURED = """
class Settings(BaseModel):
    prefix: str = "默认"
    count: int = Field(default=1, ge=1)
    words: list[str] = []
    note: str | None = None

class Secrets(BaseModel):
    token: str = ""
"""

DUMP = """
dump(host, self.id + ".json", {
    "settings": host.settings.model_dump(mode="json"),
    "secrets": host.secrets.model_dump(mode="json"),
})
"""


def configured_app(tmp_path: Path) -> FastAPI:
    return movie_app(
        tmp_path,
        {
            "t.cfg": [
                H("t.conf", "scrape.finished", DUMP, members=CONFIGURED),
                H("t.plain", "scrape.finished", DUMP),
            ],
            "t.other": [H("t.else", "scrape.finished")],
        },
    )


def put_hook(client: TestClient, plugin_id: str, hook_id: str, body: dict[str, Any]) -> Any:
    return client.put(f"/api/plugins/{plugin_id}/hooks/{hook_id}", json=body)


def test_hook_settings_saved_through_api_reach_the_running_hook(tmp_path: Path) -> None:
    app = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = put_hook(
            client,
            "t.cfg",
            "t.conf",
            {
                "settings": {"prefix": "新", "count": 3, "words": ["甲", "乙"]},
                "secrets": {"token": " abc "},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["id"] == "t.cfg"
        state = {h["id"]: h for h in response.json()["hooks"]}["t.conf"]
        assert state["settings"] == {
            "prefix": "新",
            "count": 3,
            "words": ["甲", "乙"],
            "note": None,
        }
        assert state["secrets"] == {"token": True}
        enqueue_scrape(app, movie_item(app, tmp_path))
        run(app)
    assert dumped(app, "t.cfg", "t.conf.json") == {
        "settings": {"prefix": "新", "count": 3, "words": ["甲", "乙"], "note": None},
        "secrets": {"token": "abc"},
    }
    store = app.state.config_store
    raw = tomllib.loads(store.config_path.read_text(encoding="utf-8"))
    assert raw["sources"]["t.conf"] == {"prefix": "新", "count": 3, "words": ["甲", "乙"]}
    assert store.load_secrets().sources["t.conf"] == {"token": "abc"}


def test_invalid_values_in_files_fall_back_to_defaults_and_log(tmp_path: Path) -> None:
    app = configured_app(tmp_path)
    store = app.state.config_store
    store.save_plugin("t.conf", values={"count": "很多", "prefix": "留不住"})
    store.update_plugin_secrets("t.conf", {"token": 5})
    scrape_id = enqueue_scrape(app, movie_item(app, tmp_path))
    run(app)
    assert dumped(app, "t.cfg", "t.conf.json") == {
        "settings": {"prefix": "默认", "count": 1, "words": [], "note": None},
        "secrets": {"token": ""},
    }
    task = next(t for t in hook_tasks_of(app, scrape_id) if '"t.conf"' in (t.payload or ""))
    assert task.status == "succeeded", task.error
    log = task_log_path(app.state.settings.data_dir, task.id).read_text(encoding="utf-8")
    assert "[t.conf]" in log and "设置" in log and "count" in log and "密钥" in log
    assert "很多" not in log


def test_plugin_list_shows_hook_schema_without_secret_values(tmp_path: Path) -> None:
    app = configured_app(tmp_path)
    app.state.config_store.update_plugin_secrets("t.conf", {"token": "绝密"})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/plugins")
        assert "绝密" not in response.text
        hooks = hooks_of(client, "t.cfg")
    conf = hooks["t.conf"]
    assert set(conf["settings_schema"]["properties"]) == {"prefix", "count", "words", "note"}
    assert conf["settings_schema"]["properties"]["note"]["type"] == "string"  # 规范化过
    assert conf["settings"] == {"prefix": "默认", "count": 1, "words": [], "note": None}
    assert set(conf["secrets_schema"]["properties"]) == {"token"}
    assert conf["secrets"] == {"token": True}
    plain = hooks["t.plain"]
    assert (plain["settings_schema"], plain["settings"]) == (None, None)
    assert (plain["secrets_schema"], plain["secrets"]) == (None, None)


def test_put_hook_settings_rejects_invalid_values_with_location(tmp_path: Path) -> None:
    app = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        bad = put_hook(client, "t.cfg", "t.conf", {"settings": {"count": 0, "words": ["a", 2]}})
        assert bad.status_code == 422
        assert "count" in bad.text and "words.1" in bad.text
        extra = put_hook(client, "t.cfg", "t.conf", {"enabled": False})
        assert extra.status_code == 422
        unknown = put_hook(client, "t.cfg", "t.conf", {"secrets": {"nope": "x"}})
        assert unknown.status_code == 422 and "没有这些密钥：nope" in unknown.text
    assert "t.conf" not in app.state.config_store.load_config().sources
    assert "t.conf" not in app.state.config_store.load_secrets().sources


def test_put_hook_of_another_plugin_is_404(tmp_path: Path) -> None:
    app = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        body = {"settings": {}}
        assert put_hook(client, "t.cfg", "t.else", body).status_code == 404
        assert put_hook(client, "t.cfg", "nope", body).status_code == 404
        assert put_hook(client, "nope", "t.conf", body).status_code == 404


def test_hook_without_settings_keeps_old_behavior(tmp_path: Path) -> None:
    app = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        no_settings = put_hook(client, "t.cfg", "t.plain", {"settings": {"x": 1}})
        assert no_settings.status_code == 422 and "没有可改的设置" in no_settings.text
        no_secrets = put_hook(client, "t.cfg", "t.plain", {"secrets": {"x": "1"}})
        assert no_secrets.status_code == 422 and "没有密钥" in no_secrets.text
        enqueue_scrape(app, movie_item(app, tmp_path))
        run(app)
    assert dumped(app, "t.cfg", "t.plain.json") == {"settings": {}, "secrets": {}}
    assert "t.plain" not in app.state.config_store.load_config().sources
