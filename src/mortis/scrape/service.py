import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session, aliased

from mortis.config.models import MovieImages, MovieScrapeConfig
from mortis.db.models import (
    Item,
    ItemExternalId,
    ItemSourceRecord,
    Library,
    Person,
    PersonExternalId,
    TypeField,
    utc_now,
)
from mortis.items.identity import find_by_tmdb
from mortis.organize.service import unscraped_movie_ids
from mortis.scrape.apply import image_urls, level_fields, plan_values, source_order, write_plan
from mortis.scrape.images import ImageError, ImageStore
from mortis.sources.tmdb.client import TmdbClient, open_tmdb
from mortis.sources.tmdb.movie import (
    SOURCE,
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


def _skip(
    fields: list[TypeField], locked: set[str], toggles: dict[str, str], images: MovieImages
) -> set[str]:
    skip = set(locked)
    for field in fields:
        if field.value_type == "images":
            flag = toggles.get(field.field_key)
            if flag is None or not getattr(images, flag):
                skip.add(field.field_key)
    return skip


def tmdb_id_of(session: Session, item_id: int) -> int | None:
    value = session.scalar(
        select(ItemExternalId.external_id).where(
            ItemExternalId.item_id == item_id, ItemExternalId.provider == "tmdb"
        )
    )
    return int(value) if value and value.isdigit() else None


def _save_snapshot(session: Session, item_id: int, raw: dict[str, Any]) -> None:
    record = session.get(ItemSourceRecord, (item_id, SOURCE))
    text = json.dumps(raw, ensure_ascii=False)
    if record is None:
        session.add(
            ItemSourceRecord(item_id=item_id, source=SOURCE, fetched_at=utc_now(), raw=text)
        )
    else:
        record.raw = text
        record.fetched_at = utc_now()


def _snapshot(session: Session, item_id: int | None, force: bool) -> dict[str, Any] | None:
    if item_id is None or force:
        return None
    record = session.get(ItemSourceRecord, (item_id, SOURCE))
    return json.loads(record.raw) if record is not None else None


def _download(
    store: ImageStore, tmdb: TmdbClient, urls: list[str], ctx: TaskContext
) -> dict[str, str]:
    files: dict[str, str] = {}
    for url in dict.fromkeys(urls):
        ctx.check_cancelled()
        try:
            files[url] = store.download(tmdb.http, url)
        except ImageError as error:
            ctx.log(f"图片下载失败：{url}（{error}）")
    return files


def _missing_photos(session: Session, actors: list[dict[str, Any]]) -> list[str]:
    ids = [str(actor["tmdb_id"]) for actor in actors if actor.get("photo_url")]
    has_photo = set(
        session.scalars(
            select(PersonExternalId.external_id)
            .join(Person, Person.id == PersonExternalId.person_id)
            .where(
                PersonExternalId.provider == "tmdb",
                PersonExternalId.external_id.in_(ids),
                Person.photo_path.is_not(None),
            )
        )
    )
    return [
        actor["photo_url"]
        for actor in actors
        if actor.get("photo_url") and str(actor["tmdb_id"]) not in has_photo
    ]


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
        raw = _snapshot(session, item.id, force)
        work_skip = _skip(work_fields, set(), WORK_IMAGES, config.images)

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
                urls += _missing_photos(session, candidates.get("actors", []))

        collection: _Collection | None = None
        info = raw.get("belongs_to_collection") or {}
        if info.get("id"):
            collection_id = int(info["id"])
            with ctx.session_factory() as session:
                existing = find_by_tmdb(session, library_id, collection_id, kind="collection")
                collection_raw = _snapshot(session, existing.id if existing else None, force)
            collection_fetched = collection_raw is None
            if collection_raw is None:
                ctx.log(f"请求 TMDB 系列 {collection_id}")
                collection_raw = fetch_collection(tmdb, collection_id, config)
            collection_values = collection_candidates(collection_raw, config)
            collection_skip = _skip(collection_fields, set(), COLLECTION_IMAGES, config.images)
            urls += image_urls(collection_fields, {SOURCE: collection_values}, collection_skip)
            collection = _Collection(
                collection_id, collection_raw, collection_fetched, collection_values
            )
        files = _download(store, tmdb, urls, ctx)

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
            _skip(work_fields, locked, WORK_IMAGES, config.images),
        )
        if fetched:
            _save_snapshot(session, item.id, raw)
        write_plan(session, item, work_fields, plan, files)
        for provider, value in movie_external_ids(raw).items():
            row = session.get(ItemExternalId, (item.id, provider))
            if row is None:
                session.add(
                    ItemExternalId(
                        item_id=item.id, provider=provider, external_id=value, is_primary=0
                    )
                )
            else:
                row.external_id = value

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
                _save_snapshot(session, parent.id, collection.raw)
            parent_plan = plan_values(
                collection_fields,
                {SOURCE: collection.candidates},
                order,
                _skip(
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
    result = scrape_movie(ctx, int(payload["item_id"]), force=bool(payload.get("force")))
    return TaskResult(result=result)


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
            ids = unscraped_movie_ids(session, library_id)
    ctx.log(f"排了 {len(ids)} 个刮削任务")
    return TaskResult(
        result={"scrape_queued": len(ids)},
        followups=[Followup("scrape", {"item_id": item_id, "force": force}) for item_id in ids],
    )
