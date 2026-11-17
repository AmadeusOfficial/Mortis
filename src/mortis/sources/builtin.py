"""内置来源的登记表：TMDB、Fanart.tv、TVDB、IMDb 评分和烂番茄。来源插件不在这里。"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx2 as httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.catalog.install import BUILTIN_TYPES
from mortis.config.models import AppConfig, MovieScrapeConfig, Secrets, TvScrapeConfig
from mortis.db.models import SourceOrder
from mortis.plugins.registry import seed_orders
from mortis.services import Services
from mortis.sources.tmdb.client import open_tmdb
from mortis.sources.tmdb.movie import fetch_movie, movie_candidates
from mortis.sources.tmdb.tv import fetch_show, show_candidates

FieldKind = Literal["text", "images", "rating"]
NOT_IMPLEMENTED = "还没实现"
PRIMARY = "tmdb"


@dataclass(frozen=True)
class FetchRequest:
    """一次刮削交给来源的东西。ids 是条目已有的外部编号，键是 provider。"""

    services: Services
    data_type_id: str
    config: MovieScrapeConfig | TvScrapeConfig
    ids: Mapping[str, str]
    http: httpx.Client
    log: Callable[[str], None]


class Fetcher(Protocol):
    def fetch(self, request: FetchRequest) -> dict[str, Any] | None:
        """返回要存成快照的原始结果；来源上没有这个条目时返回 None。"""
        ...

    def candidates(self, raw: dict[str, Any], request: FetchRequest) -> dict[str, Any]:
        """从原始结果算出作品层的候选值，键是字段键。"""
        ...


def _always(_config: AppConfig, _secrets: Secrets) -> str | None:
    return None


@dataclass(frozen=True)
class BuiltinSource:
    id: str
    name: str
    data_types: frozenset[str]
    field_kinds: frozenset[FieldKind]
    check: Callable[[AppConfig, Secrets], str | None] = _always
    """返回不可用的原因；可用时返回 None。"""
    fetcher: Fetcher | None = None
    primary: bool = False
    """主源失败时整个刮削失败；其余来源失败只记下来。"""

    def disabled_reason(self, config: AppConfig, secrets: Secrets) -> str | None:
        if self.fetcher is None:
            return NOT_IMPLEMENTED
        return self.check(config, secrets)

    def enabled(self, config: AppConfig, secrets: Secrets) -> bool:
        return self.disabled_reason(config, secrets) is None


class _TmdbFetcher:
    def fetch(self, request: FetchRequest) -> dict[str, Any]:
        tmdb = open_tmdb(request.services, request.data_type_id, http=request.http)
        tmdb_id = int(request.ids[PRIMARY])
        if isinstance(request.config, TvScrapeConfig):
            request.log(f"请求 TMDB 剧 {tmdb_id}")
            return fetch_show(tmdb, tmdb_id, request.config)
        request.log(f"请求 TMDB 电影 {tmdb_id}")
        return fetch_movie(tmdb, tmdb_id, request.config)

    def candidates(self, raw: dict[str, Any], request: FetchRequest) -> dict[str, Any]:
        if isinstance(request.config, TvScrapeConfig):
            return show_candidates(raw, request.config)
        return movie_candidates(raw, request.config)


def _tmdb_check(_config: AppConfig, secrets: Secrets) -> str | None:
    return None if secrets.tmdb_api_key.strip() else "Key 未填"


ALL_TYPES = frozenset({"movie", "tv", "anime"})
SERIES_TYPES = frozenset({"tv", "anime"})

SOURCES: tuple[BuiltinSource, ...] = (
    BuiltinSource(
        "tmdb",
        "TMDB",
        ALL_TYPES,
        frozenset({"text", "images", "rating"}),
        check=_tmdb_check,
        fetcher=_TmdbFetcher(),
        primary=True,
    ),
    BuiltinSource("fanart", "Fanart.tv", ALL_TYPES, frozenset({"images"})),
    BuiltinSource("tvdb", "TVDB", SERIES_TYPES, frozenset({"text", "images"})),
    BuiltinSource("imdb", "IMDb", ALL_TYPES, frozenset({"rating"})),
    BuiltinSource("ratings_rt", "烂番茄", ALL_TYPES, frozenset({"rating"})),
)
BY_ID = {source.id: source for source in SOURCES}

DEFAULT_ORDERS: dict[str, tuple[str, ...]] = {
    "movie": ("tmdb", "fanart", "imdb", "ratings_rt"),
    "tv": ("tmdb", "tvdb", "fanart", "imdb", "ratings_rt"),
    "anime": ("tmdb", "tvdb", "fanart", "imdb", "ratings_rt"),
}
"""内置类型的整体来源顺序，也是 reset 回到的顺序。"""

IMAGES_ONLY = frozenset({"tvdb"})
"""默认只给图片（和评分）用的来源：文本类字段的字段行里把它们记为排除。"""


def builtin_sources(data_type_id: str) -> list[BuiltinSource]:
    """适用于这个类型的内置来源，按默认顺序。"""
    return [BY_ID[source_id] for source_id in DEFAULT_ORDERS.get(data_type_id, ())]


def source_status(services: Services, data_type_id: str) -> list[tuple[BuiltinSource, str | None]]:
    """每个适用来源和它不可用的原因（可用时是 None）。"""
    config = services.config_store.load_config()
    secrets = services.config_store.load_secrets()
    return [
        (source, source.disabled_reason(config, secrets))
        for source in builtin_sources(data_type_id)
    ]


def builtin_ids(data_type_id: str) -> list[str]:
    return list(DEFAULT_ORDERS.get(data_type_id, ()))


def is_builtin_type(data_type_id: str) -> bool:
    return data_type_id in DEFAULT_ORDERS


def default_orders(data_type_id: str) -> dict[str, list[str]]:
    """写进 source_orders 的默认顺序，形状和数据类型插件的 default_orders 一样。"""
    whole = builtin_ids(data_type_id)
    orders = {"": whole}
    images_only = IMAGES_ONLY & set(whole)
    if not images_only:
        return orders
    spec = next(spec for spec in BUILTIN_TYPES if spec.id == data_type_id)
    for field in spec.fields:
        if field.value_type not in ("images", "rating"):
            orders[field.field_key] = [source for source in whole if source not in images_only]
    return orders


def seed_builtin_orders(session: Session) -> None:
    """内置类型第一次启动时写入默认顺序。

    以后只给整体顺序里还没出现过的新来源补一次：接到整体顺序末尾，只出图片的来源同时从文本字段里排除。
    已经出现过的来源不再碰，用户把字段改回「跟随整体」后不会被重新补上。
    """
    for data_type_id in DEFAULT_ORDERS:
        defaults = default_orders(data_type_id)
        rows = session.scalars(
            select(SourceOrder).where(SourceOrder.data_type_id == data_type_id)
        ).all()
        if not rows:
            seed_orders(session, data_type_id, defaults)
            continue
        whole_rows = [row for row in rows if row.field_key == ""]
        known = {row.source for row in whole_rows}
        added = [source for source in defaults[""] if source not in known]
        if not added:
            continue
        position = max((row.position for row in whole_rows), default=-1) + 1
        for source in added:
            session.add(
                SourceOrder(
                    data_type_id=data_type_id,
                    field_key="",
                    source=source,
                    position=position,
                    is_excluded=0,
                )
            )
            position += 1
        _exclude_from_text_fields(session, data_type_id, rows, set(added) & IMAGES_ONLY)
        session.flush()


def _exclude_from_text_fields(
    session: Session, data_type_id: str, rows: list[SourceOrder], sources: set[str]
) -> None:
    """文本字段原来跟随整体顺序的，按整体顺序写出字段行再排除这些来源；已有字段行的在末尾加排除行。"""
    if not sources:
        return
    whole = [row.source for row in sorted(rows, key=lambda r: r.position) if row.field_key == ""]
    by_field: dict[str, list[SourceOrder]] = {}
    for row in rows:
        if row.field_key:
            by_field.setdefault(row.field_key, []).append(row)
    for field_key in default_orders(data_type_id):
        if field_key == "":
            continue
        field_rows = by_field.get(field_key, [])
        listed = {row.source for row in field_rows}
        position = max((row.position for row in field_rows), default=-1) + 1
        entries = [] if field_rows else [(source, 0) for source in whole]
        entries += [(source, 1) for source in sorted(sources) if source not in listed]
        for source, excluded in entries:
            session.add(
                SourceOrder(
                    data_type_id=data_type_id,
                    field_key=field_key,
                    source=source,
                    position=position,
                    is_excluded=excluded,
                )
            )
            position += 1
