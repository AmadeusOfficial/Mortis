from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from mortis.api.deps import RunnerDep, SessionDep
from mortis.db.models import Item
from mortis.tasks import repo as task_repo
from mortis.tasks.runner import TaskRunner

router = APIRouter(prefix="/api")


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = True


class Enqueued(BaseModel):
    task_id: int


def get_item(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="没有这个条目")
    return item


def enqueue_scrape(session: Session, runner: TaskRunner, item_id: int, *, force: bool) -> int:
    queued = task_repo.find_queued(session, "scrape", "item_id", item_id)
    if queued is not None:
        return queued.id
    task = task_repo.enqueue(session, "scrape", {"item_id": item_id, "force": force}, priority=1)
    session.commit()
    runner.wake()
    return task.id


@router.post("/items/{item_id}/scrape", response_model=Enqueued, status_code=202)
def scrape_item(
    item_id: int, session: SessionDep, runner: RunnerDep, body: ScrapeRequest | None = None
) -> Enqueued:
    item = get_item(session, item_id)
    if item.kind != "work":
        raise HTTPException(status_code=422, detail="系列电影跟着其中的电影一起刮削")
    force = body.force if body is not None else True
    return Enqueued(task_id=enqueue_scrape(session, runner, item.id, force=force))
