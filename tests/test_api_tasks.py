from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.settings import Settings
from mortis.tasks import repo
from mortis.tasks.base import Followup, TaskContext, TaskResult

LOCAL = "http://127.0.0.1"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(Settings(data_dir=tmp_path / "data"), start_worker=False)

    def echo(payload: dict, ctx: TaskContext) -> TaskResult:
        ctx.log("做了一点事")
        if payload.get("spawn"):
            return TaskResult(followups=[Followup("echo", {}), Followup("boom", {})])
        return TaskResult(result={"ok": True})

    def boom(payload: dict, ctx: TaskContext) -> TaskResult:
        raise RuntimeError("坏了")

    app.state.task_runner.register("echo", echo)
    app.state.task_runner.register("boom", boom)
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _enqueue(client: TestClient, payload: dict) -> int:
    with client.app.state.session_factory() as session:
        task = repo.enqueue(session, "echo", payload)
        session.commit()
        return task.id


def test_list_children_detail_log(client: TestClient) -> None:
    root = _enqueue(client, {"spawn": True})
    client.app.state.task_runner.run_until_idle()
    page = client.get("/api/tasks").json()
    assert page["total"] == 1
    row = page["items"][0]
    assert row["id"] == root and row["status"] == "succeeded"
    assert row["child_count"] == 2
    assert row["child_status"] == {"succeeded": 1, "failed": 1}
    children = client.get(f"/api/tasks/{root}/children").json()
    assert [child["status"] for child in children] == ["succeeded", "failed"]
    detail = client.get(f"/api/tasks/{root}").json()
    assert detail["payload"] == {"spawn": True}
    lines = client.get(f"/api/tasks/{root}/log").json()["lines"]
    assert any("做了一点事" in line for line in lines)
    assert client.get("/api/tasks?status=failed").json()["total"] == 1


def test_batch_cancel_retry_delete(client: TestClient) -> None:
    queued = _enqueue(client, {})
    response = client.post("/api/tasks/batch", json={"action": "cancel", "task_ids": [queued]})
    assert response.json() == {"affected": 1}
    response = client.post("/api/tasks/batch", json={"action": "retry", "task_ids": [queued]})
    assert response.json() == {"affected": 1}
    client.app.state.task_runner.run_until_idle()
    response = client.post("/api/tasks/batch", json={"action": "delete", "task_ids": [queued]})
    assert response.json() == {"affected": 1}
    assert client.get(f"/api/tasks/{queued}").status_code == 404
    assert client.post("/api/tasks/clear-finished").json() == {"affected": 1}
    assert client.get("/api/tasks").json()["total"] == 0


def test_batch_rejects_unknown_action(client: TestClient) -> None:
    response = client.post("/api/tasks/batch", json={"action": "explode", "task_ids": [1]})
    assert response.status_code == 422


def test_restart_marks_running_failed(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, start_worker=False)
    with app.state.session_factory() as session:
        task = repo.enqueue(session, "echo", {})
        task.status = "running"
        session.commit()
        task_id = task.id
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url=LOCAL) as client:
        body = client.get(f"/api/tasks/{task_id}").json()
    assert (body["status"], body["error"]) == ("failed", "程序重启时中断")
