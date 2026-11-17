from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from mortis import libraries
from mortis.api.deps import RunnerDep, SessionDep, WatchDep
from mortis.db.models import Library, Schedule
from mortis.organize.watch import WatchStatus
from mortis.tasks import repo as task_repo
from mortis.tasks import schedule

router = APIRouter(prefix="/api/libraries")


class LibraryOut(BaseModel):
    id: int
    name: str
    data_type_id: str
    data_type_name: str
    automation: str
    is_auto_scrape: bool
    paths: list[str]
    item_count: int
    file_count: int
    scraped_count: int
    issue_count: int
    sample_posters: list[str]
    schedule_cron: str | None
    refresh_running: bool
    next_scan_at: str | None
    watch_state: Literal["watching", "failed"] | None
    watch_error: str | None
    created_at: str
    updated_at: str


Automation = Literal["manual", "watch", "schedule"]


class LibraryCreate(BaseModel):
    name: str
    data_type_id: str
    paths: list[str]
    automation: Automation = "manual"
    schedule_cron: str | None = None
    refresh_running: bool = False


class LibraryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    paths: list[str] | None = None
    is_auto_scrape: bool | None = None
    automation: Automation | None = None
    schedule_cron: str | None = None
    refresh_running: bool | None = None


def _out(
    library: Library,
    stats: libraries.LibraryStats,
    plans: dict[str, Schedule],
    watching: WatchStatus | None,
) -> LibraryOut:
    scan = plans.get(schedule.SCAN)
    return LibraryOut(
        id=library.id,
        name=library.name,
        data_type_id=library.data_type_id,
        data_type_name=library.data_type.name,
        automation=library.automation,
        is_auto_scrape=bool(library.is_auto_scrape),
        paths=[row.path for row in library.paths],
        item_count=stats.item_count,
        file_count=stats.file_count,
        scraped_count=stats.scraped_count,
        issue_count=stats.issue_count,
        sample_posters=stats.sample_posters,
        schedule_cron=scan.cron if scan is not None else None,
        refresh_running=schedule.REFRESH_RUNNING in plans,
        next_scan_at=scan.next_run_at if scan is not None else None,
        watch_state=watching.state if watching is not None else None,
        watch_error=watching.error if watching is not None else None,
        created_at=library.created_at,
        updated_at=library.updated_at,
    )


def _many(session: SessionDep, watcher: WatchDep, rows: list[Library]) -> list[LibraryOut]:
    ids = [library.id for library in rows]
    stats = libraries.library_stats(session, ids)
    plans = schedule.schedules_by_library(session, ids)
    return [
        _out(
            library,
            stats[library.id],
            plans[library.id],
            watcher.status(library.id) if library.automation == "watch" else None,
        )
        for library in rows
    ]


def _one(session: SessionDep, watcher: WatchDep, library: Library) -> LibraryOut:
    return _many(session, watcher, [library])[0]


@router.get("", response_model=list[LibraryOut])
def list_libraries(session: SessionDep, watcher: WatchDep) -> list[LibraryOut]:
    return _many(session, watcher, libraries.list_libraries(session))


@router.post("", response_model=LibraryOut, status_code=201)
def create_library(body: LibraryCreate, session: SessionDep, watcher: WatchDep) -> LibraryOut:
    library = libraries.create_library(
        session,
        name=body.name,
        data_type_id=body.data_type_id,
        paths=body.paths,
        automation=body.automation,
        schedule_cron=body.schedule_cron,
        refresh_running=body.refresh_running,
    )
    watcher.sync()
    return _one(session, watcher, library)


@router.get("/{library_id}", response_model=LibraryOut)
def read_library(library_id: int, session: SessionDep, watcher: WatchDep) -> LibraryOut:
    return _one(session, watcher, libraries.get_library(session, library_id))


@router.patch("/{library_id}", response_model=LibraryOut)
def update_library(
    library_id: int, body: LibraryUpdate, session: SessionDep, watcher: WatchDep
) -> LibraryOut:
    library = libraries.update_library(
        session,
        library_id,
        name=body.name,
        paths=body.paths,
        is_auto_scrape=body.is_auto_scrape,
        automation=body.automation,
        schedule_cron=body.schedule_cron,
        refresh_running=body.refresh_running,
    )
    watcher.sync()
    return _one(session, watcher, library)


class OrganizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scrape: bool | None = None


class Enqueued(BaseModel):
    task_id: int


@router.post("/{library_id}/organize", response_model=Enqueued, status_code=202)
def organize_library(
    library_id: int,
    session: SessionDep,
    runner: RunnerDep,
    body: OrganizeRequest | None = None,
) -> Enqueued:
    library = libraries.get_library(session, library_id)
    queued = task_repo.find_queued(session, "organize", "library_id", library.id)
    if queued is not None:
        return Enqueued(task_id=queued.id)
    task = task_repo.enqueue(
        session,
        "organize",
        {"library_id": library.id, "scrape": body.scrape if body is not None else None},
        priority=1,
    )
    session.commit()
    runner.wake()
    return Enqueued(task_id=task.id)


class LibraryScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


@router.post("/{library_id}/scrape", response_model=Enqueued, status_code=202)
def scrape_library(
    library_id: int,
    session: SessionDep,
    runner: RunnerDep,
    body: LibraryScrapeRequest | None = None,
) -> Enqueued:
    library = libraries.get_library(session, library_id)
    queued = task_repo.find_queued(session, "scrape_library", "library_id", library.id)
    if queued is not None:
        return Enqueued(task_id=queued.id)
    task = task_repo.enqueue(
        session,
        "scrape_library",
        {"library_id": library.id, "force": body.force if body is not None else False},
        priority=1,
    )
    session.commit()
    runner.wake()
    return Enqueued(task_id=task.id)


@router.delete("/{library_id}", status_code=204)
def delete_library(library_id: int, session: SessionDep, watcher: WatchDep) -> Response:
    libraries.delete_library(session, library_id)
    watcher.sync()
    return Response(status_code=204)
