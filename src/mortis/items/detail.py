import json
import os
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
    Task,
    TypeField,
)
from mortis.items.identity import manual_ids
from mortis.items.missing import show_missing_episodes
from mortis.organize.episodes import parse_episode
from mortis.scrape.apply import level_fields
from mortis.tasks import repo as task_repo

EDITABLE_TYPES = frozenset({"text", "longtext", "number", "date", "url"})
ISSUE_LENGTH = 40


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


def issue_text(error: str | None) -> str | None:
    """失败原因的第一行，超过 40 个字截断。"""
    line = next((row.strip() for row in (error or "").splitlines() if row.strip()), "")
    if not line:
        return None
    return line if len(line) <= ISSUE_LENGTH else line[:ISSUE_LENGTH] + "…"


def wall_rows(
    session: Session, items: list[Item], year_dates: dict[int, str | None] | None = None
) -> list[dict[str, Any]]:
    """year_dates 按条目 id 给出算年份用的日期，没给的用条目自己的发行日期。"""
    ids = [item.id for item in items]
    dates = year_dates or {}
    scraped = set(
        session.scalars(select(ItemSourceRecord.item_id).where(ItemSourceRecord.item_id.in_(ids)))
    )
    latest = task_repo.latest_scrape_tasks(session, ids)
    manual = manual_ids(session, items)
    return [
        {
            "id": item.id,
            "kind": item.kind,
            "title": item.title,
            "subtitle": item.subtitle,
            "year": year_of(dates.get(item.id, item.release_date)),
            "poster_url": image_url(item.poster_path),
            "is_scraped": item.id in scraped,
            "is_manual": item.id in manual,
            "issue": _issue_of(latest.get(item.id)),
        }
        for item in items
    ]


def _issue_of(task: Task | None) -> str | None:
    if task is None or task.status != task_repo.FAILED:
        return None
    return issue_text(task.error)


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


def _episode_runtime(episode: Item) -> int | None:
    try:
        raw = json.loads(episode.fields)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    val = raw.get("runtime")
    return val if isinstance(val, int) and val > 0 else None


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
    by_id = {e.id: e for e in episodes}
    covered: set[tuple[int | None, int | None]] = set()
    owners: set[int] = set()
    for item_id, path in session.execute(
        select(MediaFile.item_id, MediaFile.path).where(
            MediaFile.item_id.in_(by_id), MediaFile.file_kind == "video"
        )
    ):
        episode = by_id[item_id]
        owners.add(item_id)
        stem = os.path.splitext(os.path.basename(path))[0]
        parsed = parse_episode(stem, os.path.basename(os.path.dirname(path)))
        first = episode.index_number or 0
        offset = first - parsed.episodes[0] if parsed else 0
        for number in parsed.episodes if parsed else (first,):
            covered.add((episode.parent_id, number + offset))
    thumbs = _first_images(session, [e.id for e in episodes], "thumb")
    by_season: dict[int, list[dict[str, Any]]] = {s.id: [] for s in seasons}
    show_missing = show_missing_episodes(session, show, True)
    for episode in episodes:
        has_file = (episode.parent_id, episode.index_number) in covered
        if not has_file and not show_missing:
            continue
        by_season[episode.parent_id or 0].append(
            {
                "id": episode.id,
                "number": episode.index_number,
                "title": episode.title,
                "air_date": episode.release_date,
                "overview": episode.overview,
                "runtime": _episode_runtime(episode),
                "has_file": has_file,
                "playable": episode.id in owners,
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
        if by_season[season.id]
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
    if fanart is None and item.kind == "season" and show is not None:
        fanart = session.scalar(
            select(ItemImage.file_path)
            .where(
                ItemImage.item_id == show.id,
                ItemImage.image_kind == "fanart",
                ItemImage.file_path.is_not(None),
            )
            .order_by(ItemImage.position)
            .limit(1)
        )
    data_type = session.get(DataType, item.data_type_id)
    is_series_work = item.kind == "work" and data_type is not None and data_type.structure == "series"
    seasons = (
        _seasons(session, item)
        if is_series_work
        else (_seasons(session, show) if item.kind == "season" and show is not None else [])
    )
    scraped_at = session.scalar(
        select(func.max(ItemSourceRecord.fetched_at)).where(
            ItemSourceRecord.item_id == (show or item).id
        )
    )
    cover = (
        _first_images(session, [item.id], "thumb").get(item.id)
        if item.kind == "episode"
        else item.poster_path
    )
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
        "poster_url": image_url(cover),
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
        "show": {
            "id": show.id,
            "title": show.title,
            "poster_url": image_url(show.poster_path),
            "logo_url": image_url(_first_images(session, [show.id], "clearlogo").get(show.id)),
        }
        if show
        else None,
        "position": position,
        "seasons": seasons,
        "show_missing_episodes": (
            None if item.show_missing_episodes is None else bool(item.show_missing_episodes)
        )
        if is_series_work
        else None,
        "show_missing_effective": (
            show_missing_episodes(session, item, True)
            if is_series_work
            else (
                show_missing_episodes(session, show, True)
                if item.kind == "season" and show is not None
                else None
            )
        ),
        "files": [
            {"id": f.id, "path": f.path, "file_kind": f.file_kind, "size": f.size} for f in files
        ],
        "scraped_at": scraped_at,
        "is_manual": bool(manual_ids(session, [show or item])),
        "sections": [{"name": name, "fields": rows} for name, rows in sections.items()],
    }
