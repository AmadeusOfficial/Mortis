import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.db.models import DataType, Item, ItemTag, Tag, TypeField


def _read(session: Session, item: Item, field: TypeField) -> str | None:
    if field.stored_in == "items_column" and field.column_name:
        value = getattr(item, field.column_name)
    elif field.stored_in == "fields":
        value = json.loads(item.fields).get(field.field_key)
    elif field.stored_in == "tags":
        names = session.scalars(
            select(Tag.name)
            .join(ItemTag, ItemTag.tag_id == Tag.id)
            .where(ItemTag.item_id == item.id, Tag.tag_kind == field.tag_kind)
            .order_by(ItemTag.position)
        ).all()
        value = "、".join(names)
    else:
        value = None
    return str(value) if value not in (None, "") else None


def refresh_subtitle(session: Session, item: Item) -> None:
    """海报墙第二行。和标题一样就不显示。"""
    data_type = session.get(DataType, item.data_type_id)
    value = None
    if data_type is not None and data_type.subtitle_field:
        field = session.get(TypeField, (item.data_type_id, item.kind, data_type.subtitle_field))
        if field is not None:
            value = _read(session, item, field)
    item.subtitle = value if value is not None and value != item.title else None
