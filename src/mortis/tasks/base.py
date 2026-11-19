# 任务结果带后续任务的做法改编自 Amane（https://github.com/sqzw-x/amane，GPL-3.0）。

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from mortis.services import Services
from mortis.tasks.logs import append_log

if TYPE_CHECKING:
    from mortis.tasks.runner import TaskRunner


class TaskError(Exception):
    """给用户看的失败原因，原样记到任务的 error 里。"""


class TaskCancelled(Exception):
    pass


@dataclass
class Followup:
    task_type: str
    payload: dict[str, Any]
    priority: int = -1


@dataclass
class TaskResult:
    result: dict[str, Any] = field(default_factory=dict)
    followups: list[Followup] = field(default_factory=list)


class TaskContext:
    def __init__(
        self,
        task_id: int,
        session_factory: sessionmaker[Session],
        services: Services,
        log_path: Path,
        runner: TaskRunner,
    ) -> None:
        self.task_id = task_id
        self.session_factory = session_factory
        self.services = services
        self._log_path = log_path
        self._runner = runner

    @property
    def runner(self) -> TaskRunner:
        return self._runner

    def log(self, message: str) -> None:
        append_log(self._log_path, message)

    def progress(self, current: int, total: int, message: str = "") -> None:
        self._runner.set_progress(self.task_id, current, total, message)

    def check_cancelled(self) -> None:
        if self._runner.is_cancel_requested(self.task_id):
            raise TaskCancelled


Handler = Callable[[dict[str, Any], TaskContext], TaskResult]
