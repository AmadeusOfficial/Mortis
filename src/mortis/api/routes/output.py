from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis import libraries
from mortis.api.deps import RunnerDep, ServicesDep, SessionDep
from mortis.api.routes.items import Enqueued, get_item
from mortis.config.models import MovieScrapeConfig
from mortis.db.models import Item, MediaFile
from mortis.items.edit import ItemError
from mortis.output.service import (
    arrange_steps,
    arrangeable_items,
    enqueue_arrange,
    has_video,
    is_scraped,
)
from mortis.rename.plan import RenamePlan, plan_rename
from mortis.tasks import repo as task_repo

router = APIRouter(prefix="/api")


class MoveOut(BaseModel):
    source: str
    target: str


class ArrangePreview(BaseModel):
    item_id: int
    title: str
    rename: bool
    write: bool
    folder: str
    folder_from: str | None
    folder_to: str | None
    make_folder: bool
    moves: list[MoveOut]
    problems: list[str]
    changes: bool


class LibraryArrangePreview(BaseModel):
    count: int
    rename: bool
    write: bool
    previews: list[ArrangePreview]


def _arrangeable(session: Session, item_id: int) -> Item:
    item = get_item(session, item_id)
    if item.kind != "work":
        raise ItemError("系列电影没有自己的文件")
    if not has_video(session, item.id):
        raise ItemError("这个条目没有视频文件")
    if not is_scraped(session, item.id):
        raise ItemError("这个条目还没刮削，先刮削再整理")
    return item


def _video_folder(session: Session, item: Item) -> str:
    path = session.scalar(
        select(MediaFile.path)
        .where(MediaFile.item_id == item.id, MediaFile.file_kind == "video")
        .order_by(MediaFile.path)
    )
    return str(Path(path).parent) if path else ""


def _preview(session: Session, item: Item, config: MovieScrapeConfig) -> ArrangePreview:
    rename, write = arrange_steps(session, item.id, config, {})
    plan = plan_rename(session, item, config.output) if rename else RenamePlan(item.id)
    return ArrangePreview(
        item_id=item.id,
        title=item.title,
        rename=rename,
        write=write,
        folder=plan.folder_to or _video_folder(session, item),
        folder_from=plan.folder_from,
        folder_to=plan.folder_to,
        make_folder=plan.make_folder,
        moves=[
            MoveOut(source=m.source, target=m.target) for m in plan.moves if m.source != m.target
        ],
        problems=plan.problems,
        changes=plan.changes,
    )


@router.get("/items/{item_id}/arrange-preview", response_model=ArrangePreview)
def preview_item(item_id: int, session: SessionDep, services: ServicesDep) -> ArrangePreview:
    item = _arrangeable(session, item_id)
    return _preview(session, item, services.config_store.load_config().movie)


@router.post("/items/{item_id}/arrange", response_model=Enqueued, status_code=202)
def arrange_item(
    item_id: int, session: SessionDep, runner: RunnerDep, services: ServicesDep
) -> Enqueued:
    item = _arrangeable(session, item_id)
    preview = _preview(session, item, services.config_store.load_config().movie)
    if preview.problems:
        raise ItemError("；".join(preview.problems))
    return Enqueued(task_id=enqueue_arrange(session, runner, item.id))


@router.get("/libraries/{library_id}/arrange-preview", response_model=LibraryArrangePreview)
def preview_library(
    library_id: int, session: SessionDep, services: ServicesDep
) -> LibraryArrangePreview:
    library = libraries.get_library(session, library_id)
    config = services.config_store.load_config().movie
    ids = arrangeable_items(session, library.id)
    rename, write = arrange_steps(session, -1, config, {})
    previews = []
    if rename:
        for item in session.scalars(select(Item).where(Item.id.in_(ids)).order_by(Item.id)):
            preview = _preview(session, item, config)
            if preview.changes or preview.problems:
                previews.append(preview)
    return LibraryArrangePreview(count=len(ids), rename=rename, write=write, previews=previews)


@router.post("/libraries/{library_id}/arrange", response_model=Enqueued, status_code=202)
def arrange_library(library_id: int, session: SessionDep, runner: RunnerDep) -> Enqueued:
    library = libraries.get_library(session, library_id)
    queued = task_repo.find_queued(session, "arrange_library", "library_id", library.id)
    if queued is not None:
        return Enqueued(task_id=queued.id)
    task = task_repo.enqueue(session, "arrange_library", {"library_id": library.id}, priority=1)
    session.commit()
    runner.wake()
    return Enqueued(task_id=task.id)
