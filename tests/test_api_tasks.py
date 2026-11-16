from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.db.models import Item, Library, Task, utc_now
from mortis.settings import Settings
from mortis.tasks import repo
from mortis.tasks.base import Followup, TaskContext, TaskResult
from mortis.tasks.logs import task_log_path

LOCAL = "http://127.0.0.1"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(Settings(data_dir=tmp_path / "data"), start_worker=False)

    def echo(payload: dict, ctx: TaskContext) -> TaskResult:
        ctx.log("做了一点事")
        if payload.get("spawn"):
            return TaskResult(followups=[Followup("echo", {}), Followup("boom", {})])
        if "result_item_id" in payload:
            return TaskResult(result={"item_id": payload["result_item_id"]})
        return TaskResult(result={"ok": True})

    def boom(payload: dict, ctx: TaskContext) -> TaskResult:
        raise RuntimeError("坏了")

    app.state.task_runner.register("echo", echo)
    app.state.task_runner.register("boom", boom)
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _enqueue(client: TestClient, payload: dict, *, task_type: str = "echo") -> int:
    with client.app.state.session_factory() as session:
        task = repo.enqueue(session, task_type, payload)
        session.commit()
        return task.id


def _add_item(client: TestClient, title: str = "黑客帝国") -> int:
    with client.app.state.session_factory() as session:
        now = utc_now()
        library = Library(data_type_id="movie", name="电影", created_at=now, updated_at=now)
        session.add(library)
        session.flush()
        item = Item(
            library_id=library.id,
            data_type_id="movie",
            kind="work",
            title=title,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.commit()
        return item.id


def _set_status(client: TestClient, task_id: int, status: str) -> None:
    with client.app.state.session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = status
        session.commit()


def _spawn(client: TestClient, parent_id: int, status: str) -> int:
    with client.app.state.session_factory() as session:
        parent = session.get(Task, parent_id)
        assert parent is not None
        child = repo.enqueue(session, "echo", {}, parent=parent)
        child.status = status
        session.commit()
        return child.id


def test_list_children_detail_log(client: TestClient) -> None:
    root = _enqueue(client, {"spawn": True})
    client.app.state.task_runner.run_until_idle()
    page = client.get("/api/tasks").json()
    assert page["total"] == 1
    row = page["items"][0]
    assert row["id"] == root and row["status"] == "succeeded"
    assert row["child_count"] == 2
    assert row["child_status"] == {"succeeded": 1, "failed": 1}
    assert row["tree_status"] == {"succeeded": 1, "failed": 1}
    assert (row["parent_task_id"], row["root_task_id"], row["priority"]) == (None, None, 0)
    children = client.get(f"/api/tasks/{root}/children").json()
    assert [child["status"] for child in children] == ["succeeded", "failed"]
    assert [child["child_count"] for child in children] == [0, 0]
    assert [child["root_task_id"] for child in children] == [root, root]
    detail = client.get(f"/api/tasks/{root}").json()
    assert detail["payload"] == {"spawn": True}
    lines = client.get(f"/api/tasks/{root}/log").json()["lines"]
    assert any("做了一点事" in line for line in lines)
    assert client.get("/api/tasks?status=failed").json()["total"] == 1


def test_counts_only_direct_children_at_every_level(client: TestClient) -> None:
    root = _enqueue(client, {"spawn": True})
    client.app.state.task_runner.run_until_idle()
    with client.app.state.session_factory() as session:
        middle = repo.children(session, root)[0]
        grandchild = repo.enqueue(session, "echo", {}, parent=middle)
        session.commit()
        middle_id, grandchild_id = middle.id, grandchild.id
    client.app.state.task_runner.run_until_idle()
    row = client.get("/api/tasks").json()["items"][0]
    assert (row["child_count"], row["child_status"]) == (2, {"succeeded": 1, "failed": 1})
    assert row["tree_status"] == {"succeeded": 2, "failed": 1}
    children = client.get(f"/api/tasks/{root}/children").json()
    assert [(row["id"], row["child_count"], row["tree_status"]) for row in children] == [
        (middle_id, 1, {"succeeded": 1}),
        (children[1]["id"], 0, {}),
    ]
    nested = client.get(f"/api/tasks/{middle_id}/children").json()
    assert [(row["id"], row["child_count"], row["tree_status"]) for row in nested] == [
        (grandchild_id, 0, {})
    ]
    detail = client.get(f"/api/tasks/{middle_id}").json()
    assert detail["root_task_id"] == root
    assert detail["tree_status"] == detail["child_status"] == {"succeeded": 1}


def test_detail_item_link(client: TestClient) -> None:
    item_id = _add_item(client)
    from_result = _enqueue(client, {"result_item_id": item_id})
    from_payload = _enqueue(client, {"item_id": item_id})
    client.app.state.task_runner.run_until_idle()
    expected = {"id": item_id, "title": "黑客帝国"}
    assert client.get(f"/api/tasks/{from_result}").json()["item"] == expected
    assert client.get(f"/api/tasks/{from_payload}").json()["item"] == expected
    with client.app.state.session_factory() as session:
        session.delete(session.get(Item, item_id))
        session.commit()
    assert client.get(f"/api/tasks/{from_result}").json()["item"] is None
    assert client.get(f"/api/tasks/{from_payload}").json()["item"] is None


def test_worker_pause_blocks_new_tasks(client: TestClient) -> None:
    assert client.get("/api/tasks/worker").json() == {"paused": False}
    assert client.put("/api/tasks/worker", json={"paused": True}).json() == {"paused": True}
    assert client.get("/api/tasks/worker").json() == {"paused": True}
    task = _enqueue(client, {})
    assert client.app.state.task_runner.run_once() is False
    assert client.get(f"/api/tasks/{task}").json()["status"] == "queued"
    assert client.put("/api/tasks/worker", json={"paused": False}).json() == {"paused": False}
    client.app.state.task_runner.run_until_idle()
    assert client.get(f"/api/tasks/{task}").json()["status"] == "succeeded"


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


def test_batch_rejects_unknown_action(client: TestClient) -> None:
    response = client.post("/api/tasks/batch", json={"action": "explode", "task_ids": [1]})
    assert response.status_code == 422


def test_bulk_cancel_active(client: TestClient) -> None:
    first = _enqueue(client, {})
    second = _enqueue(client, {})
    assert client.post("/api/tasks/bulk", json={"action": "cancel_active"}).json() == {
        "affected": 2
    }
    assert [client.get(f"/api/tasks/{task}").json()["status"] for task in (first, second)] == [
        "cancelled",
        "cancelled",
    ]
    assert client.post("/api/tasks/bulk", json={"action": "cancel_active"}).json() == {
        "affected": 0
    }


def test_bulk_retry_failed(client: TestClient) -> None:
    failed = _enqueue(client, {}, task_type="boom")
    client.app.state.task_runner.run_until_idle()
    assert client.get(f"/api/tasks/{failed}").json()["status"] == "failed"
    assert client.post("/api/tasks/bulk", json={"action": "retry_failed"}).json() == {"affected": 1}
    page = client.get("/api/tasks").json()
    assert (page["total"], page["items"][0]["status"]) == (2, "queued")


def test_bulk_clear_succeeded_skips_tree_with_failure(client: TestClient) -> None:
    good = _enqueue(client, {})
    broken = _enqueue(client, {"spawn": True})
    client.app.state.task_runner.run_until_idle()
    log = task_log_path(client.app.state.services.data_dir, good)
    assert log.is_file()
    assert client.post("/api/tasks/bulk", json={"action": "clear_succeeded"}).json() == {
        "affected": 1
    }
    assert client.get(f"/api/tasks/{good}").status_code == 404
    assert not log.exists()
    detail = client.get(f"/api/tasks/{broken}").json()
    assert detail["tree_status"] == {"succeeded": 1, "failed": 1}


def test_bulk_clear_failed(client: TestClient) -> None:
    parent = _enqueue(client, {}, task_type="boom")
    client.app.state.task_runner.run_until_idle()
    _set_status(client, parent, "succeeded")
    failed_child = _spawn(client, parent, "failed")
    busy = _enqueue(client, {}, task_type="boom")
    client.app.state.task_runner.run_until_idle()
    queued_child = _spawn(client, busy, "queued")
    assert client.post("/api/tasks/bulk", json={"action": "clear_failed"}).json() == {"affected": 1}
    assert client.get(f"/api/tasks/{failed_child}").status_code == 404
    assert client.get(f"/api/tasks/{parent}").status_code == 200
    # 失败根任务还有排队中的子任务，它自己留着。
    assert client.get(f"/api/tasks/{busy}").status_code == 200
    assert client.get(f"/api/tasks/{queued_child}").status_code == 200


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
