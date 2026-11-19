"""电影和电视剧刮削共用的快照、图片和编号读写。"""

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Protocol

import httpx2 as httpx
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
from mortis.errors import UserError
from mortis.scrape.images import ImageError, ImageStore
from mortis.sources.builtin import BuiltinSource, FetchRequest, builtin_sources
from mortis.tasks.base import TaskCancelled, TaskContext, TaskError

SOURCE = "tmdb"


def source_outcome(
    source_id: str, name: str, outcome: str, detail: str | None = None
) -> dict[str, Any]:
    """刮削结果里的一条来源记录。outcome 是 ok、cached、not_found、failed 或 skipped。"""
    return {"id": source_id, "name": name, "outcome": outcome, "detail": detail}


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


def save_snapshot(
    session: Session, item_id: int, raw: dict[str, Any], source: str = SOURCE
) -> None:
    record = session.get(ItemSourceRecord, (item_id, source))
    text = json.dumps(raw, ensure_ascii=False)
    if record is None:
        session.add(
            ItemSourceRecord(item_id=item_id, source=source, fetched_at=utc_now(), raw=text)
        )
    else:
        record.raw = text
        record.fetched_at = utc_now()


def load_snapshot(
    session: Session, item_id: int | None, force: bool, source: str = SOURCE
) -> dict[str, Any] | None:
    if item_id is None or force:
        return None
    record = session.get(ItemSourceRecord, (item_id, source))
    return json.loads(record.raw) if record is not None else None


def load_snapshots(session: Session, item_id: int, force: bool) -> dict[str, dict[str, Any]]:
    """条目已有的各来源快照；强制刮削时当作都没有。"""
    if force:
        return {}
    return {
        record.source: json.loads(record.raw)
        for record in session.scalars(
            select(ItemSourceRecord).where(ItemSourceRecord.item_id == item_id)
        )
    }


def external_ids(session: Session, item_id: int) -> dict[str, str]:
    return dict(
        session.execute(
            select(ItemExternalId.provider, ItemExternalId.external_id).where(
                ItemExternalId.item_id == item_id
            )
        ).all()
    )


@dataclass
class Gathered:
    """各来源的结果。raws 含沿用的快照，fetched 是这次新请求、要写快照的来源。"""

    raws: dict[str, dict[str, Any]] = dataclass_field(default_factory=dict)
    candidates: dict[str, dict[str, Any]] = dataclass_field(default_factory=dict)
    fetched: set[str] = dataclass_field(default_factory=set)
    found_ids: dict[str, str] = dataclass_field(default_factory=dict)
    """来源按标题认出的外部编号，键是 provider；插件来源用。"""
    sources: list[dict[str, Any]] = dataclass_field(default_factory=list)

    def record(self, source_id: str, name: str, outcome: str, detail: str | None = None) -> None:
        self.sources.append(source_outcome(source_id, name, outcome, detail))


class SourceMissing(Exception):
    """来源上没有这个条目。消息原样记进日志和来源记录。"""


class GatherSource(Protocol):
    """取数循环眼里的一个来源。内置来源和插件来源各有一个适配器。"""

    id: str
    name: str
    primary: bool
    """主源不可用、找不到或出错时整个刮削失败；其余来源只记下来。"""

    def session(self, ctx: TaskContext) -> AbstractContextManager[None]:
        """围住对这个来源的所有调用（插件来源在这里开宿主）。"""
        ...

    def skip(self) -> tuple[str, str] | None:
        """不可用时返回（日志文案，来源记录里的原因）；可用时返回 None。"""
        ...

    def fetch(self, ctx: TaskContext, found_ids: dict[str, str]) -> dict[str, Any]:
        """请求来源，返回要存成快照的原始结果。找不到时抛 SourceMissing。

        认出的外部编号（键是 provider）一认出就写进 found_ids，之后请求失败也保留。
        """
        ...

    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]:
        """从原始结果（新请求的或快照）算出候选值。"""
        ...

    def failure(self, error: Exception) -> str:
        """出错时记进日志和来源记录的整句文案。"""
        ...


class BuiltinAdapter:
    """把 BuiltinSource 接成 GatherSource。"""

    def __init__(
        self, source: BuiltinSource, name: str, reason: str | None, request: FetchRequest
    ) -> None:
        self._source = source
        self._reason = reason
        self._request = request
        self.id = source.id
        self.name = name
        self.primary = source.primary

    def session(self, ctx: TaskContext) -> AbstractContextManager[None]:
        return nullcontext()

    def skip(self) -> tuple[str, str] | None:
        if self._reason is None:
            return None
        return f"{self.name} 不可用，跳过：{self._reason}", self._reason

    def fetch(self, ctx: TaskContext, found_ids: dict[str, str]) -> dict[str, Any]:
        assert self._source.fetcher is not None
        raw = self._source.fetcher.fetch(self._request)
        if raw is None:
            raise SourceMissing(f"{self.name} 上没有这个条目")
        return raw

    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]:
        fetcher = self._source.fetcher
        assert fetcher is not None
        values = fetcher.candidates(raw, self._request)
        reported = getattr(fetcher, "external_ids", None)
        if reported is not None:
            self._request.ids.update(reported(raw, self._request))
        return values

    def failure(self, error: Exception) -> str:
        detail = (
            error.message if isinstance(error, UserError) else f"{type(error).__name__}: {error}"
        )
        return f"{self.name} 出错，跳过：{detail}"


def gather_sources(
    ctx: TaskContext,
    request: FetchRequest,
    snapshots: Mapping[str, dict[str, Any]],
    labels: Mapping[str, str],
) -> Gathered:
    """按默认顺序逐个取适用于这个类型的内置来源。已有快照的不再请求。

    还没有取数函数的来源不出现在结果里。labels 给结果里的来源换个名字，比如「TMDB 电影」。
    """
    config = ctx.services.config_store.load_config()
    secrets = ctx.services.config_store.load_secrets()
    sources = [
        BuiltinAdapter(
            source,
            labels.get(source.id, source.name),
            source.disabled_reason(config, secrets, ctx.services.data_dir),
            request,
        )
        for source in builtin_sources(request.data_type_id)
        if source.fetcher is not None
    ]
    return gather(ctx, sources, snapshots)


def gather(
    ctx: TaskContext, sources: Sequence[GatherSource], snapshots: Mapping[str, dict[str, Any]]
) -> Gathered:
    """内置来源和插件来源共用的取数循环。已有快照的不再请求。

    主源失败时整个刮削失败，其余来源出错只记下来；取消照常往外抛。
    """
    gathered = Gathered()
    for source in sources:
        ctx.check_cancelled()
        with source.session(ctx):
            notice = source.skip()
            if notice is not None:
                message, reason = notice
                if source.primary:
                    raise TaskError(f"{source.name} 不可用：{reason}")
                ctx.log(message)
                gathered.record(source.id, source.name, "skipped", reason)
                continue
            raw = snapshots.get(source.id)
            cached = raw is not None
            try:
                if raw is None:
                    raw = source.fetch(ctx, gathered.found_ids)
                values = source.candidates(raw)
            except SourceMissing as missing:
                message = str(missing)
                if source.primary:
                    raise TaskError(message) from None
                ctx.log(message)
                gathered.record(source.id, source.name, "not_found", message)
                continue
            except TaskCancelled:
                raise
            except Exception as error:  # 来源和插件代码的缺陷不能拖垮整个刮削
                if source.primary:
                    raise
                message = source.failure(error)
                ctx.log(message)
                gathered.record(source.id, source.name, "failed", message)
                continue
        gathered.raws[source.id] = raw
        gathered.candidates[source.id] = values
        if not cached:
            gathered.fetched.add(source.id)
        gathered.record(source.id, source.name, "cached" if cached else "ok")
    return gathered


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
    store: ImageStore, http: httpx.Client, urls: list[str], ctx: TaskContext
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
            files[url] = store.download(http, url)
        except ImageError as error:
            ctx.log(f"图片下载失败：{url}（{error}）")
    return files


def person_key(entry: dict[str, Any]) -> tuple[str, str]:
    """人物在来源里的编号。TMDB 的候选值用 tmdb_id，插件的用 provider 和 external_id。"""
    if "tmdb_id" in entry:
        return SOURCE, str(entry["tmdb_id"])
    return str(entry["provider"]), str(entry["external_id"])


def missing_photos(session: Session, actors: list[dict[str, Any]]) -> list[str]:
    """还没有头像的人物的头像地址。"""
    urls: list[str] = []
    for actor in actors:
        if not actor.get("photo_url"):
            continue
        provider, external_id = person_key(actor)
        has_photo = session.scalar(
            select(Person.id)
            .join(PersonExternalId, PersonExternalId.person_id == Person.id)
            .where(
                PersonExternalId.provider == provider,
                PersonExternalId.external_id == external_id,
                Person.photo_path.is_not(None),
            )
        )
        if has_photo is None:
            urls.append(actor["photo_url"])
    return urls
