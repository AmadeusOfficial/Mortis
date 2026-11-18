from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from mortis.services import Services
from mortis.tasks.runner import TaskRunner

Result = dict[str, Any]


@dataclass(frozen=True)
class ToolContext:
    session: Session
    runner: TaskRunner
    services: Services
