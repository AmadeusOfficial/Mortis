"""设置页的钩子状态：/api/plugins 里每个钩子的熔断状态，以及重新启用接口。"""

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_hooks_dispatch import H, enqueue_scrape, movie_app, movie_item, run

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
