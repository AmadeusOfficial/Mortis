import json
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from mortis.db.models import (
    DataType,
    Item,
    ItemExternalId,
    ItemImage,
    ItemPerson,
    ItemRating,
    ItemSourceRecord,
    ItemTag,
    Library,
    MediaFile,
    Person,
    Tag,
    TypeField,
)
from mortis.scrape.apply import level_fields

EDITABLE_TYPES = frozenset({"text", "longtext", "number", "date", "url"})


def image_url(rel: str | None) -> str | None:
    return f"/api/images/{rel}" if rel else None


def year_of(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def is_editable(field: TypeField) -> bool:
    if field.stored_in == "tags":
        return True
    return field.value_type in EDITABLE_TYPES and field.stored_in in ("items_column", "fields")


def _people(session: Session, item: Item, role: str | None) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Person, ItemPerson.character_name)
        .join(ItemPerson, ItemPerson.person_id == Person.id)
        .where(ItemPerson.item_id == item.id, ItemPerson.role == role)
        .order_by(ItemPerson.position)
    ).all()
    return [
        {
            "id": person.id,
            "name": person.name,
            "original_name": person.original_name,
            "character": character,
            "photo_url": image_url(person.photo_path),
        }
        for person, character in rows
    ]


def read_value(session: Session, item: Item, field: TypeField) -> Any:
    match field.stored_in:
        case "items_column":
            value = getattr(item, field.column_name or field.field_key)
            if field.value_type == "images":
                if not value:
                    return []
                return [{"url": image_url(value), "language": None, "width": None, "height": None}]
            return value
        case "fields":
            return json.loads(item.fields).get(field.field_key)
        case "tags":
            return list(
                session.scalars(
                    select(Tag.name)
                    .join(ItemTag, ItemTag.tag_id == Tag.id)
                    .where(ItemTag.item_id == item.id, Tag.tag_kind == field.tag_kind)
                    .order_by(ItemTag.position)
                )
            )
        case "people":
            return _people(session, item, field.person_role)
        case "ratings":
            return [
                {
                    "source": r.source,
                    "rating": r.rating,
                    "max_rating": r.max_rating,
                    "vote_count": r.vote_count,
                }
                for r in session.scalars(
                    select(ItemRating)
                    .where(ItemRating.item_id == item.id)
                    .order_by(ItemRating.source)
                )
            ]
        case "images":
            return [
                {
                    "url": image_url(i.file_path),
                    "language": i.language,
                    "width": i.width,
                    "height": i.height,
                }
                for i in session.scalars(
                    select(ItemImage)
                    .where(
                        ItemImage.item_id == item.id,
                        ItemImage.image_kind == field.image_kind,
                        ItemImage.file_path.is_not(None),
                    )
                    .order_by(ItemImage.position)
                )
            ]
    return None


def wall_rows(session: Session, items: list[Item]) -> list[dict[str, Any]]:
    ids = [item.id for item in items]
    scraped = set(
        session.scalars(select(ItemSourceRecord.item_id).where(ItemSourceRecord.item_id.in_(ids)))
    )
    return [
        {
            "id": item.id,
            "title": item.title,
            "subtitle": item.subtitle,
            "year": year_of(item.release_date),
            "poster_url": image_url(item.poster_path),
            "is_scraped": item.id in scraped,
        }
        for item in items
    ]


def _first_images(session: Session, ids: list[int], kind: str) -> dict[int, str]:
    rows = session.execute(
        select(ItemImage.item_id, ItemImage.file_path)
        .where(
            ItemImage.item_id.in_(ids),
            ItemImage.image_kind == kind,
            ItemImage.file_path.is_not(None),
        )
        .order_by(ItemImage.item_id, ItemImage.position.desc())
    )
    return {item_id: path for item_id, path in rows}


def _seasons(session: Session, show: Item) -> list[dict[str, Any]]:
    seasons = list(
        session.scalars(
            select(Item)
            .where(Item.parent_id == show.id, Item.kind == "season")
            .order_by(Item.index_number)
        )
    )
    episodes = list(
        session.scalars(
            select(Item)
            .where(Item.parent_id.in_([s.id for s in seasons]), Item.kind == "episode")
            .order_by(Item.index_number)
        )
    )
    videos = dict(
        session.execute(
            select(MediaFile.item_id, func.count())
            .where(MediaFile.item_id.in_([e.id for e in episodes]), MediaFile.file_kind == "video")
            .group_by(MediaFile.item_id)
        ).all()
    )
    thumbs = _first_images(session, [e.id for e in episodes], "thumb")
    by_season: dict[int, list[dict[str, Any]]] = {s.id: [] for s in seasons}
    for episode in episodes:
        by_season[episode.parent_id or 0].append(
            {
                "id": episode.id,
                "number": episode.index_number,
                "title": episode.title,
                "air_date": episode.release_date,
                "has_file": episode.id in videos,
                "still_url": image_url(thumbs.get(episode.id)),
            }
        )
    return [
        {
            "id": season.id,
            "number": season.index_number,
            "title": season.title,
            "poster_url": image_url(season.poster_path),
            "episode_count": len(by_season[season.id]),
            "file_count": sum(1 for e in by_season[season.id] if e["has_file"]),
            "episodes": by_season[season.id],
        }
        for season in seasons
    ]


def _show_of(session: Session, item: Item) -> tuple[Item | None, str | None]:
    """季或集所属的剧，和「第 1 季 第 2 集」这样的位置。"""
    if item.kind == "season":
        show = session.get(Item, item.parent_id) if item.parent_id else None
        return show, f"第 {item.index_number} 季"
    if item.kind == "episode":
        season = session.get(Item, item.parent_id) if item.parent_id else None
        show = session.get(Item, season.parent_id) if season and season.parent_id else None
        number = season.index_number if season else None
        return show, f"第 {number} 季 第 {item.index_number} 集"
    return None, None


def item_detail(session: Session, item: Item) -> dict[str, Any]:
    fields = level_fields(session, item.data_type_id, item.kind)
    sources = json.loads(item.field_sources)
    locked = set(json.loads(item.locked_fields))
    sections: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        sections.setdefault(field.detail_section, []).append(
            {
                "key": field.field_key,
                "label": field.label,
                "value_type": field.value_type,
                "stored_in": field.stored_in,
                "value": read_value(session, item, field),
                "source": sources.get(field.field_key),
                "locked": field.field_key in locked,
                "lockable": bool(field.is_lockable),
                "editable": is_editable(field),
            }
        )
    library = session.get(Library, item.library_id)
    parent = session.get(Item, item.parent_id) if item.parent_id else None
    fanart = session.scalar(
        select(ItemImage.file_path)
        .where(
            ItemImage.item_id == item.id,
            ItemImage.image_kind == "fanart",
            ItemImage.file_path.is_not(None),
        )
        .order_by(ItemImage.position)
        .limit(1)
    )
    parts: list[dict[str, Any]] = []
    if item.kind == "collection":
        children = list(
            session.scalars(
                select(Item)
                .where(Item.parent_id == item.id)
                .order_by(Item.release_date.is_(None), Item.release_date, Item.id)
            )
        )
        parts = wall_rows(session, children)
    show, position = _show_of(session, item)
    if show is not None:
        parent = None
    data_type = session.get(DataType, item.data_type_id)
    seasons = (
        _seasons(session, item)
        if item.kind == "work" and data_type is not None and data_type.structure == "series"
        else []
    )
    record = session.get(ItemSourceRecord, (item.id, "tmdb"))
    external_ids = {
        provider: value
        for provider, value in session.execute(
            select(ItemExternalId.provider, ItemExternalId.external_id)
            .where(ItemExternalId.item_id == item.id)
            .order_by(ItemExternalId.is_primary.desc(), ItemExternalId.provider)
        )
    }
    files = session.scalars(
        select(MediaFile)
        .where(MediaFile.item_id == item.id)
        .order_by(
            case({"video": 0, "subtitle": 1, "nfo": 2}, value=MediaFile.file_kind, else_=3),
            MediaFile.path,
        )
    )
    return {
        "id": item.id,
        "library_id": item.library_id,
        "data_type_id": item.data_type_id,
        "library_name": library.name if library else "",
        "kind": item.kind,
        "title": item.title,
        "subtitle": item.subtitle,
        "year": year_of(item.release_date),
        "poster_url": image_url(item.poster_path),
        "fanart_url": image_url(fanart),
        "external_ids": external_ids,
        "collection": {
            "id": parent.id,
            "title": parent.title,
            "poster_url": image_url(parent.poster_path),
        }
        if parent
        else None,
        "parts": parts,
        "show": {"id": show.id, "title": show.title, "poster_url": image_url(show.poster_path)}
        if show
        else None,
        "position": position,
        "seasons": seasons,
        "files": [
            {"id": f.id, "path": f.path, "file_kind": f.file_kind, "size": f.size} for f in files
        ],
        "scraped_at": record.fetched_at if record else None,
        "sections": [{"name": name, "fields": rows} for name, rows in sections.items()],
    }
