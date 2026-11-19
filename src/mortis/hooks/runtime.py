"""`hook` 任务的处理函数和真正的 HookHost。

一个任务对应「一个钩子收到一个事件」。钩子抛异常只会让这个任务失败，不影响刮削和其他钩子；
连续失败 FUSE_LIMIT 次后熔断：进程内立即停用，并把钩子 id 写进设置文件的 `[plugins] disabled`，
设置页从注册表的 `hook_status` 读到停用原因。钩子超时只能记录，宿主不会强行结束它。
钩子任务不发布事件，钩子失败也不触发 task.failed（runner 里按任务类型跳过）。
"""

import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any

import httpx2 as httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from mortis.db.models import Item, ItemTag, Tag, TypeField
from mortis.hooks.dispatch import HOOK_TASK
from mortis.hooks.netguard import Resolver, fetch_json
from mortis.items import tags as tag_service
from mortis.items.detail import read_value, year_of
from mortis.plugin_api import HookPlugin, NoSecrets, NoSettings
from mortis.plugin_api.events import event_from_dict
from mortis.plugin_api.hooks import (
    DEFAULT_LIMITS,
    HookBudget,
    HookError,
    HookLimits,
    ItemView,
    TagResult,
    require_tag_permission,
)
from mortis.plugins.host import plugin_secrets, plugin_settings
from mortis.tasks.base import TaskCancelled, TaskContext, TaskError, TaskResult

__all__ = ["HOOK_TASK", "HookHostImpl", "build_view", "handle_hook"]

SLOW_SECONDS = 60.0
"""钩子跑得比这久就记一次失败（只记录，不强杀）。"""
DATA_FOLDER = "plugin_data"
_LOG = logging.getLogger(__name__)


def build_view(session: Any, item_id: int) -> ItemView | None:
    item = session.get(Item, item_id)
    if item is None:
        return None
    tags: dict[str, list[str]] = {}
    for kind, name in session.execute(
        select(Tag.tag_kind, Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(ItemTag.item_id == item.id)
        .order_by(Tag.tag_kind, ItemTag.position)
    ):
        tags.setdefault(kind, []).append(name)
    fields: dict[str, Any] = {}
    for spec in session.scalars(
        select(TypeField).where(
            TypeField.data_type_id == item.data_type_id,
            TypeField.applies_to == item.kind,
            TypeField.stored_in.in_(("items_column", "fields")),
            TypeField.value_type != "images",
        )
    ):
        fields[spec.field_key] = read_value(session, item, spec)
    return ItemView(
        id=item.id,
        kind=item.kind,
        data_type=item.data_type_id,
        title=item.title,
        original_title=item.original_title,
        year=year_of(item.release_date),
        tags={kind: tuple(names) for kind, names in tags.items()},
        fields=fields,
        locked_fields=tuple(str(key) for key in json.loads(item.locked_fields)),
    )


def _tag_result(result: tag_service.TagWriteResult) -> TagResult:
    return TagResult(
        applied=result.applied,
        unchanged=result.unchanged,
        locked=result.locked,
        removed_by_user=result.removed_by_user,
        not_owned=result.not_owned,
    )


class HookHostImpl:
    """一个钩子处理一个事件时拿到的宿主。额度按这一次事件计。"""

    def __init__(
        self,
        ctx: TaskContext,
        plugin_id: str,
        writes: tuple[str, ...],
        *,
        label: str = "",
        hook: HookPlugin | None = None,
        limits: HookLimits = DEFAULT_LIMITS,
        resolver: Resolver | None = None,
    ) -> None:
        self._ctx = ctx
        self._plugin_id = plugin_id
        self._writes = writes
        self._label = label or plugin_id
        self._hook = hook
        self._resolver = resolver
        self.budget = HookBudget(limits)
        self._client: httpx.Client | None = None
        self._settings: BaseModel | None = None
        self._secrets: BaseModel | None = None

    @property
    def data_dir(self) -> Path:
        folder = self._ctx.services.data_dir / DATA_FOLDER / self._plugin_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @property
    def settings(self) -> BaseModel:
        if self._settings is None:
            if self._hook is None:
                self._settings = NoSettings()
            else:
                config = self._ctx.services.config_store.load_config()
                self._settings = plugin_settings(config, self._hook, self.log)
        return self._settings

    @property
    def secrets(self) -> BaseModel:
        if self._secrets is None:
            if self._hook is None:
                self._secrets = NoSecrets()
            else:
                secrets = self._ctx.services.config_store.load_secrets()
                self._secrets = plugin_secrets(secrets, self._hook, self.log)
        return self._secrets

    def log(self, message: str) -> None:
        self._ctx.log(f"[{self._label}] {message}")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def get_item(self, item_id: int) -> ItemView | None:
        self.budget.spend_read()
        with self._ctx.session_factory() as session:
            return build_view(session, item_id)

    def _write(self, item_id: int, tag_kind: str, names: list[str], *, add: bool) -> TagResult:
        require_tag_permission(self._writes, tag_kind)
        cleaned = self.budget.spend_tags(names)
        write = tag_service.add_tags if add else tag_service.remove_tags
        with self._ctx.session_factory() as session:
            try:
                result = write(
                    session,
                    self._ctx.services.locks,
                    item_id,
                    tag_kind,
                    cleaned,
                    self._plugin_id,
                    runner=self._ctx.runner,
                    quiet=True,
                )
            except LookupError:
                raise HookError("条目不存在") from None
        return _tag_result(result)

    def add_tags(self, item_id: int, tag_kind: str, names: list[str]) -> TagResult:
        return self._write(item_id, tag_kind, names, add=True)

    def remove_tags(self, item_id: int, tag_kind: str, names: list[str]) -> TagResult:
        return self._write(item_id, tag_kind, names, add=False)

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = self._ctx.services.http_client()
        return self._client

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.budget.spend_http()
        self.log(f"{method} {url.split('?', 1)[0]}")
        return fetch_json(
            self._http(),
            method,
            url,
            self.budget.limits,
            resolver=self._resolver,
            **kwargs,
        )

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._request("GET", url, params=params, headers=headers)

    def post_json(
        self,
        url: str,
        *,
        json: Any = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._request("POST", url, json_body=json, data=data, headers=headers)


def _record_failure(ctx: TaskContext, hook_id: str, message: str) -> None:
    services = ctx.services
    if not services.plugins.health.failed(hook_id, message):
        return
    services.plugins.disable_hook(hook_id)
    try:
        services.config_store.save_plugin(hook_id, disabled=True)
    except Exception:
        _LOG.warning("钩子 %s 熔断后没能写进设置文件", hook_id, exc_info=True)
    ctx.log(f"钩子 {hook_id} 连续失败，已自动停用（设置页可以重新启用）")


def handle_hook(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    services = ctx.services
    plugin_id, hook_id = str(payload.get("plugin_id", "")), str(payload.get("hook_id", ""))
    hook = next(
        (h for pid, h in services.plugins.hooks() if pid == plugin_id and h.id == hook_id), None
    )
    info = services.plugins.get(plugin_id)
    entry = next((e for e in info.hooks if e.id == hook_id), None) if info else None
    if hook is None or entry is None:
        ctx.log(f"钩子 {hook_id} 已停用或被移除，跳过")
        return TaskResult(result={"skipped": True})
    try:
        event = event_from_dict(payload.get("event") or {})
    except ValidationError:
        raise TaskError("事件内容不认识，可能是旧版本留下的任务") from None
    host = HookHostImpl(ctx, plugin_id, entry.writes, label=hook.name, hook=hook)
    started = time.monotonic()
    try:
        ctx.check_cancelled()
        hook.on_event(host, event)
    except TaskCancelled:
        raise
    except Exception as error:
        ctx.log("钩子出错：\n" + traceback.format_exc())
        message = f"{type(error).__name__}: {getattr(error, 'message', error)}"
        _record_failure(ctx, hook_id, message)
        raise TaskError(f"钩子 {hook.name} 出错：{message}") from None
    finally:
        host.close()
    elapsed = time.monotonic() - started
    summary = {
        "hook": hook_id,
        "event": str(event.type),
        "http_calls": host.budget.http_calls,
        "tag_calls": host.budget.tag_calls,
        "seconds": round(elapsed, 2),
    }
    if elapsed > SLOW_SECONDS:
        ctx.log(
            f"钩子 {hook.name} 用了 {elapsed:.0f} 秒，超过 {SLOW_SECONDS:.0f} 秒"
            "（只记录，不强制结束）"
        )
        _record_failure(ctx, hook_id, f"处理一个事件用了 {elapsed:.0f} 秒")
        return TaskResult(result={**summary, "slow": True})
    services.plugins.health.succeeded(hook_id)
    return TaskResult(result=summary)
