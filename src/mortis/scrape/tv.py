"""刮削一部剧：剧、季、集三层，已播出的缺失集也建行，视频按季集号挂到集上。"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from mortis.config.models import TvScrapeConfig
from mortis.config.types import type_config
from mortis.db.models import Item, MediaFile, utc_now
from mortis.organize.episodes import parse_episode
from mortis.organize.names import parse_name, subtitle_belongs_to
from mortis.scrape.apply import field_orders, image_urls, level_fields, plan_values, write_plan
from mortis.scrape.common import (
    SOURCE,
    download_images,
    external_ids,
    gather_sources,
    image_skip,
    load_snapshot,
    load_snapshots,
    missing_photos,
    save_external_ids,
    save_snapshot,
    source_outcome,
    tmdb_id_of,
)
from mortis.scrape.images import ImageStore
from mortis.sources import fanart, tvdb
from mortis.sources.builtin import FetchRequest, builtin_ids
from mortis.sources.tmdb.client import open_tmdb
from mortis.sources.tmdb.search import similarity
from mortis.sources.tmdb.tv import (
    episode_candidates,
    fetch_season,
    season_candidates,
    show_external_ids,
)
from mortis.tasks.base import TaskContext, TaskError

SHOW_IMAGES = {
    "poster": "poster",
    "fanart": "fanart",
    "clearlogo": "clearlogo",
    "keyart": "keyart",
    "banner": "banner",
    "clearart": "clearart",
    "thumb": "thumb",
    "characterart": "characterart",
}
SEASON_IMAGES = {"poster": "season_poster", "banner": "season_banner", "thumb": "season_thumb"}
EPISODE_IMAGES = {"thumb": "episode_thumb"}
SEASON_NAME_MATCH = 0.6
SAME_TITLE = 0.95


@dataclass
class _Season:
    number: int
    raw: dict[str, Any]
    fetched: bool
    values: dict[str, Any]
    episodes: dict[int, dict[str, Any]] = field(default_factory=dict)
    ids: dict[int, str] = field(default_factory=dict)


def _subtree_files(session: Session, show_id: int) -> list[MediaFile]:
    """挂在剧、季、集上的视频和字幕。"""
    season_ids = select(Item.id).where(Item.parent_id == show_id, Item.kind == "season")
    episode_ids = select(Item.id).where(Item.parent_id.in_(season_ids), Item.kind == "episode")
    return list(
        session.scalars(
            select(MediaFile)
            .where(
                MediaFile.file_kind.in_(("video", "subtitle")),
                (MediaFile.item_id == show_id) | MediaFile.item_id.in_(episode_ids),
            )
            .order_by(MediaFile.path)
        )
    )


Local = dict[str, tuple[int | None, tuple[int, ...]]]


def _local_numbers(files: list[MediaFile]) -> Local:
    """视频路径 → (季号, 文件里的各集集号)。季号没写时是 None，花絮不在里面。"""
    numbers: Local = {}
    for row in files:
        if row.file_kind != "video":
            continue
        folder = os.path.basename(os.path.dirname(row.path))
        parsed = parse_episode(os.path.splitext(os.path.basename(row.path))[0], folder)
        if parsed is not None:
            numbers[row.path] = (parsed.season, parsed.episodes)
    return numbers


def _year(date_text: str | None) -> int | None:
    return int(date_text[:4]) if date_text and date_text[:4].isdigit() else None


def _folder_season(folder: str, show_raw: dict[str, Any]) -> int | None:
    """续作单独一个目录时它对应哪一季：目录年份是某季的播出年份，或冒号后面是那季的季名。"""
    parsed = parse_name(folder)
    seasons = [s for s in show_raw.get("seasons") or [] if (s.get("season_number") or 0) >= 1]
    names = (show_raw.get("name") or "", show_raw.get("original_name") or "")
    if max(similarity(parsed.title, name) for name in names) >= SAME_TITLE:
        return None
    if parsed.year is not None and parsed.year != _year(show_raw.get("first_air_date")):
        for entry in seasons:
            if _year(entry.get("air_date")) == parsed.year:
                return entry["season_number"]
    _, colon, suffix = parsed.title.partition(":")
    if colon and suffix.strip():
        named = [(similarity(suffix, entry.get("name") or ""), entry) for entry in seasons]
        if named:
            value, entry = max(named, key=lambda pair: pair[0])
            if value >= SEASON_NAME_MATCH:
                return entry["season_number"]
    return None


def _absolute(number: int, show_raw: dict[str, Any]) -> tuple[int, int]:
    """绝对集数按 TMDB 各季（不含特别篇）集数累加换算成季和集。"""
    counts = sorted(
        (s["season_number"], s.get("episode_count") or 0)
        for s in show_raw.get("seasons") or []
        if (s.get("season_number") or 0) >= 1
    )
    rest = number
    for season, count in counts:
        if rest <= count or count == 0:
            return season, rest
        rest -= count
    return (counts[0][0], number) if counts else (1, number)


def _resolve(local: Local, show_raw: dict[str, Any]) -> dict[str, tuple[int, tuple[int, ...]]]:
    """没写季号的视频，先看所在目录对应哪一季，再按绝对集数换算，都不行就当第 1 季。"""
    resolved: dict[str, tuple[int, tuple[int, ...]]] = {}
    folders: dict[str, int | None] = {}
    for path, (season, episodes) in local.items():
        if season is not None:
            resolved[path] = (season, episodes)
            continue
        folder = os.path.basename(os.path.dirname(path))
        if folder not in folders:
            folders[folder] = _folder_season(folder, show_raw)
        if folders[folder] is not None:
            resolved[path] = (folders[folder], episodes)
            continue
        mapped = [_absolute(number, show_raw) for number in episodes]
        resolved[path] = (mapped[0][0], tuple(n for s, n in mapped if s == mapped[0][0]))
    return resolved


def _child(session: Session, parent: Item, kind: str, number: int) -> Item:
    row = session.scalar(
        select(Item).where(
            Item.parent_id == parent.id, Item.kind == kind, Item.index_number == number
        )
    )
    if row is None:
        now = utc_now()
        row = Item(
            library_id=parent.library_id,
            data_type_id=parent.data_type_id,
            parent_id=parent.id,
            kind=kind,
            index_number=number,
            title=f"第 {number} 季" if kind == "season" else f"第 {number} 集",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
    return row


def _write(
    session: Session,
    item: Item,
    level: str,
    candidates: Mapping[str, Mapping[str, Any]],
    toggles: dict[str, str],
    config: TvScrapeConfig,
    order: Mapping[str, list[str]],
    files: dict[str, str],
) -> None:
    fields = level_fields(session, item.data_type_id, level)
    locked = set(json.loads(item.locked_fields))
    plan = plan_values(
        fields, candidates, order, image_skip(fields, locked, toggles, config.images)
    )
    write_plan(session, item, fields, plan, files)


def _bind(session: Session, files: list[MediaFile], targets: dict[str, int], show_id: int) -> int:
    """视频按季集号改挂到集，字幕跟同名视频走；对不上的留在剧上。返回挂上的视频数。"""
    bound = 0
    videos = {row.path: row for row in files if row.file_kind == "video"}
    for path, row in videos.items():
        target = targets.get(path, show_id)
        bound += target != show_id
        if row.item_id != target:
            row.item_id = target
            row.updated_at = utc_now()
    stems = {os.path.splitext(path)[0]: row.item_id for path, row in videos.items()}
    for row in files:
        if row.file_kind != "subtitle":
            continue
        folder, name = os.path.split(row.path)
        owner = next(
            (
                item_id
                for stem, item_id in stems.items()
                if os.path.dirname(stem) == folder
                and subtitle_belongs_to(name, os.path.basename(stem))
            ),
            show_id,
        )
        if row.item_id != owner:
            row.item_id = owner
            row.updated_at = utc_now()
    return bound


def _season_extras(
    raws: Mapping[str, dict[str, Any]], number: int, config: TvScrapeConfig
) -> dict[str, dict[str, Any]]:
    """作品层取到的其他来源给这一季的候选值。季和集只由 TMDB 决定有没有，这里只补值。"""
    extras: dict[str, dict[str, Any]] = {}
    if "fanart" in raws:
        extras["fanart"] = fanart.season_candidates(raws["fanart"], number, config.language)
    if "tvdb" in raws:
        extras["tvdb"] = tvdb.season_candidates(raws["tvdb"], number)
    return extras


def _episode_extras(
    raws: Mapping[str, dict[str, Any]], season: int, episode: int
) -> dict[str, dict[str, Any]]:
    if "tvdb" not in raws:
        return {}
    return {"tvdb": tvdb.episode_candidates(raws["tvdb"], season, episode)}


def _show_config(ctx: TaskContext, item_id: int) -> TvScrapeConfig:
    """电视剧和动漫各用自己的一节设置。"""
    config = ctx.services.config_store.load_config()
    with ctx.session_factory() as session:
        data_type_id = session.scalar(select(Item.data_type_id).where(Item.id == item_id))
    section = type_config(config, data_type_id or "")
    return section if isinstance(section, TvScrapeConfig) else config.types.tv


def scrape_show(ctx: TaskContext, item_id: int, *, force: bool) -> dict[str, Any]:
    services = ctx.services
    config = _show_config(ctx, item_id)
    store = ImageStore(services.data_dir)
    today = date.today().isoformat()

    with ctx.session_factory() as session:
        show = session.get(Item, item_id)
        if show is None or show.kind != "work":
            raise TaskError("条目不存在，可能已被删除")
        tmdb_id = tmdb_id_of(session, show.id)
        if tmdb_id is None:
            raise TaskError(f"「{show.title}」没有 TMDB 编号，先给它匹配一部剧")
        data_type_id = show.data_type_id
        ids = external_ids(session, show.id)
        snapshots = load_snapshots(session, show.id, force)
        season_raws = {
            row.index_number: load_snapshot(session, row.id, force)
            for row in session.scalars(
                select(Item).where(Item.parent_id == show.id, Item.kind == "season")
            )
        }
        parsed_local = _local_numbers(_subtree_files(session, show.id))
        show_fields = level_fields(session, data_type_id, "work")
        season_fields = level_fields(session, data_type_id, "season")
        episode_fields = level_fields(session, data_type_id, "episode")

    with open_tmdb(services, data_type_id) as tmdb:
        request = FetchRequest(services, data_type_id, config, ids, tmdb.http, ctx.log)
        gathered = gather_sources(ctx, request, snapshots, {SOURCE: "TMDB 剧集"})
        sources = gathered.sources
        show_raw = gathered.raws[SOURCE]
        show_fetched = SOURCE in gathered.fetched
        local = _resolve(parsed_local, show_raw)
        local_seasons = {season for season, _ in local.values()}
        local_episodes = {(season, n) for season, numbers in local.values() for n in numbers}
        by_source = gathered.candidates
        seasons: list[_Season] = []
        for entry in show_raw.get("seasons") or []:
            number = entry.get("season_number")
            if number is None:
                continue
            if number == 0 and not config.show_missing_specials and 0 not in local_seasons:
                continue
            ctx.check_cancelled()
            raw = season_raws.get(number)
            if raw is not None and "original_names" not in raw:
                raw = None
            fetched = raw is None
            if raw is None:
                ctx.log(f"请求 TMDB 第 {number} 季")
                raw = fetch_season(tmdb, tmdb_id, number, config, show_raw.get("original_language"))
            sources.append(
                source_outcome(
                    f"tmdb_season_{number}",
                    f"TMDB 第 {number} 季",
                    "ok" if fetched else "cached",
                )
            )
            season = _Season(number, raw, fetched, season_candidates(raw, config))
            originals = raw.get("original_names") or {}
            for episode in raw.get("episodes") or []:
                key = (number, episode.get("episode_number"))
                has_file = key in local_episodes
                aired = bool(episode.get("air_date")) and episode["air_date"] <= today
                missing_ok = config.show_missing_episodes and (
                    number != 0 or config.show_missing_specials
                )
                if has_file or (aired and missing_ok):
                    season.episodes[key[1]] = episode_candidates(
                        episode, config, originals.get(str(key[1]))
                    )
                    if episode.get("id"):
                        season.ids[key[1]] = str(episode["id"])
            if season.episodes or number in local_seasons:
                seasons.append(season)

        skip = image_skip(show_fields, set(), SHOW_IMAGES, config.images)
        urls = image_urls(show_fields, by_source, skip)
        season_skip = image_skip(season_fields, set(), SEASON_IMAGES, config.images)
        episode_skip = image_skip(episode_fields, set(), EPISODE_IMAGES, config.images)
        people: list[dict[str, Any]] = [
            actor for values in by_source.values() for actor in values.get("actors", [])
        ]
        for season in seasons:
            season_values = {
                SOURCE: season.values,
                **_season_extras(gathered.raws, season.number, config),
            }
            urls += image_urls(season_fields, season_values, season_skip)
            for number, values in season.episodes.items():
                episode_values = {
                    SOURCE: values,
                    **_episode_extras(gathered.raws, season.number, number),
                }
                urls += image_urls(episode_fields, episode_values, episode_skip)
                people += values.get("actors", [])
        if config.images.actor_images:
            with ctx.session_factory() as session:
                urls += missing_photos(session, people)
        files = download_images(store, tmdb.http, urls, ctx)

    ctx.check_cancelled()
    with services.locks.hold("catalog-write"), ctx.session_factory() as session:
        show = session.get(Item, item_id)
        if show is None:
            raise TaskError("条目在刮削期间被删除")
        order = field_orders(session, data_type_id, builtin_ids(data_type_id))
        for source in gathered.fetched:
            save_snapshot(session, show.id, gathered.raws[source], source)
        _write(session, show, "work", by_source, SHOW_IMAGES, config, order, files)
        save_external_ids(session, show.id, show_external_ids(show_raw))

        targets: dict[str, int] = {}
        kept_seasons: set[int] = set()
        episode_count = 0
        for season in seasons:
            season_row = _child(session, show, "season", season.number)
            kept_seasons.add(season_row.id)
            if season.fetched:
                save_snapshot(session, season_row.id, season.raw)
            _write(
                session,
                season_row,
                "season",
                {SOURCE: season.values, **_season_extras(gathered.raws, season.number, config)},
                SEASON_IMAGES,
                config,
                order,
                files,
            )
            kept: set[int] = set()
            for number, values in season.episodes.items():
                episode_row = _child(session, season_row, "episode", number)
                kept.add(episode_row.id)
                _write(
                    session,
                    episode_row,
                    "episode",
                    {SOURCE: values, **_episode_extras(gathered.raws, season.number, number)},
                    EPISODE_IMAGES,
                    config,
                    order,
                    files,
                )
                episode_count += 1
                if season.ids.get(number):
                    save_external_ids(session, episode_row.id, {"tmdb": season.ids[number]})
            for path, (number, episodes) in local.items():
                if number == season.number and episodes[0] in season.episodes:
                    targets[path] = _child(session, season_row, "episode", episodes[0]).id
            _drop_stale(session, season_row.id, "episode", kept)
        files_on_show = _subtree_files(session, show.id)
        bound = _bind(session, files_on_show, targets, show.id)
        session.flush()
        for season_id in session.scalars(
            select(Item.id).where(
                Item.parent_id == show.id, Item.kind == "season", Item.id.not_in(kept_seasons)
            )
        ).all():
            _drop_stale(session, season_id, "episode", set())
        _drop_stale(session, show.id, "season", kept_seasons)
        session.commit()
        title = show.title

    unbound = len(local) - bound
    ctx.log(
        f"刮削完成：{title}，{len(seasons)} 季 {episode_count} 集，"
        f"挂上视频 {bound} 个，下载图片 {len(files)} 张"
    )
    if unbound > 0:
        ctx.log(f"有 {unbound} 个视频的季集号在 TMDB 上找不到，留在剧上")
    return {
        "item_id": item_id,
        "title": title,
        "fetched": show_fetched,
        "seasons": len(seasons),
        "episodes": episode_count,
        "images": len(files),
        "sources": sources,
    }


def _drop_stale(session: Session, parent_id: int, kind: str, kept: set[int]) -> None:
    """TMDB 上已经没有、也没挂文件的季或集删掉。"""
    child = Item.__table__.alias("child")
    session.execute(
        delete(Item).where(
            Item.parent_id == parent_id,
            Item.kind == kind,
            Item.id.not_in(kept),
            ~exists().where(MediaFile.item_id == Item.id),
            ~exists().where(child.c.parent_id == Item.id),
        )
    )
