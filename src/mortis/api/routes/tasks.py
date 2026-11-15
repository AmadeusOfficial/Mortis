import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from mortis.api.deps import RunnerDep, ServicesDep, SessionDep
from mortis.db.models import Item, Library, Task
from mortis.tasks import repo
from mortis.tasks.logs import tail_log, task_log_path
from mortis.tasks.runner import TaskRunner

router = APIRouter(prefix="/api/tasks")

TYPE_LABELS = {
    "organize": "整理媒体库",
    "scrape": "刮削",
    "scrape_library": "刮削媒体库",
    "write_files": "写出文件",
    "rename": "重命名",
    "write_files_library": "写出整库",
    "rename_library": "重命名整库",
}


class TaskOut(BaseModel):
    id: int
    task_type: str
    type_label: str
    title: str
    status: str
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    child_count: int
    child_status: dict[str, int]
    progress: dict[str, Any] | None


class TaskDetail(TaskOut):
    payload: dict[str, Any]
    result: dict[str, Any] | None


class TaskPage(BaseModel):
    items: list[TaskOut]
    total: int


class LogOut(BaseModel):
    lines: list[str]


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["cancel", "retry", "delete"]
    task_ids: list[int]


class Affected(BaseModel):
    affected: int


def _title(session: Session, payload: dict[str, Any]) -> str:
    if "item_id" in payload:
        item = session.get(Item, payload["item_id"])
        return item.title if item is not None else f"条目 {payload['item_id']}（已删除）"
    if "library_id" in payload:
        library = session.get(Library, payload["library_id"])
        return library.name if library is not None else f"媒体库 {payload['library_id']}（已删除）"
    return ""


def _out(session: Session, task: Task, stats: dict[str, int], runner: TaskRunner) -> dict[str, Any]:
    payload = json.loads(task.payload or "{}")
    return {
        "id": task.id,
        "task_type": task.task_type,
        "type_label": TYPE_LABELS.get(task.task_type, task.task_type),
        "title": _title(session, payload),
        "status": task.status,
        "error": task.error,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "child_count": sum(stats.values()),
        "child_status": stats,
        "progress": runner.progress_of(task.id) if task.status == repo.RUNNING else None,
    }


@router.get("", response_model=TaskPage)
def list_tasks(
    session: SessionDep,
    runner: RunnerDep,
    status: Literal["active", "queued", "running", "succeeded", "failed", "cancelled"]
    | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows, total = repo.list_roots(session, status=status, limit=limit, offset=offset)
    stats = repo.child_stats(session, [row.id for row in rows])
    return {"items": [_out(session, row, stats[row.id], runner) for row in rows], "total": total}


def _get(session: Session, task_id: int) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有这个任务")
    return task


@router.get("/{task_id}", response_model=TaskDetail)
def read_task(task_id: int, session: SessionDep, runner: RunnerDep) -> dict[str, Any]:
    task = _get(session, task_id)
    stats = repo.child_stats(session, [task.id])[task.id] if task.root_task_id is None else {}
    return {
        **_out(session, task, stats, runner),
        "payload": json.loads(task.payload or "{}"),
        "result": json.loads(task.result) if task.result else None,
    }


@router.get("/{task_id}/children", response_model=list[TaskOut])
def read_children(task_id: int, session: SessionDep, runner: RunnerDep) -> list[dict[str, Any]]:
    _get(session, task_id)
    return [_out(session, child, {}, runner) for child in repo.children(session, task_id)]


@router.get("/{task_id}/log", response_model=LogOut)
def read_log(
    task_id: int,
    session: SessionDep,
    services: ServicesDep,
    limit: int = Query(default=500, ge=1, le=5000),
) -> LogOut:
    _get(session, task_id)
    return LogOut(lines=tail_log(task_log_path(services.data_dir, task_id), limit))


@router.post("/batch", response_model=Affected)
def batch(
    body: BatchRequest, session: SessionDep, runner: RunnerDep, services: ServicesDep
) -> Affected:
    if body.action == "cancel":
        affected, running = repo.cancel(session, body.task_ids)
        runner.request_cancel(running)
        return Affected(affected=affected)
    if body.action == "retry":
        created = repo.retry(session, body.task_ids)
        runner.wake()
        return Affected(affected=len(created))
    removed = repo.delete_finished(session, body.task_ids, data_dir=services.data_dir)
    return Affected(affected=len(removed))


@router.post("/clear-finished", response_model=Affected)
def clear_finished(session: SessionDep, services: ServicesDep) -> Affected:
    return Affected(affected=len(repo.clear_finished(session, data_dir=services.data_dir)))
