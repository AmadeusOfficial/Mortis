"""发布事件：给每个订阅的钩子排一个 `hook` 任务。

事件在库提交之后才发布。任务里的处理函数用 `hook_followups` 取后续任务，
放进 `TaskResult.followups`；没有 `TaskResult` 可用的地方（失败路径、接口、runner）
用 `enqueue_events` 直接入队。钩子任务自己不发布事件。
"""

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mortis.db.models import Task
from mortis.plugin_api.events import Event
from mortis.services import Services
from mortis.tasks import repo
from mortis.tasks.base import Followup, TaskContext

HOOK_TASK = "hook"
HOOK_PRIORITY = -5
"""比刮削、整理的默认优先级低，钩子不抢它们的位置。"""
QUEUE_LIMIT = 200
"""排队中的钩子任务上限，超出的事件丢弃并记日志。"""
_LOG = logging.getLogger(__name__)


def hook_payload(plugin_id: str, hook_id: str, event: Event) -> dict[str, Any]:
    return {
        "plugin_id": plugin_id,
        "hook_id": hook_id,
        "event": event.model_dump(mode="json"),
    }


def _queued(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.task_type == HOOK_TASK, Task.status == repo.QUEUED)
        )
        or 0
    )


def _payloads(services: Services, events: Iterable[Event], room: int) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in events:
        for subscriber in services.plugins.subscribers(str(event.type), event.data_type):
            if len(payloads) >= room:
                _LOG.warning("钩子队列已满（%s 个），丢弃事件 %s", QUEUE_LIMIT, event.type)
                return payloads
            payloads.append(hook_payload(subscriber.plugin_id, subscriber.hook.id, event))
    return payloads


def hook_followups(ctx: TaskContext, *events: Event) -> list[Followup]:
    """这些事件要排的钩子任务，交给 TaskResult.followups。没有订阅者时返回空列表。"""
    if not events or not ctx.services.plugins.hooks():
        return []
    with ctx.session_factory() as session:
        room = max(0, QUEUE_LIMIT - _queued(session))
    return [
        Followup(HOOK_TASK, payload, HOOK_PRIORITY)
        for payload in _payloads(ctx.services, events, room)
    ]


def enqueue_events(
    session: Session,
    services: Services,
    *events: Event,
    parent: Task | None = None,
) -> int:
    """直接入队并提交。返回排了几个任务。钩子的事不应该影响调用方，所以只记日志不抛错。"""
    if not events or not services.plugins.hooks():
        return 0
    try:
        payloads = _payloads(services, events, max(0, QUEUE_LIMIT - _queued(session)))
        for payload in payloads:
            repo.enqueue(session, HOOK_TASK, payload, priority=HOOK_PRIORITY, parent=parent)
        session.commit()
    except Exception:
        session.rollback()
        _LOG.warning("事件入队失败", exc_info=True)
        return 0
    return len(payloads)
