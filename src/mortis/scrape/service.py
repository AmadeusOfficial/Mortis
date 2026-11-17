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
from mortis.output.service import after_scrape, arrange_config, has_video
from mortis.scrape.apply import field_orders, image_urls, level_fields, plan_values, write_plan
from mortis.scrape.common import (
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
from mortis.scrape.item import scrape_item
from mortis.scrape.tv import scrape_show
from mortis.sources.builtin import FetchRequest, builtin_ids
from mortis.sources.tmdb.client import open_tmdb
from mortis.sources.tmdb.common import SOURCE
from mortis.sources.tmdb.movie import (
    collection_candidates,
    fetch_collection,
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
    config: MovieScrapeConfig = services.config_store.load_config().types.movie
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
        ids = external_ids(session, item.id)
        snapshots = load_snapshots(session, item.id, force)
        work_skip = image_skip(work_fields, set(), WORK_IMAGES, config.images)

    # 请求和下载不持锁；写库时再进锁，按当时的锁定集合选值。
    with open_tmdb(services, data_type_id) as tmdb:
        request = FetchRequest(services, data_type_id, config, ids, tmdb.http, ctx.log)
        gathered = gather_sources(ctx, request, snapshots, {SOURCE: "TMDB 电影"})
        sources = gathered.sources
        raw = gathered.raws[SOURCE]
        fetched = SOURCE in gathered.fetched
        ctx.check_cancelled()
        candidates = gathered.candidates
        urls = image_urls(work_fields, candidates, work_skip)
        if config.images.actor_images:
            with ctx.session_factory() as session:
                for values in candidates.values():
                    urls += missing_photos(session, values.get("actors", []))

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
            sources.append(
                source_outcome(
                    "tmdb_collection",
                    "TMDB 系列",
                    "ok" if collection_fetched else "cached",
                )
            )
        files = download_images(store, tmdb.http, urls, ctx)

    ctx.check_cancelled()
    with services.locks.hold("catalog-write"), ctx.session_factory() as session:
        item = session.get(Item, item_id)
        if item is None:
            raise TaskError("条目在刮削期间被删除")
        order = field_orders(session, data_type_id, builtin_ids(data_type_id))
        locked = set(json.loads(item.locked_fields))
        plan = plan_values(
            work_fields,
            candidates,
            order,
            image_skip(work_fields, locked, WORK_IMAGES, config.images),
        )
        for source in gathered.fetched:
            save_snapshot(session, item.id, gathered.raws[source], source)
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
    return {
        "item_id": item_id,
        "title": title,
        "fetched": fetched,
        "images": len(files),
        "sources": sources,
    }


def handle_scrape(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    item_id = int(payload["item_id"])
    force = bool(payload.get("force"))
    with ctx.session_factory() as session:
        row = session.execute(
            select(DataType.structure, DataType.plugin_id, Item.data_type_id)
            .join(Item, Item.data_type_id == DataType.id)
            .where(Item.id == item_id)
        ).first()
    structure = row.structure if row is not None else None
    data_type_id = row.data_type_id if row is not None else ""
    if row is not None and row.plugin_id is not None:
        # 插件类型不写 NFO、不整理
        return TaskResult(result=scrape_item(ctx, item_id, force=force))
    config = ctx.services.config_store.load_config()
    if structure == "series":
        result = scrape_show(ctx, item_id, force=force)
    else:
        result = scrape_movie(ctx, item_id, force=force)
    config_section = arrange_config(config, data_type_id)
    with ctx.session_factory() as session:
        followups = after_scrape(config_section, item_id)
        if not has_video(session, item_id):
            followups = []
    return TaskResult(result=result, followups=followups)


def handle_scrape_library(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    library_id = int(payload["library_id"])
    force = bool(payload.get("force"))
    with ctx.session_factory() as session:
        library = session.get(Library, library_id)
        if library is None:
            raise TaskError("媒体库不存在，可能已被删除")
        data_type = session.get(DataType, library.data_type_id)
        by_plugin = data_type is not None and data_type.plugin_id is not None
        if force:
            has_id = exists().where(ItemExternalId.item_id == Item.id)
            if not by_plugin:
                has_id = has_id.where(ItemExternalId.provider == "tmdb")
            ids = list(
                session.scalars(
                    select(Item.id)
                    .where(Item.library_id == library_id, Item.kind == "work", has_id)
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
