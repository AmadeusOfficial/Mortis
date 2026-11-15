from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from mortis import libraries
from mortis.api.deps import RunnerDep, SessionDep
from mortis.db.models import Library
from mortis.tasks import repo as task_repo

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
    created_at: str
    updated_at: str


class LibraryCreate(BaseModel):
    name: str
    data_type_id: str
    paths: list[str]


class LibraryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    paths: list[str] | None = None
    is_auto_scrape: bool | None = None


def _out(library: Library, counts: tuple[int, int]) -> LibraryOut:
    return LibraryOut(
        id=library.id,
        name=library.name,
        data_type_id=library.data_type_id,
        data_type_name=library.data_type.name,
        automation=library.automation,
        is_auto_scrape=bool(library.is_auto_scrape),
        paths=[row.path for row in library.paths],
        item_count=counts[0],
        file_count=counts[1],
        created_at=library.created_at,
        updated_at=library.updated_at,
    )


def _one(session: SessionDep, library: Library) -> LibraryOut:
    return _out(library, libraries.library_counts(session, [library.id])[library.id])


@router.get("", response_model=list[LibraryOut])
def list_libraries(session: SessionDep) -> list[LibraryOut]:
    rows = libraries.list_libraries(session)
    counts = libraries.library_counts(session, [library.id for library in rows])
    return [_out(library, counts[library.id]) for library in rows]


@router.post("", response_model=LibraryOut, status_code=201)
def create_library(body: LibraryCreate, session: SessionDep) -> LibraryOut:
    library = libraries.create_library(
        session, name=body.name, data_type_id=body.data_type_id, paths=body.paths
    )
    return _one(session, library)


@router.get("/{library_id}", response_model=LibraryOut)
def read_library(library_id: int, session: SessionDep) -> LibraryOut:
    return _one(session, libraries.get_library(session, library_id))


@router.patch("/{library_id}", response_model=LibraryOut)
def update_library(library_id: int, body: LibraryUpdate, session: SessionDep) -> LibraryOut:
    library = libraries.update_library(
        session,
        library_id,
        name=body.name,
        paths=body.paths,
        is_auto_scrape=body.is_auto_scrape,
    )
    return _one(session, library)


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
def delete_library(library_id: int, session: SessionDep) -> Response:
    libraries.delete_library(session, library_id)
    return Response(status_code=204)
