"""插件数据类型的刮削：每个来源插件各出一份候选值，按字段的来源顺序择优。"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from mortis.config.types import type_config
from mortis.db.models import DataType, Item, ItemExternalId, ItemSourceRecord
from mortis.plugin_api import SourceError, SourcePlugin
from mortis.plugins.host import open_host
from mortis.plugins.matching import best_hit
from mortis.scrape.apply import field_orders, image_urls, level_fields, plan_values, write_plan
from mortis.scrape.common import (
    SourceMissing,
    download_images,
    gather,
    missing_photos,
    save_external_ids,
    save_snapshot,
)
from mortis.scrape.images import ImageStore
from mortis.tasks.base import TaskContext, TaskError


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


@dataclass
class _SearchHint:
    titles: list[str]
    year: int | None
    from_primary: bool = False


class _PluginAdapter:
    """把 SourcePlugin 接成取数循环里的 GatherSource。"""

    primary = False

    def __init__(
        self,
        plugin: SourcePlugin,
        ids: dict[str, str],
        hint: _SearchHint,
        people_keys: set[str],
        threshold: float,
        is_primary: bool,
    ) -> None:
        self._plugin = plugin
        self._ids = ids
        self._hint = hint
        self._people_keys = people_keys
        self._threshold = threshold
        self._is_primary = is_primary
        self._host: Any = None
        self.id = plugin.id
        self.name = plugin.name

    @contextmanager
    def session(self, ctx: TaskContext) -> Iterator[None]:
        with open_host(ctx.services, self._plugin, ctx.log) as host:
            self._host = host
            try:
                yield
            finally:
                self._host = None

    def skip(self) -> tuple[str, str] | None:
        if self._plugin.enabled(self._host):
            return None
        message = f"{self.name} 没有启用（缺少设置或密钥），跳过"
        return message, self._plugin.disabled_reason(self._host) or message

    def fetch(self, ctx: TaskContext, found_ids: dict[str, str]) -> dict[str, Any]:
        plugin = self._plugin
        external_id = self._ids.get(plugin.provider)
        if external_id is None:
            titles = self._hint.titles
            year = self._hint.year
            match = best_hit(self._host, plugin, titles, year)
            if match is None or match.score < self._threshold:
                best = (
                    f"，最高匹配度 {match.score:.2f}，低于 {self._threshold:.2f}"
                    f"（{match.hit.title}）"
                    if match
                    else ""
                )
                raise SourceMissing(f"{self.name} 上没找到「{titles[0]}」{best}")
            external_id = match.hit.external_id
            found_ids[plugin.provider] = external_id
            ctx.log(
                f"{self.name} 认出 {match.hit.title}（{external_id}），匹配度 {match.score:.2f}"
            )
        return plugin.fetch(self._host, external_id)

    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]:
        values = self._plugin.candidates(raw)
        if self._plugin.provider in self._ids and (self._is_primary or not self._hint.from_primary):
            found_titles = [
                str(values[key]).strip()
                for key in ("title", "original_title")
                if isinstance(values.get(key), str) and values[key].strip()
            ]
            if found_titles:
                self._hint.titles = list(dict.fromkeys(found_titles))
            found_year = _year(values.get("release_date"))
            if found_year is not None:
                self._hint.year = found_year
            if self._is_primary:
                self._hint.from_primary = True
        return _tag_people(values, self._people_keys, self._plugin.provider)

    def failure(self, error: Exception) -> str:
        if isinstance(error, SourceError):
            return f"{self.name} 出错，跳过：{error.message}"
        return f"{self.name} 插件出错，跳过：{type(error).__name__}: {error}"


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
        rows = session.execute(
            select(
                ItemExternalId.provider, ItemExternalId.external_id, ItemExternalId.is_primary
            ).where(ItemExternalId.item_id == item.id)
        ).all()
        ids = {provider: external_id for provider, external_id, _ in rows}
        primary_provider = next(
            (provider for provider, _, is_primary in rows if is_primary), None
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
    hint = _SearchHint(titles=titles, year=year)
    ordered_sources = sorted(
        sources,
        key=lambda plugin: (
            0 if plugin.provider == primary_provider else (1 if plugin.provider in ids else 2)
        ),
    )
    adapters = [
        _PluginAdapter(
            plugin,
            ids,
            hint,
            people_keys,
            settings.match_threshold,
            plugin.provider == primary_provider,
        )
        for plugin in ordered_sources
    ]
    gathered = gather(ctx, adapters, snapshots)
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
        for source in gathered.fetched:
            save_snapshot(session, item.id, gathered.raws[source], source)
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
