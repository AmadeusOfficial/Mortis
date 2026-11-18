"""详情页换图：候选每次从各来源的快照现算，选中后下载到数据目录、记来源并锁定。"""

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx2 as httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.config.models import MovieScrapeConfig, TvScrapeConfig
from mortis.config.types import type_config
from mortis.db.models import Item, ItemImage, TypeField, utc_now
from mortis.items.edit import MANUAL, ItemError
from mortis.items.subtitle import refresh_subtitle
from mortis.scrape.apply import field_orders, level_fields, replace_image
from mortis.scrape.common import load_snapshot, load_snapshots
from mortis.scrape.images import ImageError, ImageStore
from mortis.scrape.tv import season_extras
from mortis.services import Services
from mortis.sources.builtin import FetchRequest, builtin_ids, builtin_sources, is_builtin_type
from mortis.sources.tmdb.movie import collection_candidates
from mortis.sources.tmdb.tv import season_candidates

SINGLE_LEVELS = frozenset({"work", "collection", "season"})
MULTI_IMAGE_KEYS = frozenset({"extrafanart"})
"""一个字段存多张的图，换一张没有意义。"""
MAX_UPLOAD = 20 * 1024 * 1024
UPLOAD_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_TMDB_IMAGE = re.compile(
    r"https://image\.tmdb\.org/t/p/[a-z0-9]+/([A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp|svg))"
)


def image_field(session: Session, item: Item, key: str) -> TypeField:
    if item.kind not in SINGLE_LEVELS:
        raise ItemError("集的图片不能在这里换")
    field = next(
        (f for f in level_fields(session, item.data_type_id, item.kind) if f.field_key == key),
        None,
    )
    if field is None or field.value_type != "images" or key in MULTI_IMAGE_KEYS:
        raise ItemError(f"「{key}」不是能换的图片字段")
    return field


def _builtin_candidates(
    services: Services, session: Session, item: Item
) -> dict[str, Mapping[str, Any]]:
    section = type_config(services.config_store.load_config(), item.data_type_id)
    if item.kind == "collection":
        raw = load_snapshot(session, item.id, False)
        if raw is None or not isinstance(section, MovieScrapeConfig):
            return {}
        return {"tmdb": collection_candidates(raw, section)}
    if item.kind == "season":
        if not isinstance(section, TvScrapeConfig):
            return {}
        found: dict[str, Mapping[str, Any]] = {}
        raw = load_snapshot(session, item.id, False)
        if raw is not None:
            found["tmdb"] = season_candidates(raw, section)
        if item.parent_id is not None:
            show_raws = load_snapshots(session, item.parent_id, False)
            found.update(season_extras(show_raws, item.index_number or 0, section))
        return found
    assert isinstance(section, MovieScrapeConfig | TvScrapeConfig)
    raws = load_snapshots(session, item.id, False)
    found = {}
    with services.http_client() as http:
        request = FetchRequest(services, item.data_type_id, section, {}, http, lambda _m: None)
        for source in builtin_sources(item.data_type_id):
            raw = raws.get(source.id)
            if raw is None or source.fetcher is None:
                continue
            try:
                found[source.id] = source.fetcher.candidates(raw, request)
            except Exception:  # 旧快照结构不对时，这个来源不给候选，别的照常列
                continue
    return found


def _candidates(
    services: Services, session: Session, item: Item
) -> tuple[dict[str, Mapping[str, Any]], list[str], dict[str, str]]:
    """各来源的候选值、参与排序的来源 id、来源名字。"""
    data_type_id = item.data_type_id
    if is_builtin_type(data_type_id):
        names = {source.id: source.name for source in builtin_sources(data_type_id)}
        return _builtin_candidates(services, session, item), builtin_ids(data_type_id), names
    plugins = services.plugins.sources(data_type_id)
    names = {plugin.id: plugin.name for plugin in plugins}
    found: dict[str, Mapping[str, Any]] = {}
    if item.kind == "work":
        raws = load_snapshots(session, item.id, False)
        for plugin in plugins:
            if plugin.id not in raws:
                continue
            try:
                found[plugin.id] = plugin.candidates(raws[plugin.id])
            except Exception:  # 插件代码的缺陷不能拖垮详情页
                continue
    return found, [plugin.id for plugin in plugins], names


def _current_file(session: Session, item: Item, field: TypeField) -> str | None:
    if field.stored_in == "items_column":
        return getattr(item, field.column_name or field.field_key)
    return session.scalar(
        select(ItemImage.file_path)
        .where(ItemImage.item_id == item.id, ItemImage.image_kind == field.image_kind)
        .order_by(ItemImage.position)
        .limit(1)
    )


def preview_url(item_id: int, key: str, url: str) -> str:
    """缩略图经后端取，沿用宿主的代理。TMDB 的图换成小尺寸。"""
    match = _TMDB_IMAGE.fullmatch(url)
    if match:
        return f"/api/tmdb/image/w300/{match.group(1)}"
    return f"/api/items/{item_id}/images/{key}/preview?url={quote(url, safe='')}"


def image_choices(
    services: Services, session: Session, item: Item, key: str
) -> list[dict[str, Any]]:
    """按这个字段的来源顺序列出候选，同一地址只列一次。"""
    field = image_field(session, item, key)
    found, ids, names = _candidates(services, session, item)
    orders = field_orders(session, item.data_type_id, ids)
    current = _current_file(session, item, field)
    store = ImageStore(services.data_dir)
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for source in orders.get(key, orders[""]):
        for image in found.get(source, {}).get(key) or []:
            url = image.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            rows.append(
                {
                    "url": url,
                    "source": source,
                    "source_name": names.get(source, source),
                    "language": image.get("language"),
                    "region": image.get("region"),
                    "width": image.get("width"),
                    "height": image.get("height"),
                    "preview_url": preview_url(item.id, key, url),
                    "selected": current is not None and store.cached(url) == current,
                }
            )
    return rows


def _save(
    services: Services,
    session: Session,
    item: Item,
    field: TypeField,
    image: Mapping[str, Any],
    source: str,
    file_path: str,
) -> None:
    with services.locks.hold("catalog-write"):
        session.refresh(item)
        if field.stored_in == "items_column":
            setattr(item, field.column_name or field.field_key, file_path)
        else:
            replace_image(
                session, item, field.image_kind or field.field_key, image, source, file_path
            )
        sources = json.loads(item.field_sources)
        sources[field.field_key] = source
        locked = json.loads(item.locked_fields)
        if field.is_lockable and field.field_key not in locked:
            locked.append(field.field_key)
        item.field_sources = json.dumps(sources, ensure_ascii=False)
        item.locked_fields = json.dumps(locked, ensure_ascii=False)
        item.updated_at = utc_now()
        refresh_subtitle(session, item)
        session.commit()


def choose_image(services: Services, session: Session, item: Item, key: str, url: str) -> None:
    """候选里的图记原来源；候选外的网址当作手动添加，记 manual。两种都锁定。"""
    field = image_field(session, item, key)
    url = url.strip()
    chosen = next(
        (row for row in image_choices(services, session, item, key) if row["url"] == url), None
    )
    if chosen is None and not url.startswith(("http://", "https://")):
        raise ItemError("图片网址要以 http:// 或 https:// 开头")
    store = ImageStore(services.data_dir)
    file_path = store.cached(url)
    if file_path is None:
        with services.http_client() as http:
            try:
                file_path = store.download(http, url)
            except ImageError as error:
                raise ItemError(f"图片下载失败：{error}") from None
    image = chosen or {"url": url, "language": None, "width": None, "height": None}
    _save(services, session, item, field, image, chosen["source"] if chosen else MANUAL, file_path)


def _looks_like(content: bytes, ext: str) -> bool:
    if ext == ".jpg":
        return content.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    return content[:4] == b"RIFF" and content[8:12] == b"WEBP"


def upload_image(
    services: Services, session: Session, item: Item, key: str, content: bytes, content_type: str
) -> None:
    field = image_field(session, item, key)
    ext = UPLOAD_TYPES.get(content_type.split(";")[0].strip().lower())
    if ext is None or not _looks_like(content, ext):
        raise ItemError("只能上传 JPG、PNG 或 WebP 图片")
    if len(content) > MAX_UPLOAD:
        raise ItemError("图片不能超过 20 MB")
    file_path = ImageStore(services.data_dir).save(content, ext)
    image = {"url": None, "language": None, "width": None, "height": None}
    _save(services, session, item, field, image, MANUAL, file_path)


def fetch_preview(
    services: Services, session: Session, item: Item, key: str, url: str
) -> tuple[bytes, str]:
    """只代理当前候选里的地址。"""
    if url not in {row["url"] for row in image_choices(services, session, item, key)}:
        raise ItemError("这张图不在候选里", status_code=404)
    with services.http_client() as http:
        try:
            response = http.get(url)
        except httpx.HTTPError as error:
            raise ItemError(f"图片取不到：{type(error).__name__}", status_code=502) from None
    content_type = response.headers.get("content-type", "")
    if response.status_code != 200 or not content_type.startswith("image/"):
        raise ItemError("图片取不到", status_code=502)
    return response.content, content_type
