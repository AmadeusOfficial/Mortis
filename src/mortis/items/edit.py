import json
import re
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from mortis.db.models import Item, TypeField, utc_now
from mortis.errors import UserError
from mortis.items.detail import is_editable
from mortis.items.subtitle import refresh_subtitle
from mortis.scrape.apply import level_fields, write_tags

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MANUAL = "manual"


class ItemError(UserError):
    pass


def _coerce(field: TypeField, raw: Any) -> Any:
    label = field.label
    if field.stored_in == "tags":
        if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
            raise ItemError(f"「{label}」要填一组文字")
        return list(dict.fromkeys(v.strip() for v in raw if v.strip()))
    if raw is None:
        value = None
    elif field.value_type == "number":
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise ItemError(f"「{label}」要填数字")
        value = int(raw) if float(raw).is_integer() else float(raw)
    elif isinstance(raw, str):
        value = raw.strip() or None
    else:
        raise ItemError(f"「{label}」要填文字")
    if field.value_type == "date" and value is not None:
        try:
            if not _DATE.fullmatch(value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            raise ItemError(f"「{label}」日期要写成 1999-03-30 这样") from None
    if field.stored_in == "items_column" and field.column_name == "title" and value is None:
        raise ItemError("片名不能为空")
    return value


def edit_fields(session: Session, item: Item, values: dict[str, Any]) -> None:
    fields = {f.field_key: f for f in level_fields(session, item.data_type_id, item.kind)}
    prepared: list[tuple[TypeField, Any]] = []
    for key, raw in values.items():
        field = fields.get(key)
        if field is None:
            raise ItemError(f"没有这个字段：{key}")
        if not is_editable(field):
            raise ItemError(f"「{field.label}」不能手动修改")
        prepared.append((field, _coerce(field, raw)))
    extra = json.loads(item.fields)
    sources = json.loads(item.field_sources)
    locked = json.loads(item.locked_fields)
    for field, value in prepared:
        if field.stored_in == "items_column":
            setattr(item, field.column_name or field.field_key, value)
        elif field.stored_in == "fields":
            if value is None:
                extra.pop(field.field_key, None)
            else:
                extra[field.field_key] = value
        else:
            write_tags(session, item, field.tag_kind or field.field_key, value)
        sources[field.field_key] = MANUAL
        if field.is_lockable and field.field_key not in locked:
            locked.append(field.field_key)
    item.fields = json.dumps(extra, ensure_ascii=False)
    item.field_sources = json.dumps(sources, ensure_ascii=False)
    item.locked_fields = json.dumps(locked, ensure_ascii=False)
    item.updated_at = utc_now()
    refresh_subtitle(session, item)
    session.commit()


def set_locks(session: Session, item: Item, keys: list[str]) -> None:
    fields = level_fields(session, item.data_type_id, item.kind)
    lockable = [field.field_key for field in fields if field.is_lockable]
    unknown = [key for key in keys if key not in lockable]
    if unknown:
        raise ItemError(f"这些字段不能锁定：{'、'.join(unknown)}")
    wanted = set(keys)
    item.locked_fields = json.dumps([key for key in lockable if key in wanted], ensure_ascii=False)
    item.updated_at = utc_now()
    session.commit()
