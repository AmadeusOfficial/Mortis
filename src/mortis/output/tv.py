"""电视剧的 NFO 和图片：剧目录的 tvshow.nfo 和剧图、季目录的 season.nfo、集视频旁边的 NFO 和缩略图。

元素顺序照 TMM 写出的 Emby 样式。缺失集（没有视频）不写任何文件。
"""

import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.config.models import TvScrapeConfig
from mortis.db.models import Item, ItemRating, ItemSourceRecord, Library, MediaFile
from mortis.items.matching import show_folder
from mortis.output.nfo import (
    _EMPTY,
    NFO_PROFILE_SIZE,
    RATING_NAMES,
    _external_ids,
    _image_urls,
    _local_time,
    _people,
    _person,
    _tags,
    _text,
    kodi_trailer,
)
from mortis.output.writer import Target, WriteReport, _first_images, apply_targets
from mortis.scrape.images import ImageStore
from mortis.sources.tmdb.client import TmdbClient
from mortis.sources.tmdb.common import SOURCE
from mortis.sources.tmdb.tv import STATUS, season_candidates, show_candidates

SHOW_IMAGES = (
    "poster",
    "fanart",
    "banner",
    "clearlogo",
    "clearart",
    "keyart",
    "thumb",
    "characterart",
)
STATUS_EN = {value: key for key, value in STATUS.items()}


@dataclass
class _Episode:
    item: Item
    video: Path


@dataclass
class _Season:
    item: Item
    raw: dict[str, Any]
    episodes: list[_Episode] = field(default_factory=list)


@dataclass
class ShowLayout:
    """剧在磁盘上的位置。folder 是 None 时说明视频直接放在库根目录，不写剧级文件。"""

    show: Item
    folder: Path | None
    raw: dict[str, Any]
    seasons: list[_Season]


def _raw(session: Session, item_id: int) -> dict[str, Any]:
    record = session.get(ItemSourceRecord, (item_id, SOURCE))
    return json.loads(record.raw) if record is not None else {}


def show_layout(session: Session, show: Item) -> ShowLayout:
    library = session.get(Library, show.library_id)
    seasons: list[_Season] = []
    folder: Path | None = None
    for season in session.scalars(
        select(Item)
        .where(Item.parent_id == show.id, Item.kind == "season")
        .order_by(Item.index_number)
    ):
        entry = _Season(season, _raw(session, season.id))
        rows = session.execute(
            select(Item, MediaFile.path)
            .join(MediaFile, MediaFile.item_id == Item.id)
            .where(Item.parent_id == season.id, MediaFile.file_kind == "video")
            .order_by(Item.index_number, MediaFile.path)
        ).all()
        for episode, path in rows:
            entry.episodes.append(_Episode(episode, Path(path)))
        seasons.append(entry)
    paths = [str(e.video) for s in seasons for e in s.episodes] + list(
        session.scalars(
            select(MediaFile.path).where(
                MediaFile.item_id == show.id, MediaFile.file_kind == "video"
            )
        )
    )
    if library is not None:
        for path in paths:
            found = show_folder(library, path)
            if found:
                folder = Path(found)
                break
    return ShowLayout(show, folder, _raw(session, show.id), seasons)


def season_folder(layout: ShowLayout, season: _Season) -> Path | None:
    """季的视频都在同一个目录、且不是剧目录时，那个目录就是季目录。"""
    folders = {episode.video.parent for episode in season.episodes}
    if len(folders) != 1:
        return None
    (folder,) = folders
    return folder if folder != layout.folder else None


def _season_image_name(number: int | None) -> str:
    return "season-specials-poster" if number == 0 else f"season{number or 0:02d}-poster"


def _photos(raws: list[dict[str, Any]]) -> dict[str, str]:
    photos: dict[str, str] = {}

    def add(entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            if entry.get("id") is not None and entry.get("profile_path"):
                photos.setdefault(
                    str(entry["id"]),
                    TmdbClient.image_url(entry["profile_path"], NFO_PROFILE_SIZE),
                )

    for raw in raws:
        credits = raw.get("aggregate_credits") or {}
        add(credits.get("cast") or [])
        add(credits.get("crew") or [])
        for episode in raw.get("episodes") or []:
            add(episode.get("guest_stars") or [])
            add(episode.get("crew") or [])
    return photos


def _ratings(session: Session, root: ET.Element, item: Item) -> None:
    container = _text(root, "ratings")
    rows = session.scalars(
        select(ItemRating).where(ItemRating.item_id == item.id).order_by(ItemRating.source)
    )
    for index, rating in enumerate(rows):
        element = _text(
            container,
            "rating",
            default="true" if index == 0 else "false",
            max=f"{rating.max_rating:g}",
            name=RATING_NAMES.get(rating.source, rating.source),
        )
        _text(element, "value", f"{rating.rating:g}")
        _text(element, "votes", rating.vote_count or 0)


def _poster_url(item: Item, candidates: dict[str, Any]) -> str | None:
    if not item.poster_path or json.loads(item.field_sources).get("poster") != SOURCE:
        return None
    posters = candidates.get("poster") or []
    return posters[0]["url"] if posters else None


def _actors(session: Session, root: ET.Element, items: list[Item], photos: dict[str, str]) -> None:
    seen: set[int] = set()
    for item in items:
        for person, character, tmdb_id in _people(session, item, "actor"):
            if person.id in seen:
                continue
            seen.add(person.id)
            _person(root, "actor", person, character, tmdb_id, photos.get(tmdb_id or ""))


def _finish(root: ET.Element, stamp: str) -> bytes:
    ET.indent(root, space="  ")
    body = _EMPTY.sub(r"<\1/>", ET.tostring(root, encoding="unicode"))
    header = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<!--created on {stamp} by Mortis for EMBY-->\n"
    )
    return (header + body + "\n").encode("utf-8")


def build_show_nfo(session: Session, layout: ShowLayout, config: TvScrapeConfig) -> bytes:
    show = layout.show
    output = config.output
    extra = json.loads(show.fields)
    ids = _external_ids(session, show)
    images = _image_urls(session, show)
    root = ET.Element("tvshow")
    _text(root, "title", show.title)
    _text(root, "originaltitle", show.original_title)
    _text(root, "showtitle", show.title)
    _text(root, "sorttitle", show.sort_title)
    _text(root, "year", (show.release_date or "")[:4] or None)
    _text(root, "top250", extra.get("top250") or 0)
    _ratings(session, root, show)
    _text(root, "userrating", 0)
    _text(root, "outline", show.overview)
    _text(root, "plot", show.overview)
    _text(root, "tagline")
    _text(root, "runtime", extra.get("runtime") or 0)
    poster = _poster_url(show, show_candidates(layout.raw, config)) if layout.raw else None
    if poster:
        _text(root, "thumb", poster, aspect="poster")
    for kind in ("clearlogo", "keyart", "banner", "clearart", "thumb", "characterart"):
        for url in images.get(kind, [])[:1]:
            _text(root, "thumb", url, aspect="landscape" if kind == "thumb" else kind)
    seasons = sorted(layout.seasons, key=lambda s: -(s.item.index_number or 0))
    for season in seasons:
        _text(root, "namedseason", season.item.title, number=str(season.item.index_number))
    for season in seasons:
        url = _poster_url(season.item, season_candidates(season.raw, config))
        if url:
            _text(
                root,
                "thumb",
                url,
                aspect="poster",
                season=str(season.item.index_number),
                type="season",
            )
    fanart = images.get("fanart", [])
    if fanart:
        element = _text(root, "fanart")
        for url in fanart:
            _text(element, "thumb", url)
    certification = extra.get("certification")
    _text(root, "mpaa", certification)
    _text(root, "certification", certification)
    guide = {key: ids[key] for key in ("tmdb", "imdb", "tvdb") if key in ids}
    _text(root, "episodeguide", json.dumps(guide, separators=(",", ":")) if guide else None)
    _text(root, "id", ids.get("tvdb"))
    _text(root, "imdbid", ids.get("imdb"))
    _text(root, "tmdbid", ids.get("tmdb"))
    for provider in ("tmdb", "imdb", "tvdb"):
        if provider in ids:
            _text(
                root,
                "uniqueid",
                ids[provider],
                default="true" if provider == "tmdb" else "false",
                type=provider,
            )
    _text(root, "premiered", show.release_date)
    status = extra.get("status")
    _text(root, "status", STATUS_EN.get(status, status))
    _text(root, "watched", "false")
    _text(root, "playcount")
    for genre in _tags(session, show, "genre"):
        _text(root, "genre", genre)
    studios = _tags(session, show, "studio")
    for studio in studios[:1] if output.nfo_single_studio else studios:
        _text(root, "studio", studio)
    for country in _tags(session, show, "country"):
        _text(root, "country", country)
    for keyword in _tags(session, show, "keyword"):
        _text(root, "tag", keyword)
    _actors(session, root, [show], _photos([layout.raw]))
    _text(root, "trailer", kodi_trailer(extra.get("trailer")))
    _text(root, "dateadded", _local_time(show.created_at))
    if output.nfo_write_lockdata:
        _text(root, "lockdata", "true")
    return _finish(root, _local_time(show.updated_at))


def build_season_nfo(
    session: Session, layout: ShowLayout, season: _Season, config: TvScrapeConfig
) -> bytes:
    item = season.item
    ids = _external_ids(session, layout.show)
    root = ET.Element("season")
    _text(root, "seasonnumber", item.index_number)
    _text(root, "title", item.title)
    _text(root, "showtitle", layout.show.title)
    _text(root, "sorttitle", item.title)
    _text(root, "year", (item.release_date or "")[:4] or None)
    _text(root, "plot", item.overview)
    poster = _poster_url(item, season_candidates(season.raw, config))
    if poster:
        _text(root, "thumb", poster, aspect="poster")
    _text(root, "tvdbid", ids.get("tvdb"))
    _text(root, "imdbid", ids.get("imdb"))
    _text(root, "tmdbid", ids.get("tmdb"))
    if "tmdb" in ids:
        _text(root, "uniqueid", ids["tmdb"], type="tmdb")
    _text(root, "premiered", item.release_date)
    if config.output.nfo_write_lockdata:
        _text(root, "lockdata", "true")
    return _finish(root, _local_time(item.updated_at))


def build_episode_nfo(
    session: Session,
    layout: ShowLayout,
    season: _Season,
    episode: Item,
    config: TvScrapeConfig,
) -> bytes:
    output = config.output
    show_extra = json.loads(layout.show.fields)
    ids = _external_ids(session, episode)
    images = _image_urls(session, episode)
    root = ET.Element("episodedetails")
    _text(root, "title", episode.title)
    _text(root, "originaltitle", episode.original_title or episode.title)
    _text(root, "showtitle", layout.show.title)
    _text(root, "season", season.item.index_number)
    _text(root, "episode", episode.index_number)
    _text(root, "displayseason", -1)
    _text(root, "displayepisode", -1)
    _text(root, "id")
    if "tmdb" in ids:
        _text(root, "uniqueid", ids["tmdb"], default="true", type="tmdb")
    _ratings(session, root, episode)
    _text(root, "userrating", 0)
    _text(root, "plot", episode.overview)
    runtime = json.loads(episode.fields).get("runtime") or show_extra.get("runtime")
    _text(root, "runtime", runtime or 0)
    for url in images.get("thumb", [])[:1]:
        _text(root, "thumb", url)
    _text(root, "mpaa", show_extra.get("certification"))
    _text(root, "premiered", episode.release_date)
    _text(root, "aired", episode.release_date)
    _text(root, "watched", "false")
    _text(root, "playcount", 0)
    studios = _tags(session, layout.show, "studio")
    for studio in studios[:1] if output.nfo_single_studio else studios:
        _text(root, "studio", studio)
    for person, _role, tmdb_id in _people(session, episode, "writer"):
        _text(root, "credits", person.name, **({"tmdbid": tmdb_id} if tmdb_id else {}))
    for person, _role, tmdb_id in _people(session, episode, "director"):
        _text(root, "director", person.name, **({"tmdbid": tmdb_id} if tmdb_id else {}))
    _actors(session, root, [layout.show, episode], _photos([layout.raw, season.raw]))
    _text(root, "trailer")
    _text(root, "dateadded", _local_time(episode.created_at))
    _text(root, "epbookmark")
    _text(root, "code")
    if output.nfo_write_lockdata:
        _text(root, "lockdata", "true")
    return _finish(root, _local_time(episode.updated_at))


def _first_image(session: Session, item: Item, kind: str) -> str | None:
    if kind == "poster" and item.poster_path:
        return item.poster_path
    return _first_images(session, item.id).get(kind)


def write_show_files(
    session: Session, store: ImageStore, show: Item, config: TvScrapeConfig
) -> WriteReport:
    """写出一部剧的全部文件并记进 media_files。只 flush，调用方提交。"""
    layout = show_layout(session, show)
    owners = [show.id] + [s.item.id for s in layout.seasons]
    owners += [e.item.id for s in layout.seasons for e in s.episodes]
    targets: list[Target] = []
    missing: list[str] = []

    def image(path: Path, rel: str | None, owner: int) -> None:
        if rel is None:
            return
        source = store.resolve(rel)
        if source is None:
            missing.append(str(path))
        else:
            targets.append(
                Target(path.with_suffix(source.suffix), "image", source=source, owner=owner)
            )

    if layout.folder is not None:
        targets.append(
            Target(
                layout.folder / "tvshow.nfo",
                "nfo",
                content=build_show_nfo(session, layout, config),
                owner=show.id,
            )
        )
        for kind in SHOW_IMAGES:
            image(layout.folder / f"{kind}.jpg", _first_image(session, show, kind), show.id)
    for season in layout.seasons:
        if layout.folder is not None and config.images.season_poster:
            image(
                layout.folder / f"{_season_image_name(season.item.index_number)}.jpg",
                season.item.poster_path,
                show.id,
            )
        folder = season_folder(layout, season)
        if folder is not None:
            targets.append(
                Target(
                    folder / "season.nfo",
                    "nfo",
                    content=build_season_nfo(session, layout, season, config),
                    owner=season.item.id,
                )
            )
        for episode in season.episodes:
            stem = episode.video.with_suffix("")
            targets.append(
                Target(
                    Path(f"{stem}.nfo"),
                    "nfo",
                    content=build_episode_nfo(session, layout, season, episode.item, config),
                    owner=episode.item.id,
                )
            )
            image(
                Path(f"{stem}-thumb.jpg"),
                _first_image(session, episode.item, "thumb"),
                episode.item.id,
            )
    unique: dict[str, Target] = {}
    for target in targets:
        unique.setdefault(os.path.normcase(str(target.path)), target)
    report = apply_targets(session, show.library_id, owners, list(unique.values()))
    report.skipped = missing
    return report
