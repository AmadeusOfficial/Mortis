"""刮削一部剧：剧、季、集三层，已播出的缺失集也建行，视频按季集号挂到集上。"""

import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from mortis.config.models import TvScrapeConfig
from mortis.config.types import type_config
from mortis.db.models import Item, MediaFile, utc_now
from mortis.organize.episodes import parse_episode
from mortis.organize.names import subtitle_belongs_to
from mortis.scrape.apply import image_urls, level_fields, plan_values, source_order, write_plan
from mortis.scrape.common import (
    download_images,
    image_skip,
    load_snapshot,
    missing_photos,
    save_external_ids,
    save_snapshot,
    tmdb_id_of,
)
from mortis.scrape.images import ImageStore
from mortis.sources.tmdb.client import open_tmdb
from mortis.sources.tmdb.tv import (
    episode_candidates,
    fetch_season,
    fetch_show,
    season_candidates,
    show_candidates,
    show_external_ids,
)
from mortis.tasks.base import TaskContext, TaskError

SHOW_IMAGES = {"poster": "poster", "fanart": "fanart", "clearlogo": "clearlogo", "keyart": "keyart"}
SEASON_IMAGES = {"poster": "season_poster"}
EPISODE_IMAGES = {"thumb": "episode_thumb"}


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


def _local_numbers(files: list[MediaFile]) -> dict[str, tuple[int, tuple[int, ...]]]:
    """视频路径 → (季号, 文件里的各集集号)。季号没写时当第 1 季。"""
    numbers: dict[str, tuple[int, tuple[int, ...]]] = {}
    for row in files:
        if row.file_kind != "video":
            continue
        folder = os.path.basename(os.path.dirname(row.path))
        parsed = parse_episode(os.path.splitext(os.path.basename(row.path))[0], folder)
        if parsed is not None:
            numbers[row.path] = (1 if parsed.season is None else parsed.season, parsed.episodes)
    return numbers


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
    values: dict[str, Any],
    toggles: dict[str, str],
    config: TvScrapeConfig,
    order: list[str],
    files: dict[str, str],
) -> None:
    fields = level_fields(session, item.data_type_id, level)
    locked = set(json.loads(item.locked_fields))
    plan = plan_values(
        fields, {"tmdb": values}, order, image_skip(fields, locked, toggles, config.images)
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


def _show_config(ctx: TaskContext, item_id: int) -> TvScrapeConfig:
    """电视剧和动漫各用自己的一节设置。"""
    config = ctx.services.config_store.load_config()
    with ctx.session_factory() as session:
        data_type_id = session.scalar(select(Item.data_type_id).where(Item.id == item_id))
    return type_config(config, data_type_id or "")


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
        show_raw = load_snapshot(session, show.id, force)
        season_raws = {
            row.index_number: load_snapshot(session, row.id, force)
            for row in session.scalars(
                select(Item).where(Item.parent_id == show.id, Item.kind == "season")
            )
        }
        local = _local_numbers(_subtree_files(session, show.id))
        show_fields = level_fields(session, data_type_id, "work")
        season_fields = level_fields(session, data_type_id, "season")
        episode_fields = level_fields(session, data_type_id, "episode")
    local_seasons = {season for season, _ in local.values()}
    local_episodes = {(season, n) for season, numbers in local.values() for n in numbers}

    with open_tmdb(services) as tmdb:
        show_fetched = show_raw is None
        if show_raw is None:
            ctx.log(f"请求 TMDB 剧 {tmdb_id}")
            show_raw = fetch_show(tmdb, tmdb_id, config)
        show_values = show_candidates(show_raw, config)
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
        urls = image_urls(show_fields, {"tmdb": show_values}, skip)
        season_skip = image_skip(season_fields, set(), SEASON_IMAGES, config.images)
        episode_skip = image_skip(episode_fields, set(), EPISODE_IMAGES, config.images)
        people: list[dict[str, Any]] = list(show_values.get("actors", []))
        for season in seasons:
            urls += image_urls(season_fields, {"tmdb": season.values}, season_skip)
            for values in season.episodes.values():
                urls += image_urls(episode_fields, {"tmdb": values}, episode_skip)
                people += values.get("actors", [])
        if config.images.actor_images:
            with ctx.session_factory() as session:
                urls += missing_photos(session, people)
        files = download_images(store, tmdb, urls, ctx)

    ctx.check_cancelled()
    with services.locks.hold("catalog-write"), ctx.session_factory() as session:
        show = session.get(Item, item_id)
        if show is None:
            raise TaskError("条目在刮削期间被删除")
        order = source_order(session, data_type_id)
        if show_fetched:
            save_snapshot(session, show.id, show_raw)
        _write(session, show, "work", show_values, SHOW_IMAGES, config, order, files)
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
                session, season_row, "season", season.values, SEASON_IMAGES, config, order, files
            )
            kept: set[int] = set()
            for number, values in season.episodes.items():
                episode_row = _child(session, season_row, "episode", number)
                kept.add(episode_row.id)
                _write(
                    session, episode_row, "episode", values, EPISODE_IMAGES, config, order, files
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
