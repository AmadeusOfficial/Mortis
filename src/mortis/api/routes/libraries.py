from fastapi import APIRouter, Response
from pydantic import BaseModel

from mortis import libraries
from mortis.api.deps import SessionDep
from mortis.db.models import Library

router = APIRouter(prefix="/api/libraries")


class LibraryOut(BaseModel):
    id: int
    name: str
    data_type_id: str
    data_type_name: str
    automation: str
    paths: list[str]
    created_at: str
    updated_at: str


class LibraryCreate(BaseModel):
    name: str
    data_type_id: str
    paths: list[str]


class LibraryUpdate(BaseModel):
    name: str | None = None
    paths: list[str] | None = None


def _out(library: Library) -> LibraryOut:
    return LibraryOut(
        id=library.id,
        name=library.name,
        data_type_id=library.data_type_id,
        data_type_name=library.data_type.name,
        automation=library.automation,
        paths=[row.path for row in library.paths],
        created_at=library.created_at,
        updated_at=library.updated_at,
    )


@router.get("", response_model=list[LibraryOut])
def list_libraries(session: SessionDep) -> list[LibraryOut]:
    return [_out(library) for library in libraries.list_libraries(session)]


@router.post("", response_model=LibraryOut, status_code=201)
def create_library(body: LibraryCreate, session: SessionDep) -> LibraryOut:
    library = libraries.create_library(
        session, name=body.name, data_type_id=body.data_type_id, paths=body.paths
    )
    return _out(library)


@router.get("/{library_id}", response_model=LibraryOut)
def read_library(library_id: int, session: SessionDep) -> LibraryOut:
    return _out(libraries.get_library(session, library_id))


@router.patch("/{library_id}", response_model=LibraryOut)
def update_library(library_id: int, body: LibraryUpdate, session: SessionDep) -> LibraryOut:
    library = libraries.update_library(session, library_id, name=body.name, paths=body.paths)
    return _out(library)


@router.delete("/{library_id}", status_code=204)
def delete_library(library_id: int, session: SessionDep) -> Response:
    libraries.delete_library(session, library_id)
    return Response(status_code=204)
