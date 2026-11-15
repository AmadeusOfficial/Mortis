from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis import libraries
from mortis.api.deps import RunnerDep, ServicesDep, SessionDep
from mortis.api.routes.items import Enqueued, get_item
from mortis.db.models import Item
from mortis.items.edit import ItemError
from mortis.output.service import has_video
from mortis.rename.plan import RenamePlan, plan_rename
from mortis.tasks import repo as task_repo
from mortis.tasks.runner import TaskRunner

router = APIRouter(prefix="/api")


class MoveOut(BaseModel):
    source: str
    target: str


class RenamePreview(BaseModel):
    item_id: int
    title: str
    folder_from: str | None
    folder_to: str | None
    make_folder: bool
    moves: list[MoveOut]
    problems: list[str]
    changes: bool


def _enqueue(
    session: Session, runner: TaskRunner, task_type: str, key: str, value: int, **extra: object
) -> Enqueued:
    queued = task_repo.find_queued(session, task_type, key, value)
    if queued is not None:
        return Enqueued(task_id=queued.id)
    task = task_repo.enqueue(session, task_type, {key: value, **extra}, priority=1)
    session.commit()
    runner.wake()
    return Enqueued(task_id=task.id)


def _work_with_video(session: Session, item_id: int) -> Item:
    item = get_item(session, item_id)
    if item.kind != "work":
        raise ItemError("系列电影没有自己的文件")
    if not has_video(session, item.id):
        raise ItemError("这个条目没有视频文件")
    return item


def _preview(item: Item, plan: RenamePlan) -> RenamePreview:
    return RenamePreview(
        item_id=item.id,
        title=item.title,
        folder_from=plan.folder_from,
        folder_to=plan.folder_to,
        make_folder=plan.make_folder,
        moves=[
            MoveOut(source=m.source, target=m.target) for m in plan.moves if m.source != m.target
        ],
        problems=plan.problems,
        changes=plan.changes,
    )


@router.post("/items/{item_id}/write-files", response_model=Enqueued, status_code=202)
def write_item(item_id: int, session: SessionDep, runner: RunnerDep) -> Enqueued:
    item = _work_with_video(session, item_id)
    return _enqueue(session, runner, "write_files", "item_id", item.id)


@router.get("/items/{item_id}/rename-preview", response_model=RenamePreview)
def preview_item(item_id: int, session: SessionDep, services: ServicesDep) -> RenamePreview:
    item = _work_with_video(session, item_id)
    output = services.config_store.load_config().movie.output
    return _preview(item, plan_rename(session, item, output))


@router.post("/items/{item_id}/rename", response_model=Enqueued, status_code=202)
def rename_item(
    item_id: int, session: SessionDep, runner: RunnerDep, services: ServicesDep
) -> Enqueued:
    item = _work_with_video(session, item_id)
    plan = plan_rename(session, item, services.config_store.load_config().movie.output)
    if plan.problems:
        raise ItemError("；".join(plan.problems))
    return _enqueue(session, runner, "rename", "item_id", item.id)


@router.post("/libraries/{library_id}/write-files", response_model=Enqueued, status_code=202)
def write_library(library_id: int, session: SessionDep, runner: RunnerDep) -> Enqueued:
    library = libraries.get_library(session, library_id)
    return _enqueue(session, runner, "write_files_library", "library_id", library.id)


@router.get("/libraries/{library_id}/rename-preview", response_model=list[RenamePreview])
def preview_library(
    library_id: int, session: SessionDep, services: ServicesDep
) -> list[RenamePreview]:
    library = libraries.get_library(session, library_id)
    output = services.config_store.load_config().movie.output
    previews = []
    for item in session.scalars(
        select(Item).where(Item.library_id == library.id, Item.kind == "work").order_by(Item.id)
    ):
        if not has_video(session, item.id):
            continue
        preview = _preview(item, plan_rename(session, item, output))
        if preview.changes or preview.problems:
            previews.append(preview)
    return previews


@router.post("/libraries/{library_id}/rename", response_model=Enqueued, status_code=202)
def rename_library(library_id: int, session: SessionDep, runner: RunnerDep) -> Enqueued:
    library = libraries.get_library(session, library_id)
    return _enqueue(session, runner, "rename_library", "library_id", library.id)
