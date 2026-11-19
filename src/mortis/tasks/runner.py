# 工作线程和协作式取消的做法改编自 Amane（https://github.com/sqzw-x/amane，GPL-3.0）。

import json
import logging
import threading
import traceback
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from mortis.db.models import Item, Task
from mortis.hooks.dispatch import HOOK_TASK, enqueue_events
from mortis.plugin_api.events import TaskFailed
from mortis.services import Services
from mortis.tasks import repo
from mortis.tasks.base import Handler, TaskCancelled, TaskContext, TaskError
from mortis.tasks.logs import task_log_path

HOOK_CONCURRENCY = 2
"""同时运行的钩子任务上限（所有插件合计），另外每个插件同时只跑一个。"""
_LOG = logging.getLogger(__name__)


class TaskRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        services: Services,
        handlers: dict[str, Handler],
        *,
        concurrency: int = 3,
        poll_seconds: float = 2.0,
        hook_concurrency: int = HOOK_CONCURRENCY,
    ) -> None:
        self.session_factory = session_factory
        self.services = services
        self.concurrency = concurrency
        self.hook_concurrency = hook_concurrency
        self.poll_seconds = poll_seconds
        self._handlers = dict(handlers)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._running: set[int] = set()
        self._hook_plugins: dict[int, str] = {}
        self._cancel_requested: set[int] = set()
        self._progress: dict[int, dict[str, Any]] = {}
        self._paused = False

    def register(self, task_type: str, handler: Handler) -> None:
        self._handlers[task_type] = handler

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for index in range(self.concurrency):
            thread = threading.Thread(target=self._loop, name=f"mortis-task-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            self._cancel_requested.update(self._running)
        for thread in self._threads:
            thread.join(timeout)
        self._threads.clear()

    def wake(self) -> None:
        self._wake.set()

    def pause(self) -> None:
        """暂停只让线程不再领新任务，已经在跑的照常跑完。"""
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False
        self._wake.set()

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._wake.wait(self.poll_seconds)
                # 停止时不清，不然其他还在等的线程要等满一个轮询周期才退出。
                if not self._stop.is_set():
                    self._wake.clear()

    def run_once(self) -> bool:
        # 领任务和登记「哪个插件的钩子在跑」要在同一把锁里，不然两个线程会同时领走同一个插件的钩子。
        with self._lock:
            if self._paused:
                return False
            busy = set(self._hook_plugins.values())
            full = len(self._hook_plugins) >= self.hook_concurrency
            with self.session_factory() as session:
                task = repo.claim_next(
                    session,
                    exclude_types=(HOOK_TASK,) if full else (),
                    exclude_plugins=busy,
                )
                if task is None:
                    return False
                task_id, task_type = task.id, task.task_type
                payload = json.loads(task.payload or "{}")
            if task_type == HOOK_TASK:
                self._hook_plugins[task_id] = str(payload.get("plugin_id", ""))
        self._execute(task_id, task_type, payload)
        return True

    def run_until_idle(self, limit: int = 10_000) -> None:
        """测试和一次性脚本用：在当前线程把队列跑空。"""
        for _ in range(limit):
            if not self.run_once():
                return
        raise RuntimeError("队列一直没有跑空")

    def request_cancel(self, task_ids: list[int]) -> None:
        with self._lock:
            self._cancel_requested.update(task_ids)

    def is_cancel_requested(self, task_id: int) -> bool:
        with self._lock:
            return task_id in self._cancel_requested

    def progress_of(self, task_id: int) -> dict[str, Any] | None:
        with self._lock:
            progress = self._progress.get(task_id)
            return dict(progress) if progress is not None else None

    def set_progress(self, task_id: int, current: int, total: int, message: str) -> None:
        with self._lock:
            self._progress[task_id] = {"current": current, "total": total, "message": message}

    def _execute(self, task_id: int, task_type: str, payload: dict[str, Any]) -> None:
        ctx = TaskContext(
            task_id,
            self.session_factory,
            self.services,
            task_log_path(self.services.data_dir, task_id),
            self,
        )
        with self._lock:
            self._running.add(task_id)
        handler = self._handlers.get(task_type)
        try:
            if handler is None:
                raise TaskError(f"没有处理「{task_type}」任务的程序")
            outcome = handler(payload, ctx)
        except TaskCancelled:
            ctx.log("已取消")
            self._close(task_id, repo.CANCELLED, repo.CANCELLED_MESSAGE)
        except TaskError as error:
            ctx.log(f"失败：{error}")
            message = str(error) or "任务失败"
            self._close(task_id, repo.FAILED, message)
            self._publish_failed(task_id, task_type, payload, message)
        except Exception as error:
            ctx.log("出错：\n" + traceback.format_exc())
            message = f"{type(error).__name__}: {error}"
            self._close(task_id, repo.FAILED, message)
            self._publish_failed(task_id, task_type, payload, message)
        else:
            with self.session_factory() as session:
                finished = repo.finish(session, task_id, outcome)
            ctx.log("完成" if finished else "运行中被取消，结果已丢弃")
            if finished and outcome.followups:
                self._wake.set()
        finally:
            with self._lock:
                self._running.discard(task_id)
                self._hook_plugins.pop(task_id, None)
                self._progress.pop(task_id, None)
                self._cancel_requested.discard(task_id)

    def _publish_failed(
        self, task_id: int, task_type: str, payload: dict[str, Any], error: str
    ) -> None:
        """发布 task.failed。钩子任务和静默任务（钩子写标签引起的整理）失败不发布，防止循环。"""
        if task_type == HOOK_TASK or payload.get("silent"):
            return
        if not self.services.plugins.hooks():
            return
        try:
            with self.session_factory() as session:
                item_id = payload.get("item_id")
                item = session.get(Item, item_id) if isinstance(item_id, int) else None
                event = TaskFailed(
                    data_type=item.data_type_id if item is not None else "",
                    task_id=task_id,
                    task_type=task_type,
                    item_id=item.id if item is not None else 0,
                    error=error[:500],
                )
                enqueue_events(session, self.services, event, parent=session.get(Task, task_id))
            self._wake.set()
        except Exception:
            _LOG.warning("发布 task.failed 失败", exc_info=True)

    def _close(self, task_id: int, status: str, error: str) -> None:
        with self.session_factory() as session:
            repo.close(session, task_id, status, error)
