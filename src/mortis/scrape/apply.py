import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mortis.db.models import (
    Item,
    ItemImage,
    ItemPerson,
    ItemRating,
    ItemTag,
    Person,
    PersonExternalId,
    SourceOrder,
    Tag,
    TypeField,
    utc_now,
)
from mortis.items.subtitle import refresh_subtitle
from mortis.scrape.common import person_key

EMPTY: tuple[Any, ...] = (None, "", [], {})
DEFAULT_ORDER = ["tmdb"]


@dataclass(frozen=True)
class Pick:
    source: str
    value: Any


def level_fields(session: Session, data_type_id: str, level: str) -> list[TypeField]:
    return list(
        session.scalars(
            select(TypeField)
            .where(TypeField.data_type_id == data_type_id, TypeField.applies_to == level)
            .order_by(TypeField.position)
        )
    )


def source_order(session: Session, data_type_id: str) -> list[str]:
    rows = session.scalars(
        select(SourceOrder.source)
        .where(
            SourceOrder.data_type_id == data_type_id,
            SourceOrder.field_key == "",
            SourceOrder.is_excluded == 0,
        )
        .order_by(SourceOrder.position)
    ).all()
    return list(rows) or list(DEFAULT_ORDER)


def field_orders(session: Session, data_type_id: str, sources: list[str]) -> dict[str, list[str]]:
    """每个字段的来源顺序，键 "" 是整体顺序。

    没写进顺序的已启用来源排在最后；字段行里排除的来源这个字段不用。
    """
    rows = session.scalars(
        select(SourceOrder)
        .where(SourceOrder.data_type_id == data_type_id)
        .order_by(SourceOrder.field_key, SourceOrder.position)
    ).all()
    grouped: dict[str, list[SourceOrder]] = {}
    for row in rows:
        grouped.setdefault(row.field_key, []).append(row)
    whole_rows = grouped.pop("", [])
    whole = [row.source for row in whole_rows if not row.is_excluded]
    dropped = {row.source for row in whole_rows if row.is_excluded}
    whole += [source for source in sources if source not in whole and source not in dropped]
    orders = {"": whole}
    for key, field_rows in grouped.items():
        listed = [row.source for row in field_rows if not row.is_excluded]
        excluded = {row.source for row in field_rows if row.is_excluded}
        orders[key] = listed + [s for s in whole if s not in listed and s not in excluded]
    return orders


def plan_values(
    fields: Iterable[TypeField],
    candidates: Mapping[str, Mapping[str, Any]],
    order: list[str] | Mapping[str, list[str]],
    skip: set[str],
) -> dict[str, Pick | None]:
    """跳过的字段不出现；各来源都没值的字段是 None，表示要清空。

    order 是整体顺序，或 field_orders 给出的按字段顺序。评分字段合并各来源，其余取第一个有值的。
    """
    plan: dict[str, Pick | None] = {}
    for field in fields:
        key = field.field_key
        if key in skip:
            continue
        sources = order if isinstance(order, list) else order.get(key, order[""])
        if field.value_type == "rating":
            plan[key] = _merge_ratings(key, sources, candidates)
            continue
        plan[key] = next(
            (
                Pick(source, candidates[source][key])
                for source in sources
                if candidates.get(source, {}).get(key) not in EMPTY
            ),
            None,
        )
    return plan


def _merge_ratings(
    key: str, sources: list[str], candidates: Mapping[str, Mapping[str, Any]]
) -> Pick | None:
    """按来源顺序合并评分；同一个评分来源（如 imdb）只留排在前面的那条。"""
    merged: list[Any] = []
    seen: set[str] = set()
    first: str | None = None
    for source in sources:
        ratings = candidates.get(source, {}).get(key)
        if ratings in EMPTY:
            continue
        first = first or source
        for rating in ratings:
            if rating["source"] not in seen:
                seen.add(rating["source"])
                merged.append(rating)
    return Pick(first, merged) if first is not None else None


def image_urls(
    fields: Iterable[TypeField], candidates: Mapping[str, Mapping[str, Any]], skip: set[str]
) -> list[str]:
    """每种没跳过的图，各来源排第一的那张都先下载；进锁后再按来源顺序选。"""
    urls: list[str] = []
    for field in fields:
        if field.value_type != "images" or field.field_key in skip:
            continue
        for values in candidates.values():
            images = values.get(field.field_key) or []
            if images and images[0]["url"] not in urls:
                urls.append(images[0]["url"])
    return urls


def write_tags(session: Session, item: Item, tag_kind: str, names: list[str]) -> None:
    session.execute(
        delete(ItemTag).where(
            ItemTag.item_id == item.id,
            ItemTag.tag_id.in_(select(Tag.id).where(Tag.tag_kind == tag_kind)),
        )
    )
    for position, name in enumerate(dict.fromkeys(names)):
        tag = session.scalar(select(Tag).where(Tag.tag_kind == tag_kind, Tag.name == name))
        if tag is None:
            tag = Tag(tag_kind=tag_kind, name=name)
            session.add(tag)
            session.flush()
        session.add(ItemTag(item_id=item.id, tag_id=tag.id, position=position))


def _person_for(session: Session, entry: Mapping[str, Any], files: Mapping[str, str]) -> Person:
    now = utc_now()
    provider, external_id = person_key(dict(entry))
    person = session.scalar(
        select(Person)
        .join(PersonExternalId, PersonExternalId.person_id == Person.id)
        .where(
            PersonExternalId.provider == provider,
            PersonExternalId.external_id == external_id,
        )
    )
    if person is None:
        person = Person(
            name=entry["name"],
            original_name=entry.get("original_name"),
            created_at=now,
            updated_at=now,
        )
        session.add(person)
        session.flush()
        session.add(
            PersonExternalId(person_id=person.id, provider=provider, external_id=external_id)
        )
    elif (person.name, person.original_name) != (entry["name"], entry.get("original_name")):
        person.name = entry["name"]
        person.original_name = entry.get("original_name")
        person.updated_at = now
    photo = files.get(entry.get("photo_url") or "")
    if photo and person.photo_path is None:
        person.photo_path = photo
        person.updated_at = now
    return person


def _write_people(
    session: Session,
    item: Item,
    role: str,
    people: list[Mapping[str, Any]],
    files: Mapping[str, str],
) -> None:
    session.execute(
        delete(ItemPerson).where(ItemPerson.item_id == item.id, ItemPerson.role == role)
    )
    seen: set[int] = set()
    for entry in people:
        person = _person_for(session, entry, files)
        if person.id in seen:
            continue
        seen.add(person.id)
        session.add(
            ItemPerson(
                item_id=item.id,
                person_id=person.id,
                role=role,
                character_name=entry.get("character"),
                position=len(seen) - 1,
            )
        )


def _write_ratings(session: Session, item: Item, ratings: list[Mapping[str, Any]]) -> None:
    session.execute(delete(ItemRating).where(ItemRating.item_id == item.id))
    for rating in ratings:
        session.add(
            ItemRating(
                item_id=item.id,
                source=rating["source"],
                rating=rating["rating"],
                max_rating=rating["max_rating"],
                vote_count=rating.get("vote_count"),
            )
        )


def _write_image(
    session: Session, item: Item, kind: str, pick: Pick | None, files: Mapping[str, str]
) -> None:
    session.execute(
        delete(ItemImage).where(ItemImage.item_id == item.id, ItemImage.image_kind == kind)
    )
    if pick is None:
        return
    first = pick.value[0]
    session.add(
        ItemImage(
            item_id=item.id,
            image_kind=kind,
            source=pick.source,
            url=first["url"],
            file_path=files[first["url"]],
            language=first.get("language"),
            width=first.get("width"),
            height=first.get("height"),
            position=0,
        )
    )


def write_plan(
    session: Session,
    item: Item,
    fields: Iterable[TypeField],
    plan: Mapping[str, Pick | None],
    files: Mapping[str, str],
) -> None:
    extra = json.loads(item.fields)
    origin = json.loads(item.field_sources)
    for field in fields:
        key = field.field_key
        if key not in plan:
            continue
        pick = plan[key]
        if field.value_type == "images" and pick is not None and pick.value[0]["url"] not in files:
            continue  # 图片没下载下来：这个字段保持原样
        if field.stored_in == "items_column" and field.column_name == "title" and pick is None:
            continue  # 片名不能为空
        value = pick.value if pick is not None else None
        match field.stored_in:
            case "items_column":
                if field.value_type == "images":
                    value = files[pick.value[0]["url"]] if pick is not None else None
                setattr(item, field.column_name or key, value)
            case "fields":
                if value is None:
                    extra.pop(key, None)
                else:
                    extra[key] = value
            case "tags":
                write_tags(session, item, field.tag_kind or key, value or [])
            case "people":
                _write_people(session, item, field.person_role or key, value or [], files)
            case "ratings":
                _write_ratings(session, item, value or [])
            case "images":
                _write_image(session, item, field.image_kind or key, pick, files)
        if pick is None:
            origin.pop(key, None)
        else:
            origin[key] = pick.source
    item.fields = json.dumps(extra, ensure_ascii=False)
    item.field_sources = json.dumps(origin, ensure_ascii=False)
    item.updated_at = utc_now()
    refresh_subtitle(session, item)
