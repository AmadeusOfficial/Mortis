"""覆盖升级前按插件暂停：暂停期间该插件的新钩子任务不派发，已在跑的等它结束，失败路径也要恢复。"""

import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mortis.errors import UserError
from mortis.hooks.dispatch import HOOK_TASK
from mortis.tasks.base import TaskContext, TaskResult
from mortis.tasks.runner import TaskRunner
from tests.test_hooks_dispatch import _enqueue_hook, movie_app, tasks
from tests.test_plugin_packaging import Env, make_zip, plugin_files


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def make_runner(tmp_path: Path) -> tuple[Any, TaskRunner, list[str]]:
    app = movie_app(tmp_path, {})
    runner: TaskRunner = app.state.task_runner
    seen: list[str] = []

    def handler(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
        seen.append(payload["plugin_id"])
        return TaskResult()

    runner.register(HOOK_TASK, handler)
    return app, runner, seen


def test_paused_plugin_hook_not_dispatched_others_unaffected(tmp_path: Path) -> None:
    app, runner, seen = make_runner(tmp_path)
    for plugin in ("a", "b"):
        _enqueue_hook(app, plugin, "h")
    with runner.pause_plugin("a"):
        runner.run_until_idle()
        assert seen == ["b"]
        assert [t.status for t in tasks(app, HOOK_TASK)] == ["queued", "succeeded"]
    runner.run_until_idle()  # 恢复后排着的任务照常派发
    assert seen == ["b", "a"]


def test_pause_waits_for_running_hook_then_proceeds(tmp_path: Path) -> None:
    app, runner, _ = make_runner(tmp_path)
    started, release = threading.Event(), threading.Event()

    def slow(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
        started.set()
        release.wait(5)
        return TaskResult()

    runner.register(HOOK_TASK, slow)
    _enqueue_hook(app, "a", "h")
    worker = threading.Thread(target=runner.run_once)
    worker.start()
    assert started.wait(5)
    threading.Timer(0.2, release.set).start()
    begin = time.monotonic()
    with runner.pause_plugin("a", timeout=5) as drained:
        assert drained is True
        assert time.monotonic() - begin >= 0.15  # 等到旧任务结束才进来
        assert tasks(app, HOOK_TASK)[0].status == "succeeded"
    worker.join(5)


def test_pause_gives_up_after_timeout_and_still_resumes(tmp_path: Path) -> None:
    app, runner, seen = make_runner(tmp_path)
    runner._hook_plugins[900] = "a"  # 假装有一个一直不结束的钩子任务
    _enqueue_hook(app, "a", "h")
    with runner.pause_plugin("a", timeout=0.1) as drained:
        assert drained is False
        assert runner.run_once() is False
    runner._hook_plugins.clear()
    assert runner.run_once() and seen == ["a"]


def test_pause_is_released_when_body_raises(tmp_path: Path) -> None:
    app, runner, seen = make_runner(tmp_path)
    _enqueue_hook(app, "a", "h")
    with pytest.raises(RuntimeError), runner.pause_plugin("a"):
        raise RuntimeError("boom")
    assert runner.run_once() and seen == ["a"]


def test_nested_pauses_count(tmp_path: Path) -> None:
    app, runner, seen = make_runner(tmp_path)
    _enqueue_hook(app, "a", "h")
    with runner.pause_plugin("a"):
        with runner.pause_plugin("a"):
            pass
        assert runner.run_once() is False  # 外层还没放开
    assert runner.run_once() and seen == ["a"]


# ---- 安装器 ----


class Spy:
    def __init__(self) -> None:
        self.events: list[str] = []

    @contextmanager
    def pause(self, plugin_id: str):  # type: ignore[no-untyped-def]
        self.events.append(f"pause:{plugin_id}")
        try:
            yield True
        finally:
            self.events.append(f"resume:{plugin_id}")


def with_spy(env: Env) -> Spy:
    spy = Spy()
    env.installer.pause = spy.pause
    return spy


def test_overwrite_pauses_plugin_and_resumes(env: Env) -> None:
    env.installer.confirm(env.check(make_zip(plugin_files(version="1.0"))).token)
    spy = with_spy(env)
    env.installer.confirm(env.check(make_zip(plugin_files(version="2.0"))).token)
    assert spy.events == ["pause:me.hello", "resume:me.hello"]


def test_fresh_install_does_not_pause(env: Env) -> None:
    spy = with_spy(env)
    env.installer.confirm(env.check(make_zip(plugin_files())).token)
    assert spy.events == []


def test_overwrite_failure_still_resumes(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from mortis.plugins import packaging

    env.installer.confirm(env.check(make_zip(plugin_files(version="1.0"))).token)
    spy = with_spy(env)
    preview = env.check(make_zip(plugin_files(version="2.0")))

    def boom(source: Path, destination: Path) -> None:
        raise OSError("占用中")

    monkeypatch.setattr(packaging, "_move", boom)
    with pytest.raises(UserError):
        env.installer.confirm(preview.token)
    assert spy.events == ["pause:me.hello", "resume:me.hello"]


def test_uninstall_pauses_and_resumes(env: Env) -> None:
    env.installer.confirm(env.check(make_zip(plugin_files())).token)
    spy = with_spy(env)
    env.installer.uninstall("me.hello")
    assert spy.events == ["pause:me.hello", "resume:me.hello"]


def test_http_installer_is_wired_to_task_runner(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post("/api/plugins/import/check", content=b"not a zip")  # 触发安装器创建
        assert app.state.plugin_installer.pause == app.state.task_runner.pause_plugin
