import json
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from mortis.config.store import ConfigStore
from mortis.db.engine import make_session_factory
from mortis.db.models import Task, utc_now
from mortis.services import KeyedLocks, Services
from mortis.tasks import repo
from mortis.tasks.base import Followup, TaskContext, TaskError, TaskResult
from mortis.tasks.logs import tail_log, task_log_path
from mortis.tasks.runner import TaskRunner

Factory = sessionmaker[Session]


@pytest.fixture
def factory(engine: Engine) -> Factory:
    return make_session_factory(engine)


@pytest.fixture
def services(tmp_path: Path) -> Services:
    return Services(data_dir=tmp_path, config_store=ConfigStore(tmp_path))


def _runner(factory: Factory, services: Services, handlers: dict) -> TaskRunner:
    return TaskRunner(factory, services, handlers, concurrency=1, poll_seconds=0.05)


def _add(
    factory: Factory, task_type: str = "echo", payload: dict | None = None, **kwargs: int
) -> int:
    with factory() as session:
        task = repo.enqueue(session, task_type, payload or {}, **kwargs)
        session.commit()
        return task.id


def _get(factory: Factory, task_id: int) -> Task:
    with factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        session.expunge(task)
        return task


def test_success_records_result_and_log(factory: Factory, services: Services) -> None:
    def echo(payload: dict, ctx: TaskContext) -> TaskResult:
        ctx.log(f"收到 {payload['n']}")
        return TaskResult(result={"n": payload["n"]})

    task_id = _add(factory, payload={"n": 3})
    _runner(factory, services, {"echo": echo}).run_until_idle()
    task = _get(factory, task_id)
    assert task.status == repo.SUCCEEDED
    assert json.loads(task.result or "") == {"n": 3}
    assert task.started_at and task.finished_at
    lines = tail_log(task_log_path(services.data_dir, task_id), 50)
    assert any("收到 3" in line for line in lines)


def test_followups_are_children_of_the_root(factory: Factory, services: Services) -> None:
    def parent(payload: dict, ctx: TaskContext) -> TaskResult:
        depth = payload.get("depth", 0)
        if depth < 2:
            return TaskResult(followups=[Followup("echo", {"depth": depth + 1})])
        return TaskResult()

    root_id = _add(factory)
    _runner(factory, services, {"echo": parent}).run_until_idle()
    with factory() as session:
        rows = session.scalars(select(Task).order_by(Task.id)).all()
        assert [row.status for row in rows] == ["succeeded"] * 3
        assert rows[0].root_task_id is None
        assert rows[1].parent_task_id == root_id and rows[1].root_task_id == root_id
        assert rows[2].parent_task_id == rows[1].id and rows[2].root_task_id == root_id
        assert rows[1].priority == -1


def test_priority_then_id_order(factory: Factory, services: Services) -> None:
    seen: list[int] = []

    def record(payload: dict, ctx: TaskContext) -> TaskResult:
        seen.append(payload["n"])
        return TaskResult()

    _add(factory, payload={"n": 1})
    _add(factory, payload={"n": 2}, priority=5)
    _add(factory, payload={"n": 3})
    _runner(factory, services, {"echo": record}).run_until_idle()
    assert seen == [2, 1, 3]


def test_task_error_and_crash(factory: Factory, services: Services) -> None:
    def fail(payload: dict, ctx: TaskContext) -> TaskResult:
        if payload["kind"] == "user":
            raise TaskError("媒体库不存在")
        raise RuntimeError("坏了")

    user = _add(factory, payload={"kind": "user"})
    crash = _add(factory, payload={"kind": "crash"})
    _runner(factory, services, {"echo": fail}).run_until_idle()
    assert (_get(factory, user).status, _get(factory, user).error) == ("failed", "媒体库不存在")
    assert _get(factory, crash).error == "RuntimeError: 坏了"
    log = "\n".join(tail_log(task_log_path(services.data_dir, crash), 200))
    assert "Traceback" in log


def test_unknown_type_fails(factory: Factory, services: Services) -> None:
    task_id = _add(factory, task_type="nope")
    _runner(factory, services, {}).run_until_idle()
    task = _get(factory, task_id)
    assert task.status == "failed"
    assert "nope" in (task.error or "")


def test_cancel_queued_root_cancels_queued_children(factory: Factory) -> None:
    root = _add(factory)
    with factory() as session:
        parent = session.get(Task, root)
        assert parent is not None
        parent.status = repo.SUCCEEDED
        child = repo.enqueue(session, "echo", {}, parent=parent)
        session.commit()
        child_id = child.id
    with factory() as session:
        affected, running = repo.cancel(session, [root])
    assert (affected, running) == (1, [])
    assert _get(factory, child_id).status == "cancelled"
    assert _get(factory, root).status == "succeeded"


def test_cancel_running_discards_result_and_followups(factory: Factory, services: Services) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow(payload: dict, ctx: TaskContext) -> TaskResult:
        started.set()
        release.wait(5)
        return TaskResult(result={"x": 1}, followups=[Followup("echo", {})])

    task_id = _add(factory)
    runner = _runner(factory, services, {"echo": slow})
    worker = threading.Thread(target=runner.run_once)
    worker.start()
    assert started.wait(5)
    with factory() as session:
        _, running = repo.cancel(session, [task_id])
    runner.request_cancel(running)
    release.set()
    worker.join(5)
    task = _get(factory, task_id)
    assert (task.status, task.error, task.result) == ("cancelled", "已取消", None)
    with factory() as session:
        assert len(session.scalars(select(Task)).all()) == 1


def test_check_cancelled_raises(factory: Factory, services: Services) -> None:
    def loop(payload: dict, ctx: TaskContext) -> TaskResult:
        for _ in range(200):
            ctx.check_cancelled()
            time.sleep(0.01)
        return TaskResult()

    task_id = _add(factory)
    runner = _runner(factory, services, {"echo": loop})
    worker = threading.Thread(target=runner.run_once)
    worker.start()
    time.sleep(0.1)
    with factory() as session:
        _, running = repo.cancel(session, [task_id])
    runner.request_cancel(running)
    worker.join(5)
    assert not worker.is_alive()
    assert _get(factory, task_id).status == "cancelled"


def test_progress_is_visible_while_running(factory: Factory, services: Services) -> None:
    seen = threading.Event()
    release = threading.Event()

    def work(payload: dict, ctx: TaskContext) -> TaskResult:
        ctx.progress(2, 5, "第二部")
        seen.set()
        release.wait(5)
        return TaskResult()

    task_id = _add(factory)
    runner = _runner(factory, services, {"echo": work})
    worker = threading.Thread(target=runner.run_once)
    worker.start()
    assert seen.wait(5)
    assert runner.progress_of(task_id) == {"current": 2, "total": 5, "message": "第二部"}
    release.set()
    worker.join(5)
    assert runner.progress_of(task_id) is None


def test_recover_running_and_prune(factory: Factory, tmp_path: Path) -> None:
    with factory() as session:
        running = Task(task_type="echo", status="running", created_at=utc_now())
        old = Task(
            task_type="echo",
            status="succeeded",
            created_at="2000-01-01T00:00:00+00:00",
            finished_at="2000-01-01T00:00:00+00:00",
        )
        session.add_all([running, old])
        session.commit()
        running_id, old_id = running.id, old.id
        log = task_log_path(tmp_path, old_id)
        log.parent.mkdir(parents=True)
        log.write_text("x", encoding="utf-8")
        assert repo.recover_running(session) == 1
        session.expire_all()
        assert session.get(Task, running_id).error == "程序重启时中断"
        assert repo.prune(session, keep_days=30, data_dir=tmp_path) == [old_id]
        session.expire_all()
        assert session.get(Task, old_id) is None
        assert not log.exists()


def test_retry_and_delete(factory: Factory) -> None:
    with factory() as session:
        failed = Task(
            task_type="echo",
            status="failed",
            payload='{"a": 1}',
            priority=2,
            created_at=utc_now(),
            error="x",
        )
        active = Task(task_type="echo", status="queued", created_at=utc_now())
        session.add_all([failed, active])
        session.commit()
        new_ids = repo.retry(session, [failed.id, active.id])
        assert len(new_ids) == 1
        retried = session.get(Task, new_ids[0])
        assert retried is not None
        assert (retried.status, retried.payload, retried.priority) == ("queued", '{"a": 1}', 2)
        assert repo.delete_finished(session, [failed.id, active.id]) == [failed.id]
        assert session.get(Task, active.id) is not None


def test_delete_skips_root_with_active_children(factory: Factory) -> None:
    with factory() as session:
        root = Task(task_type="echo", status="succeeded", created_at=utc_now())
        session.add(root)
        session.flush()
        repo.enqueue(session, "echo", {}, parent=root)
        session.commit()
        assert repo.delete_finished(session, [root.id]) == []


def test_list_roots_with_child_stats(factory: Factory) -> None:
    with factory() as session:
        root = Task(task_type="organize", status="succeeded", created_at=utc_now())
        session.add(root)
        session.flush()
        first = repo.enqueue(session, "scrape", {}, parent=root)
        repo.enqueue(session, "scrape", {}, parent=root)
        first.status = "failed"
        other = Task(task_type="organize", status="failed", created_at=utc_now())
        session.add(other)
        session.commit()
        rows, total = repo.list_roots(session, status=None, limit=50, offset=0)
        assert total == 2 and [row.id for row in rows] == [other.id, root.id]
        assert repo.child_stats(session, [root.id])[root.id] == {"failed": 1, "queued": 1}
        active, _ = repo.list_roots(session, status="active", limit=50, offset=0)
        assert [row.id for row in active] == [root.id]
        failed, _ = repo.list_roots(session, status="failed", limit=50, offset=0)
        assert {row.id for row in failed} == {root.id, other.id}


def test_find_queued_matches_type_and_payload(factory: Factory) -> None:
    first = _add(factory, task_type="organize", payload={"library_id": 1})
    with factory() as session:
        found = repo.find_queued(session, "organize", {"library_id": 1})
        assert found is not None and found.id == first
        assert repo.find_queued(session, "organize", {"library_id": 2}) is None


def test_threaded_runner_picks_up_new_task(factory: Factory, services: Services) -> None:
    done = threading.Event()

    def echo(payload: dict, ctx: TaskContext) -> TaskResult:
        done.set()
        return TaskResult()

    runner = TaskRunner(factory, services, {"echo": echo}, concurrency=2, poll_seconds=5)
    runner.start()
    try:
        _add(factory)
        runner.wake()
        assert done.wait(5)
    finally:
        runner.stop()


def test_keyed_locks_serialize_same_key() -> None:
    locks = KeyedLocks()
    inside: list[str] = []
    order: list[str] = []

    def work(name: str) -> None:
        with locks.hold("library:1"):
            inside.append(name)
            assert len(inside) == 1
            time.sleep(0.02)
            order.append(name)
            inside.remove(name)

    threads = [threading.Thread(target=work, args=(str(n),)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert sorted(order) == ["0", "1", "2", "3"]
