from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from mortis.api.deps import SessionDep
from mortis.db.models import DataType

router = APIRouter(prefix="/api")


class DataTypeOut(BaseModel):
    id: str
    name: str
    structure: str


@router.get("/data-types", response_model=list[DataTypeOut])
def list_data_types(session: SessionDep) -> list[DataTypeOut]:
    rows = session.scalars(select(DataType).where(DataType.is_enabled == 1).order_by(DataType.id))
    return [DataTypeOut(id=row.id, name=row.name, structure=row.structure) for row in rows]
