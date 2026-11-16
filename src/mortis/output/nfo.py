"""Emby 格式的电影 NFO。只读选定字段，元素顺序照 TMM 写出的样子。"""

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.config.models import MovieScrapeConfig
from mortis.db.models import (
    Item,
    ItemExternalId,
    ItemImage,
    ItemPerson,
    ItemRating,
    ItemSourceRecord,
    ItemTag,
    Person,
    PersonExternalId,
    Tag,
)
from mortis.sources.tmdb.client import TmdbClient
from mortis.sources.tmdb.common import SOURCE
from mortis.sources.tmdb.movie import movie_candidates

NFO_PROFILE_SIZE = "h632"
RATING_NAMES = {"tmdb": "themoviedb"}
_EMPTY = re.compile(r"<([A-Za-z_][\w.-]*(?: [^<>]*?)?) />")
THUMB_ASPECTS = {
    "clearlogo": "clearlogo",
    "keyart": "keyart",
    "banner": "banner",
    "clearart": "clearart",
    "discart": "discart",
    "thumb": "landscape",
}


def _tags(session: Session, item: Item, kind: str) -> list[str]:
    return list(
        session.scalars(
            select(Tag.name)
            .join(ItemTag, ItemTag.tag_id == Tag.id)
            .where(ItemTag.item_id == item.id, Tag.tag_kind == kind)
            .order_by(ItemTag.position)
        )
    )


def _people(session: Session, item: Item, role: str) -> list[tuple[Person, str | None, str | None]]:
    tmdb = (
        select(PersonExternalId.external_id)
        .where(PersonExternalId.person_id == Person.id, PersonExternalId.provider == "tmdb")
        .scalar_subquery()
    )
    rows = session.execute(
        select(Person, ItemPerson.character_name, tmdb)
        .join(ItemPerson, ItemPerson.person_id == Person.id)
        .where(ItemPerson.item_id == item.id, ItemPerson.role == role)
        .order_by(ItemPerson.position)
    ).all()
    return [(person, character, tmdb_id) for person, character, tmdb_id in rows]


def _external_ids(session: Session, item: Item) -> dict[str, str]:
    return {
        provider: value
        for provider, value in session.execute(
            select(ItemExternalId.provider, ItemExternalId.external_id).where(
                ItemExternalId.item_id == item.id
            )
        )
    }


def _image_urls(session: Session, item: Item) -> dict[str, list[str]]:
    urls: dict[str, list[str]] = {}
    for kind, url in session.execute(
        select(ItemImage.image_kind, ItemImage.url)
        .where(ItemImage.item_id == item.id, ItemImage.url.is_not(None))
        .order_by(ItemImage.position)
    ):
        urls.setdefault(kind, []).append(url)
    return urls


def kodi_trailer(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    video = parse_qs(parsed.query).get("v", [None])[0]
    if parsed.netloc.endswith("youtube.com") and video:
        return f"plugin://plugin.video.youtube/play/?video_id={video}"
    return url


def _local_time(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _text(parent: ET.Element, tag: str, value: Any = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attrs)
    if value is not None and value != "":
        element.text = str(value)
    return element


@dataclass
class _Snapshot:
    """从 TMDB 快照里取库里不存的网址和职务。"""

    photos: dict[str, str]
    jobs: dict[str, str]
    poster: str | None


def _snapshot(session: Session, item: Item, config: MovieScrapeConfig) -> _Snapshot:
    record = session.get(ItemSourceRecord, (item.id, SOURCE))
    raw: dict[str, Any] = json.loads(record.raw) if record is not None else {}
    credits = raw.get("credits") or {}
    photos: dict[str, str] = {}
    jobs: dict[str, str] = {}
    for entry in (credits.get("cast") or []) + (credits.get("crew") or []):
        if entry.get("id") is None:
            continue
        key = str(entry["id"])
        if entry.get("profile_path"):
            photos.setdefault(key, TmdbClient.image_url(entry["profile_path"], NFO_PROFILE_SIZE))
        if entry.get("department") == "Production" and "Producer" in (entry.get("job") or ""):
            jobs.setdefault(key, entry["job"])
    poster = None
    if raw and item.poster_path and json.loads(item.field_sources).get("poster") == SOURCE:
        posters = movie_candidates(raw, config).get("poster") or []
        poster = posters[0]["url"] if posters else None
    return _Snapshot(photos, jobs, poster)


def _person(
    parent: ET.Element,
    tag: str,
    person: Person,
    role: str | None,
    tmdb_id: str | None,
    photo: str | None,
    *,
    with_id: bool = True,
) -> ET.Element:
    element = _text(parent, tag, **({} if with_id or not tmdb_id else {"tmdbid": tmdb_id}))
    _text(element, "name", person.name)
    if role:
        _text(element, "role", role)
    if photo:
        _text(element, "thumb", photo)
    if tmdb_id:
        _text(element, "profile", f"https://www.themoviedb.org/person/{tmdb_id}")
        if with_id:
            _text(element, "tmdbid", tmdb_id)
    return element


def build_movie_nfo(session: Session, item: Item, config: MovieScrapeConfig) -> bytes:
    output = config.output
    extra = json.loads(item.fields)
    ids = _external_ids(session, item)
    images = _image_urls(session, item)
    snapshot = _snapshot(session, item, config)
    parent = session.get(Item, item.parent_id) if item.parent_id else None
    collection_id = _external_ids(session, parent).get("tmdb") if parent is not None else None
    # 用条目的修改时间，内容没变时重写出来的字节也不变。
    stamp = _local_time(item.updated_at)

    root = ET.Element("movie")
    _text(root, "title", item.title)
    _text(root, "originaltitle", item.original_title)
    _text(root, "sorttitle", item.sort_title)
    _text(root, "epbookmark")
    _text(root, "year", (item.release_date or "")[:4] or None)

    ratings = list(
        session.scalars(
            select(ItemRating).where(ItemRating.item_id == item.id).order_by(ItemRating.source)
        )
    )
    container = _text(root, "ratings")
    for index, rating in enumerate(ratings):
        element = _text(
            container,
            "rating",
            default="true" if index == 0 else "false",
            max=f"{rating.max_rating:g}",
            name=RATING_NAMES.get(rating.source, rating.source),
        )
        _text(element, "value", f"{rating.rating:g}")
        _text(element, "votes", rating.vote_count or 0)
    _text(root, "userrating", 0)
    _text(root, "top250", extra.get("top250") or 0)

    if parent is not None:
        element = _text(root, "set", **({"tmdbcolid": collection_id} if collection_id else {}))
        _text(element, "name", parent.title)
        if parent.overview:
            _text(element, "overview", parent.overview)
    else:
        _text(root, "set")

    _text(root, "plot", item.overview)
    _text(root, "outline", item.overview)
    _text(root, "tagline", extra.get("tagline"))
    _text(root, "runtime", extra.get("runtime"))
    if snapshot.poster:
        _text(root, "thumb", snapshot.poster, aspect="poster")
    for kind, aspect in THUMB_ASPECTS.items():
        for url in images.get(kind, [])[:1]:
            _text(root, "thumb", url, aspect=aspect)
    fanart = images.get("fanart", [])
    if fanart:
        element = _text(root, "fanart")
        for url in fanart:
            _text(element, "thumb", url)
    certification = extra.get("certification")
    if certification:
        _text(root, "mpaa", f"{config.certification_country}:{certification}")
        _text(root, "certification", f"{config.certification_country}:{certification}")
    if "imdb" in ids:
        _text(root, "id", ids["imdb"])
    if "tmdb" in ids:
        _text(root, "tmdbid", ids["tmdb"])
    default_id = "imdb" if "imdb" in ids else "tmdb"
    for provider in ("tmdb", "imdb"):
        if provider in ids:
            _text(
                root,
                "uniqueid",
                ids[provider],
                default="true" if provider == default_id else "false",
                type=provider,
            )
    if collection_id:
        _text(root, "uniqueid", collection_id, default="false", type="tmdbSet")
    for country in _tags(session, item, "country"):
        _text(root, "country", country)
    _text(root, "status")
    _text(root, "code")
    _text(root, "premiered", item.release_date)
    _text(root, "watched", "false")
    _text(root, "playcount", 0)
    for genre in _tags(session, item, "genre"):
        _text(root, "genre", genre)
    studios = _tags(session, item, "studio")
    for studio in studios[:1] if output.nfo_single_studio else studios:
        _text(root, "studio", studio)
    writers = _people(session, item, "writer")
    for person, _role, tmdb_id in writers:
        _text(root, "credits", person.name, **({"tmdbid": tmdb_id} if tmdb_id else {}))
    for person, _role, tmdb_id in _people(session, item, "director"):
        _text(root, "director", person.name, **({"tmdbid": tmdb_id} if tmdb_id else {}))
    for keyword in _tags(session, item, "keyword"):
        _text(root, "tag", keyword)
    for person, character, tmdb_id in _people(session, item, "actor"):
        photo = snapshot.photos.get(tmdb_id) if tmdb_id else None
        _person(root, "actor", person, character, tmdb_id, photo)
    for person, _role, tmdb_id in _people(session, item, "producer"):
        job = snapshot.jobs.get(tmdb_id, "Producer") if tmdb_id else "Producer"
        photo = snapshot.photos.get(tmdb_id) if tmdb_id else None
        _person(root, "producer", person, job, tmdb_id, photo, with_id=False)
    _text(root, "trailer", kodi_trailer(extra.get("trailer")))
    languages = _tags(session, item, "language")
    _text(root, "languages", ", ".join(languages) or None)
    _text(root, "dateadded", _local_time(item.created_at))
    if output.nfo_write_lockdata:
        _text(root, "lockdata", "true")
    video = ET.SubElement(ET.SubElement(_text(root, "fileinfo"), "streamdetails"), "video")
    for tag, value in (("codec", None), ("aspect", "0.00"), ("width", 0), ("height", 0)):
        _text(video, tag, value)
    _text(video, "resolution")
    _text(video, "durationinseconds", 0)
    for person, _role, _tmdb_id in writers:
        _text(root, "writer", person.name)

    ET.indent(root, space="  ")
    body = _EMPTY.sub(r"<\1/>", ET.tostring(root, encoding="unicode"))
    header = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<!--created on {stamp} by Mortis for EMBY-->\n"
    )
    return (header + body + "\n").encode("utf-8")
