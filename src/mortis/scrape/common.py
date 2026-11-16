"""电影和电视剧刮削共用的快照、图片和编号读写。"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.db.models import (
    ItemExternalId,
    ItemSourceRecord,
    Person,
    PersonExternalId,
    TypeField,
    utc_now,
)
from mortis.scrape.images import ImageError, ImageStore
from mortis.sources.tmdb.client import TmdbClient
from mortis.tasks.base import TaskContext

SOURCE = "tmdb"


def image_skip(
    fields: list[TypeField], locked: set[str], toggles: dict[str, str], images: Any
) -> set[str]:
    """锁定的键，加上设置里关掉的或不在开关表里的图片字段。"""
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


def save_snapshot(session: Session, item_id: int, raw: dict[str, Any]) -> None:
    record = session.get(ItemSourceRecord, (item_id, SOURCE))
    text = json.dumps(raw, ensure_ascii=False)
    if record is None:
        session.add(
            ItemSourceRecord(item_id=item_id, source=SOURCE, fetched_at=utc_now(), raw=text)
        )
    else:
        record.raw = text
        record.fetched_at = utc_now()


def load_snapshot(session: Session, item_id: int | None, force: bool) -> dict[str, Any] | None:
    if item_id is None or force:
        return None
    record = session.get(ItemSourceRecord, (item_id, SOURCE))
    return json.loads(record.raw) if record is not None else None


def save_external_ids(session: Session, item_id: int, ids: dict[str, str]) -> None:
    for provider, value in ids.items():
        row = session.get(ItemExternalId, (item_id, provider))
        if row is None:
            session.add(
                ItemExternalId(item_id=item_id, provider=provider, external_id=value, is_primary=0)
            )
        else:
            row.external_id = value


def download_images(
    store: ImageStore, tmdb: TmdbClient, urls: list[str], ctx: TaskContext
) -> dict[str, str]:
    wanted = list(dict.fromkeys(urls))
    files = {url: rel for url in wanted if (rel := store.cached(url)) is not None}
    if files:
        ctx.log(f"库里已有 {len(files)} 张图片，不再下载")
    for url in wanted:
        if url in files:
            continue
        ctx.check_cancelled()
        try:
            files[url] = store.download(tmdb.http, url)
        except ImageError as error:
            ctx.log(f"图片下载失败：{url}（{error}）")
    return files


def missing_photos(session: Session, actors: list[dict[str, Any]]) -> list[str]:
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
