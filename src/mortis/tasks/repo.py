# 队列的抢占和完成写法改编自 Amane（https://github.com/sqzw-x/amane，GPL-3.0）。

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, delete, exists, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from mortis.db.models import Task, utc_now
from mortis.tasks.base import TaskResult
from mortis.tasks.logs import remove_logs

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
ACTIVE = (QUEUED, RUNNING)
FINISHED = (SUCCEEDED, FAILED, CANCELLED)

CANCELLED_MESSAGE = "已取消"
RESTART_MESSAGE = "程序重启时中断"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def enqueue(
    session: Session,
    task_type: str,
    payload: dict[str, Any],
    *,
    priority: int = 0,
    parent: Task | None = None,
) -> Task:
    """只 flush 不提交，调用方决定事务。"""
    task = Task(
        task_type=task_type,
        status=QUEUED,
        priority=priority,
        payload=_dump(payload),
        created_at=utc_now(),
    )
    if parent is not None:
        task.parent_task_id = parent.id
        task.root_task_id = parent.root_task_id or parent.id
    session.add(task)
    session.flush()
    return task


def find_queued(session: Session, task_type: str, key: str, value: Any) -> Task | None:
    """找一个同类型、payload 里某个键相同、还在排队的任务。"""
    for task in session.scalars(
        select(Task).where(Task.task_type == task_type, Task.status == QUEUED).order_by(Task.id)
    ):
        if json.loads(task.payload or "{}").get(key) == value:
            return task
    return None


def claim_next(session: Session) -> Task | None:
    """乐观抢占：多个线程同时抢同一行时只有一个能把 queued 改成 running。"""
    for _ in range(5):
        task_id = session.scalar(
            select(Task.id)
            .where(Task.status == QUEUED)
            .order_by(Task.priority.desc(), Task.id)
            .limit(1)
        )
        if task_id is None:
            return None
        claimed = session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == QUEUED)
            .values(status=RUNNING, started_at=utc_now())
        ).rowcount
        session.commit()
        if claimed:
            return session.get(Task, task_id)
    return None


def finish(session: Session, task_id: int, outcome: TaskResult) -> bool:
    """标记完成并插入后续任务，同一个事务。任务已不在运行（被取消）就什么都不做。"""
    done = session.execute(
        update(Task)
        .where(Task.id == task_id, Task.status == RUNNING)
        .values(status=SUCCEEDED, result=_dump(outcome.result), finished_at=utc_now())
    ).rowcount
    if not done:
        session.rollback()
        return False
    parent = session.get(Task, task_id)
    for followup in outcome.followups:
        enqueue(
            session,
            followup.task_type,
            followup.payload,
            priority=followup.priority,
            parent=parent,
        )
    session.commit()
    return True


def close(session: Session, task_id: int, status: str, error: str) -> bool:
    closed = session.execute(
        update(Task)
        .where(Task.id == task_id, Task.status == RUNNING)
        .values(status=status, error=error, finished_at=utc_now())
    ).rowcount
    session.commit()
    return bool(closed)


def cancel(session: Session, task_ids: Sequence[int]) -> tuple[int, list[int]]:
    """取消这些任务和它们还没结束的子孙。返回（取消了几个，其中正在运行的编号）。"""
    ids = list(task_ids)
    rows = session.execute(
        select(Task.id, Task.status).where(
            or_(Task.id.in_(ids), Task.root_task_id.in_(ids)), Task.status.in_(ACTIVE)
        )
    ).all()
    if rows:
        session.execute(
            update(Task)
            .where(Task.id.in_([row.id for row in rows]), Task.status.in_(ACTIVE))
            .values(status=CANCELLED, error=CANCELLED_MESSAGE, finished_at=utc_now())
        )
    session.commit()
    return len(rows), [row.id for row in rows if row.status == RUNNING]


def retry(session: Session, task_ids: Sequence[int]) -> list[int]:
    """失败或取消的任务按原样再排一个新的根任务。"""
    old = session.scalars(
        select(Task).where(Task.id.in_(list(task_ids)), Task.status.in_((FAILED, CANCELLED)))
    ).all()
    created = []
    for task in old:
        new = Task(
            task_type=task.task_type,
            status=QUEUED,
            priority=task.priority,
            payload=task.payload,
            created_at=utc_now(),
        )
        session.add(new)
        session.flush()
        created.append(new.id)
    session.commit()
    return created


def _no_active_descendant() -> ColumnElement[bool]:
    descendant = aliased(Task)
    return ~exists().where(descendant.root_task_id == Task.id, descendant.status.in_(ACTIVE))


def _delete(session: Session, query: Select[tuple[int]], data_dir: Path | None) -> list[int]:
    """删掉查到的任务；子孙顺着 parent_task_id 级联。返回查到的编号。"""
    targets = list(session.scalars(query))
    if not targets:
        return []
    removed = list(
        session.scalars(
            select(Task.id).where(
                or_(
                    Task.id.in_(targets),
                    Task.root_task_id.in_(targets),
                    Task.parent_task_id.in_(targets),
                )
            )
        )
    )
    session.execute(delete(Task).where(Task.id.in_(targets)))
    session.commit()
    if data_dir is not None:
        remove_logs(data_dir, removed)
    return targets


def delete_finished(
    session: Session, task_ids: Sequence[int], *, data_dir: Path | None = None
) -> list[int]:
    """删除已结束、且没有还在跑的子孙的任务。"""
    query = select(Task.id).where(
        Task.id.in_(list(task_ids)), Task.status.in_(FINISHED), _no_active_descendant()
    )
    return _delete(session, query, data_dir)


def clear_finished(session: Session, *, data_dir: Path | None = None) -> list[int]:
    query = select(Task.id).where(
        Task.parent_task_id.is_(None), Task.status.in_(FINISHED), _no_active_descendant()
    )
    return _delete(session, query, data_dir)


def recover_running(session: Session) -> int:
    count = session.execute(
        update(Task)
        .where(Task.status == RUNNING)
        .values(status=FAILED, error=RESTART_MESSAGE, finished_at=utc_now())
    ).rowcount
    session.commit()
    return count


def prune(session: Session, *, keep_days: int, data_dir: Path | None = None) -> list[int]:
    cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).isoformat(timespec="seconds")
    query = select(Task.id).where(
        Task.parent_task_id.is_(None),
        Task.status.in_(FINISHED),
        Task.finished_at < cutoff,
        _no_active_descendant(),
    )
    return _delete(session, query, data_dir)


def _status_filter(status: str | None) -> ColumnElement[bool] | None:
    if status is None:
        return None
    statuses = ACTIVE if status == "active" else (status,)
    descendant = aliased(Task)
    return or_(
        Task.status.in_(statuses),
        exists().where(descendant.root_task_id == Task.id, descendant.status.in_(statuses)),
    )


def list_roots(
    session: Session, *, status: str | None, limit: int, offset: int
) -> tuple[list[Task], int]:
    conditions: list[ColumnElement[bool]] = [Task.parent_task_id.is_(None)]
    extra = _status_filter(status)
    if extra is not None:
        conditions.append(extra)
    where = and_(*conditions)
    total = session.scalar(select(func.count()).select_from(Task).where(where)) or 0
    rows = session.scalars(
        select(Task).where(where).order_by(Task.id.desc()).limit(limit).offset(offset)
    ).all()
    return list(rows), total


def child_stats(session: Session, root_ids: Sequence[int]) -> dict[int, dict[str, int]]:
    stats: dict[int, dict[str, int]] = {root: {} for root in root_ids}
    rows = session.execute(
        select(Task.root_task_id, Task.status, func.count())
        .where(Task.root_task_id.in_(list(root_ids)))
        .group_by(Task.root_task_id, Task.status)
    ).all()
    for root, status, count in rows:
        stats[root][status] = count
    return stats


def children(session: Session, task_id: int) -> list[Task]:
    return list(
        session.scalars(select(Task).where(Task.parent_task_id == task_id).order_by(Task.id))
    )
