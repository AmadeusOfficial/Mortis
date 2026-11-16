import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session, aliased

from mortis.config.models import MovieScrapeConfig
from mortis.db.models import (
    DataType,
    Item,
    ItemExternalId,
    Library,
    utc_now,
)
from mortis.items.identity import find_by_tmdb
from mortis.organize.service import unscraped_work_ids
from mortis.output.service import after_scrape, has_video
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
from mortis.scrape.tv import scrape_show
from mortis.sources.tmdb.client import open_tmdb
from mortis.sources.tmdb.common import SOURCE
from mortis.sources.tmdb.movie import (
    collection_candidates,
    fetch_collection,
    fetch_movie,
    movie_candidates,
    movie_external_ids,
)
from mortis.tasks.base import Followup, TaskContext, TaskError, TaskResult

# 图片字段 → 设置里控制它的开关。不在表里的图片种类 TMDB 不提供，先跳过。
WORK_IMAGES = {"poster": "poster", "fanart": "fanart", "clearlogo": "clearlogo", "keyart": "keyart"}
COLLECTION_IMAGES = {"poster": "collection_poster", "fanart": "collection_fanart"}


def drop_empty_collections(session: Session, library_id: int) -> None:
    child = aliased(Item)
    session.execute(
        delete(Item).where(
            Item.library_id == library_id,
            Item.kind == "collection",
            ~exists().where(child.parent_id == Item.id),
        )
    )


@dataclass
class _Collection:
    tmdb_id: int
    raw: dict[str, Any]
    fetched: bool
    candidates: dict[str, Any]


def scrape_movie(ctx: TaskContext, item_id: int, *, force: bool) -> dict[str, Any]:
    services = ctx.services
    config: MovieScrapeConfig = services.config_store.load_config().movie
    store = ImageStore(services.data_dir)

    with ctx.session_factory() as session:
        item = session.get(Item, item_id)
        if item is None or item.kind != "work":
            raise TaskError("条目不存在，可能已被删除")
        tmdb_id = tmdb_id_of(session, item.id)
        if tmdb_id is None:
            raise TaskError(f"「{item.title}」没有 TMDB 编号，先给它匹配一部电影")
        library_id, data_type_id = item.library_id, item.data_type_id
        work_fields = level_fields(session, data_type_id, "work")
        collection_fields = level_fields(session, data_type_id, "collection")
        raw = load_snapshot(session, item.id, force)
        work_skip = image_skip(work_fields, set(), WORK_IMAGES, config.images)

    # 请求和下载不持锁；写库时再进锁，按当时的锁定集合选值。
    with open_tmdb(services) as tmdb:
        fetched = raw is None
        if raw is None:
            ctx.log(f"请求 TMDB 电影 {tmdb_id}")
            raw = fetch_movie(tmdb, tmdb_id, config)
        ctx.check_cancelled()
        candidates = movie_candidates(raw, config)
        urls = image_urls(work_fields, {SOURCE: candidates}, work_skip)
        if config.images.actor_images:
            with ctx.session_factory() as session:
                urls += missing_photos(session, candidates.get("actors", []))

        collection: _Collection | None = None
        info = raw.get("belongs_to_collection") or {}
        if info.get("id"):
            collection_id = int(info["id"])
            with ctx.session_factory() as session:
                existing = find_by_tmdb(session, library_id, collection_id, kind="collection")
                collection_raw = load_snapshot(session, existing.id if existing else None, force)
            collection_fetched = collection_raw is None
            if collection_raw is None:
                ctx.log(f"请求 TMDB 系列 {collection_id}")
                collection_raw = fetch_collection(tmdb, collection_id, config)
            collection_values = collection_candidates(collection_raw, config)
            collection_skip = image_skip(collection_fields, set(), COLLECTION_IMAGES, config.images)
            urls += image_urls(collection_fields, {SOURCE: collection_values}, collection_skip)
            collection = _Collection(
                collection_id, collection_raw, collection_fetched, collection_values
            )
        files = download_images(store, tmdb, urls, ctx)

    ctx.check_cancelled()
    with services.locks.hold("catalog-write"), ctx.session_factory() as session:
        item = session.get(Item, item_id)
        if item is None:
            raise TaskError("条目在刮削期间被删除")
        order = source_order(session, data_type_id)
        locked = set(json.loads(item.locked_fields))
        plan = plan_values(
            work_fields,
            {SOURCE: candidates},
            order,
            image_skip(work_fields, locked, WORK_IMAGES, config.images),
        )
        if fetched:
            save_snapshot(session, item.id, raw)
        write_plan(session, item, work_fields, plan, files)
        save_external_ids(session, item.id, movie_external_ids(raw))

        if collection is not None:
            parent = find_by_tmdb(session, library_id, collection.tmdb_id, kind="collection")
            if parent is None:
                now = utc_now()
                parent = Item(
                    library_id=library_id,
                    data_type_id=data_type_id,
                    kind="collection",
                    title=collection.candidates.get("title") or f"TMDB 系列 {collection.tmdb_id}",
                    created_at=now,
                    updated_at=now,
                )
                session.add(parent)
                session.flush()
                session.add(
                    ItemExternalId(
                        item_id=parent.id,
                        provider="tmdb",
                        external_id=str(collection.tmdb_id),
                        is_primary=1,
                    )
                )
            if collection.fetched:
                save_snapshot(session, parent.id, collection.raw)
            parent_plan = plan_values(
                collection_fields,
                {SOURCE: collection.candidates},
                order,
                image_skip(
                    collection_fields,
                    set(json.loads(parent.locked_fields)),
                    COLLECTION_IMAGES,
                    config.images,
                ),
            )
            write_plan(session, parent, collection_fields, parent_plan, files)
            item.parent_id = parent.id
        else:
            item.parent_id = None
        session.flush()
        drop_empty_collections(session, library_id)
        session.commit()
        title = item.title

    ctx.log(f"刮削完成：{title}，下载图片 {len(files)} 张")
    return {"item_id": item_id, "title": title, "fetched": fetched, "images": len(files)}


def handle_scrape(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    item_id = int(payload["item_id"])
    force = bool(payload.get("force"))
    with ctx.session_factory() as session:
        structure = session.scalar(
            select(DataType.structure)
            .join(Item, Item.data_type_id == DataType.id)
            .where(Item.id == item_id)
        )
    config = ctx.services.config_store.load_config()
    if structure == "series":
        result = scrape_show(ctx, item_id, force=force)
        config_section = config.tv
    else:
        result = scrape_movie(ctx, item_id, force=force)
        config_section = config.movie
    with ctx.session_factory() as session:
        followups = after_scrape(config_section, item_id)
        if not has_video(session, item_id):
            followups = []
    return TaskResult(result=result, followups=followups)


def handle_scrape_library(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    library_id = int(payload["library_id"])
    force = bool(payload.get("force"))
    with ctx.session_factory() as session:
        if session.get(Library, library_id) is None:
            raise TaskError("媒体库不存在，可能已被删除")
        if force:
            ids = list(
                session.scalars(
                    select(Item.id)
                    .where(
                        Item.library_id == library_id,
                        Item.kind == "work",
                        exists().where(
                            ItemExternalId.item_id == Item.id, ItemExternalId.provider == "tmdb"
                        ),
                    )
                    .order_by(Item.id)
                )
            )
        else:
            ids = unscraped_work_ids(session, library_id)
    ctx.log(f"排了 {len(ids)} 个刮削任务")
    return TaskResult(
        result={"scrape_queued": len(ids)},
        followups=[Followup("scrape", {"item_id": item_id, "force": force}) for item_id in ids],
    )
