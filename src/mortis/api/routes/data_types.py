from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select

from mortis.api.deps import ServicesDep, SessionDep
from mortis.db.models import DataType, SourceOrder, TypeField
from mortis.errors import UserError
from mortis.plugins.registry import seed_orders
from mortis.sources.builtin import default_orders, is_builtin_type, source_status

router = APIRouter(prefix="/api")


class DataTypeOut(BaseModel):
    id: str
    name: str
    structure: str
    plugin_id: str | None = None


class SourceRef(BaseModel):
    id: str
    name: str
    available: bool = True
    reason: str | None = None
    """不可用的原因，如「Key 未填」「还没实现」。不可用的来源照样排进顺序，刮削时跳过。"""


class OrderEntry(BaseModel):
    source: str
    excluded: bool = False


class FieldOrder(BaseModel):
    key: str
    label: str
    custom: bool
    """有自己的顺序；否则跟着整体顺序。"""
    order: list[OrderEntry]


class SourceOrders(BaseModel):
    sources: list[SourceRef]
    whole: list[OrderEntry]
    fields: list[FieldOrder]


class SourceOrdersUpdate(BaseModel):
    """键 "" 是整体顺序；字段键对应 null 表示改回跟随整体顺序。"""

    model_config = ConfigDict(extra="forbid")

    orders: dict[str, list[OrderEntry] | None]


@router.get("/data-types", response_model=list[DataTypeOut])
def list_data_types(session: SessionDep) -> list[DataTypeOut]:
    rows = session.scalars(select(DataType).where(DataType.is_enabled == 1).order_by(DataType.id))
    return [
        DataTypeOut(id=row.id, name=row.name, structure=row.structure, plugin_id=row.plugin_id)
        for row in rows
    ]


def _orderable_type(session: SessionDep, data_type_id: str) -> DataType:
    row = session.get(DataType, data_type_id)
    if row is None or (row.plugin_id is None and not is_builtin_type(row.id)):
        raise UserError("这个数据类型没有来源顺序", status_code=404)
    return row


def _source_refs(services: ServicesDep, data_type_id: str) -> list[SourceRef]:
    """内置类型列内置来源（含不可用的），插件类型列启用的来源插件。"""
    if not is_builtin_type(data_type_id):
        return [
            SourceRef(id=plugin.id, name=plugin.name)
            for plugin in services.plugins.sources(data_type_id)
        ]
    return [
        SourceRef(id=source.id, name=source.name, available=reason is None, reason=reason)
        for source, reason in source_status(services, data_type_id)
    ]


def _entries(rows: list[SourceOrder], sources: list[str]) -> list[OrderEntry]:
    listed = [OrderEntry(source=row.source, excluded=bool(row.is_excluded)) for row in rows]
    known = {entry.source for entry in listed}
    return listed + [OrderEntry(source=source) for source in sources if source not in known]


def _read(session: SessionDep, services: ServicesDep, data_type_id: str) -> SourceOrders:
    refs = _source_refs(services, data_type_id)
    ids = [ref.id for ref in refs]
    rows = session.scalars(
        select(SourceOrder)
        .where(SourceOrder.data_type_id == data_type_id)
        .order_by(SourceOrder.field_key, SourceOrder.position)
    ).all()
    grouped: dict[str, list[SourceOrder]] = {}
    for row in rows:
        grouped.setdefault(row.field_key, []).append(row)
    whole = _entries(grouped.get("", []), ids)
    fields = session.scalars(
        select(TypeField)
        .where(TypeField.data_type_id == data_type_id, TypeField.applies_to == "work")
        .order_by(TypeField.position)
    ).all()
    return SourceOrders(
        sources=refs,
        whole=whole,
        fields=[
            FieldOrder(
                key=field.field_key,
                label=field.label,
                custom=field.field_key in grouped,
                order=_entries(grouped[field.field_key], [e.source for e in whole])
                if field.field_key in grouped
                else whole,
            )
            for field in fields
        ],
    )


@router.get("/data-types/{data_type_id}/source-orders", response_model=SourceOrders)
def read_source_orders(
    data_type_id: str, session: SessionDep, services: ServicesDep
) -> SourceOrders:
    _orderable_type(session, data_type_id)
    return _read(session, services, data_type_id)


@router.put("/data-types/{data_type_id}/source-orders", response_model=SourceOrders)
def write_source_orders(
    data_type_id: str, body: SourceOrdersUpdate, session: SessionDep, services: ServicesDep
) -> SourceOrders:
    _orderable_type(session, data_type_id)
    keys = set(
        session.scalars(select(TypeField.field_key).where(TypeField.data_type_id == data_type_id))
    )
    unknown = sorted(key for key in body.orders if key and key not in keys)
    if unknown:
        raise UserError(f"没有这些字段：{'、'.join(unknown)}")
    with services.locks.hold("catalog-write"):
        for key, entries in body.orders.items():
            session.execute(
                delete(SourceOrder).where(
                    SourceOrder.data_type_id == data_type_id, SourceOrder.field_key == key
                )
            )
            seen: set[str] = set()
            for position, entry in enumerate(entries or []):
                if entry.source in seen:
                    continue
                seen.add(entry.source)
                session.add(
                    SourceOrder(
                        data_type_id=data_type_id,
                        field_key=key,
                        source=entry.source,
                        position=position,
                        is_excluded=int(entry.excluded),
                    )
                )
        session.commit()
    return _read(session, services, data_type_id)


@router.post("/data-types/{data_type_id}/source-orders/reset", response_model=SourceOrders)
def reset_source_orders(
    data_type_id: str, session: SessionDep, services: ServicesDep
) -> SourceOrders:
    row = _orderable_type(session, data_type_id)
    if row.plugin_id is None:
        defaults = default_orders(row.id)
    else:
        info = services.plugins.get(row.plugin_id)
        plugin = info.plugin if info is not None else None
        defaults = getattr(plugin, "default_orders", None)
        if defaults is None:
            raise UserError("数据类型插件没有启用，取不到默认顺序")
    with services.locks.hold("catalog-write"):
        session.execute(delete(SourceOrder).where(SourceOrder.data_type_id == data_type_id))
        seed_orders(session, data_type_id, defaults)
        session.commit()
    return _read(session, services, data_type_id)
