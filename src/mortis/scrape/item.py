"""插件数据类型的刮削：每个来源插件各出一份候选值，按字段的来源顺序择优。"""

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from mortis.config.types import type_config
from mortis.db.models import DataType, Item, ItemExternalId, ItemSourceRecord
from mortis.plugin_api import SourceError, SourcePlugin
from mortis.plugins.host import open_host
from mortis.plugins.matching import best_hit
from mortis.scrape.apply import field_orders, image_urls, level_fields, plan_values, write_plan
from mortis.scrape.common import (
    download_images,
    missing_photos,
    save_external_ids,
    save_snapshot,
    source_outcome,
)
from mortis.scrape.images import ImageStore
from mortis.tasks.base import TaskContext, TaskError


@dataclass
class _Gathered:
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    fetched: dict[str, dict[str, Any]] = field(default_factory=dict)
    found_ids: dict[str, str] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)

    def record(self, source_id: str, name: str, outcome: str, detail: str | None = None) -> None:
        self.sources.append(source_outcome(source_id, name, outcome, detail))


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def _tag_people(values: dict[str, Any], people_keys: set[str], provider: str) -> dict[str, Any]:
    """人物按「来源 + 编号」认人，候选值里补上来源。没有编号的人物丢掉。"""
    for key in people_keys & values.keys():
        values[key] = [
            {**entry, "provider": provider, "external_id": str(entry["external_id"])}
            for entry in values[key] or []
            if entry.get("external_id") not in (None, "") and entry.get("name")
        ]
    return values


def _gather(
    ctx: TaskContext,
    sources: list[SourcePlugin],
    ids: dict[str, str],
    snapshots: dict[str, dict[str, Any]],
    titles: list[str],
    year: int | None,
    people_keys: set[str],
    threshold: float,
) -> _Gathered:
    gathered = _Gathered()
    for plugin in sources:
        ctx.check_cancelled()
        with open_host(ctx.services, plugin, ctx.log) as host:
            if not plugin.enabled(host):
                message = f"{plugin.name} 没有启用（缺少设置或密钥），跳过"
                ctx.log(message)
                detail = plugin.disabled_reason(host) or message
                gathered.record(plugin.id, plugin.name, "skipped", detail)
                continue
            raw = snapshots.get(plugin.id)
            used_snapshot = raw is not None
            try:
                if raw is None:
                    external_id = ids.get(plugin.provider)
                    if external_id is None:
                        match = best_hit(host, plugin, titles, year)
                        if match is None or match.score < threshold:
                            best = (
                                f"，最高匹配度 {match.score:.2f}，低于 {threshold:.2f}"
                                f"（{match.hit.title}）"
                                if match
                                else ""
                            )
                            message = f"{plugin.name} 上没找到「{titles[0]}」{best}"
                            ctx.log(message)
                            gathered.record(plugin.id, plugin.name, "not_found", message)
                            continue
                        external_id = match.hit.external_id
                        gathered.found_ids[plugin.provider] = external_id
                        ctx.log(
                            f"{plugin.name} 认出 {match.hit.title}（{external_id}），"
                            f"匹配度 {match.score:.2f}"
                        )
                    raw = plugin.fetch(host, external_id)
                    gathered.fetched[plugin.id] = raw
                values = plugin.candidates(raw)
            except SourceError as error:
                message = f"{plugin.name} 出错，跳过：{error.message}"
                ctx.log(message)
                gathered.record(plugin.id, plugin.name, "failed", message)
                continue
            except Exception as error:  # 插件代码的缺陷不能拖垮整个刮削
                message = f"{plugin.name} 插件出错，跳过：{type(error).__name__}: {error}"
                ctx.log(message)
                gathered.record(plugin.id, plugin.name, "failed", message)
                continue
            gathered.record(plugin.id, plugin.name, "cached" if used_snapshot else "ok")
        gathered.candidates[plugin.id] = _tag_people(values, people_keys, plugin.provider)
    return gathered


def scrape_item(ctx: TaskContext, item_id: int, *, force: bool) -> dict[str, Any]:
    services = ctx.services
    store = ImageStore(services.data_dir)

    with ctx.session_factory() as session:
        item = session.get(Item, item_id)
        if item is None or item.kind != "work":
            raise TaskError("条目不存在，可能已被删除")
        data_type = session.get(DataType, item.data_type_id)
        if data_type is None or not data_type.is_enabled:
            raise TaskError(f"数据类型 {item.data_type_id} 的插件没有启用，不能刮削")
        data_type_id = item.data_type_id
        fields = level_fields(session, data_type_id, "work")
        ids = dict(
            session.execute(
                select(ItemExternalId.provider, ItemExternalId.external_id).where(
                    ItemExternalId.item_id == item.id
                )
            ).all()
        )
        snapshots: dict[str, dict[str, Any]] = {}
        if not force:
            for record in session.scalars(
                select(ItemSourceRecord).where(ItemSourceRecord.item_id == item.id)
            ):
                snapshots[record.source] = json.loads(record.raw)
        titles = [title for title in (item.title, item.original_title) if title]
        year = _year(item.release_date)

    sources = services.plugins.sources(data_type_id)
    if not sources:
        raise TaskError(f"数据类型 {data_type_id} 没有启用的来源插件")
    people_keys = {f.field_key for f in fields if f.value_type == "people"}
    settings = type_config(services.config_store.load_config(), data_type_id)
    gathered = _gather(
        ctx, sources, ids, snapshots, titles, year, people_keys, settings.match_threshold
    )
    if not gathered.candidates:
        raise TaskError("所有来源都没有拿到结果")

    urls = image_urls(fields, gathered.candidates, set())
    with ctx.session_factory() as session:
        for values in gathered.candidates.values():
            for key in people_keys:
                urls += missing_photos(session, values.get(key) or [])
    with services.http_client() as http:
        files = download_images(store, http, urls, ctx)

    ctx.check_cancelled()
    with services.locks.hold("catalog-write"), ctx.session_factory() as session:
        item = session.get(Item, item_id)
        if item is None:
            raise TaskError("条目在刮削期间被删除")
        orders = field_orders(session, data_type_id, [plugin.id for plugin in sources])
        locked = set(json.loads(item.locked_fields))
        plan = plan_values(fields, gathered.candidates, orders, locked)
        for source, raw in gathered.fetched.items():
            save_snapshot(session, item.id, raw, source)
        write_plan(session, item, fields, plan, files)
        save_external_ids(session, item.id, gathered.found_ids)
        session.commit()
        title = item.title

    used = "、".join(plugin.name for plugin in sources if plugin.id in gathered.candidates)
    ctx.log(f"刮削完成：{title}，来源 {used}，下载图片 {len(files)} 张")
    return {
        "item_id": item_id,
        "title": title,
        "fetched": sorted(gathered.fetched),
        "images": len(files),
        "sources": gathered.sources,
    }
