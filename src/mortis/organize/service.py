from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from mortis.config.models import PluginTypeConfig
from mortis.config.types import type_config
from mortis.db.models import DataType, Item, ItemExternalId, ItemSourceRecord, Library
from mortis.hooks.dispatch import hook_followups
from mortis.items.identity import (
    ExternalRef,
    MovieRef,
    find_or_create_movie,
    find_or_create_work,
    work_of,
)
from mortis.items.presence import drop_fileless_works
from mortis.organize.files import sync_media_files
from mortis.organize.names import ParsedName
from mortis.organize.scan import scan_folders, scan_paths, scan_shows
from mortis.plugin_api import SourceError, SourcePlugin
from mortis.plugin_api.events import Event, ItemCreated, ScanFinished
from mortis.plugins.host import open_host
from mortis.plugins.matching import SourceMatch, best_hit
from mortis.scrape.apply import field_orders
from mortis.sources.tmdb.client import TmdbAuthError, TmdbError, open_tmdb
from mortis.sources.tmdb.search import find_best
from mortis.tasks.base import Followup, TaskContext, TaskError, TaskResult


def unscraped_work_ids(session: Session, library_id: int) -> list[int]:
    """TMDB 类型：有 TMDB 编号、没有 TMDB 快照。插件类型：有任一外部编号、没有任何快照。"""
    library = session.get(Library, library_id)
    data_type = session.get(DataType, library.data_type_id) if library is not None else None
    has_id = exists().where(ItemExternalId.item_id == Item.id)
    has_record = exists().where(ItemSourceRecord.item_id == Item.id)
    if data_type is None or data_type.plugin_id is None:
        has_id = has_id.where(ItemExternalId.provider == "tmdb")
        has_record = has_record.where(ItemSourceRecord.source == "tmdb")
    return list(
        session.scalars(
            select(Item.id)
            .where(Item.library_id == library_id, Item.kind == "work", has_id, ~has_record)
            .order_by(Item.id)
        )
    )


def organize_library(
    session: Session, library_id: int, ctx: TaskContext, *, scrape: bool | None
) -> TaskResult:
    services = ctx.services
    config = services.config_store.load_config()
    library = session.get(Library, library_id)
    if library is None:
        raise TaskError("媒体库不存在，可能已被删除")
    data_type = session.get(DataType, library.data_type_id)
    if data_type is not None and data_type.plugin_id is not None:
        return organize_plugin_library(session, library, data_type, ctx, scrape=scrape)
    is_series = data_type is not None and data_type.structure == "series"
    settings = type_config(config, library.data_type_id)
    roots = [row.path for row in library.paths]
    ctx.log(f"扫描「{library.name}」的 {len(roots)} 个目录")
    scanned = (scan_shows if is_series else scan_paths)(
        roots,
        video_extensions=config.files.video_extensions,
        subtitle_extensions=config.files.subtitle_extensions,
        skip_folders=settings.skip_folders,
    )
    for error in scanned.errors:
        ctx.log(error)
    ctx.check_cancelled()
    owner = (lambda item_id: work_of(session, item_id)) if is_series else None
    report = sync_media_files(session, library, scanned.movies, owner)
    session.commit()
    ctx.log(f"找到 {len(scanned.movies)} 部，新增文件 {report.added} 个，移除 {report.removed} 个")

    pending = [group for group in report.groups if group.item_id is None]
    events: list[Event] = []
    matched = unmatched = errors = 0
    identified = bool(services.config_store.load_secrets().tmdb_api_key.strip())
    if not identified:
        ctx.log("没有填 TMDB API Key，只扫描不识别")
    elif pending:
        with open_tmdb(services, library.data_type_id) as tmdb:
            for index, group in enumerate(pending, start=1):
                ctx.check_cancelled()
                ctx.progress(index, len(pending), group.movie.name)
                try:
                    match = find_best(
                        tmdb,
                        group.movie.parsed,
                        kind="tv" if is_series else "movie",
                        threshold=settings.match_threshold,
                    )
                except TmdbAuthError:
                    raise TaskError("TMDB 拒绝了这个 API Key") from None
                except TmdbError as error:
                    errors += 1
                    ctx.log(f"识别「{group.movie.name}」出错：{error.message}")
                    continue
                if match is None or match.score < settings.match_threshold:
                    unmatched += 1
                    best = (
                        f"，最高匹配度 {match.score:.2f}，低于 {settings.match_threshold:.2f}"
                        f"（{match.hit.title}）"
                        if match
                        else ""
                    )
                    ctx.log(f"未识别：{group.movie.name}{best}")
                    continue
                if match.rivals:
                    unmatched += 1
                    names = "、".join(
                        f"{hit.title}（{hit.year or '年份未知'}）"
                        for hit in (match.hit, *match.rivals)
                    )
                    ctx.log(
                        f"未识别：{group.movie.name}，有 {len(match.rivals) + 1} 部同名片："
                        f"{names}，文件名没有年份，请手动匹配"
                    )
                    continue
                if match.loose:
                    unmatched += 1
                    year = f"（{match.hit.year}）" if match.hit.year else ""
                    ctx.log(
                        f"未识别：{group.movie.name}，最接近的是 {match.hit.title}{year}，"
                        f"匹配度 {match.score:.2f}，文件名没有年份、名字只部分对上，请手动匹配"
                    )
                    continue
                with services.locks.hold("catalog-write"):
                    item, created = find_or_create_movie(
                        session,
                        library,
                        MovieRef(
                            tmdb_id=match.hit.tmdb_id,
                            title=match.hit.title,
                            original_title=match.hit.original_title,
                            release_date=match.hit.release_date,
                        ),
                    )
                    for row in group.files:
                        row.item_id = item.id
                    session.commit()
                    if created:
                        events.append(
                            ItemCreated(
                                data_type=library.data_type_id,
                                item_id=item.id,
                                library_id=library.id,
                            )
                        )
                matched += 1
                year = f"（{match.hit.year}）" if match.hit.year else ""
                ctx.log(
                    f"已识别：{group.movie.name} → {match.hit.title}{year}，"
                    f"匹配度 {match.score:.2f}"
                )

    dropped = drop_fileless_works(session, library.id)
    if dropped:
        session.commit()
        ctx.log(f"去掉了 {dropped} 个已经没有文件的作品")

    should_scrape = bool(library.is_auto_scrape) if scrape is None else scrape
    followups: list[Followup] = []
    if should_scrape:
        ids = unscraped_work_ids(session, library.id)
        if is_series and report.grown:
            ids += session.scalars(
                select(ItemExternalId.item_id)
                .where(
                    ItemExternalId.item_id.in_(report.grown - set(ids)),
                    ItemExternalId.provider == "tmdb",
                )
                .order_by(ItemExternalId.item_id)
            ).all()
        followups = [Followup("scrape", {"item_id": item_id, "force": False}) for item_id in ids]
        ctx.log(f"排了 {len(followups)} 个刮削任务")
    result: dict[str, Any] = {
        "found": len(scanned.movies),
        "added": report.added,
        "removed": report.removed,
        "matched": matched,
        "unmatched": unmatched,
        "errors": errors,
        "identified": identified,
        "scrape_queued": len(followups),
        "dropped": dropped,
    }
    events.append(_scan_finished(library, result))
    return TaskResult(result=result, followups=[*followups, *hook_followups(ctx, *events)])


def _scan_finished(library: Library, result: dict[str, Any]) -> ScanFinished:
    return ScanFinished(
        data_type=library.data_type_id,
        library_id=library.id,
        found=result["found"],
        added=result["added"],
        removed=result["removed"],
        matched=result["matched"],
        unmatched=result["unmatched"],
    )


def _identify(
    ctx: TaskContext, sources: list[SourcePlugin], parsed: ParsedName, threshold: float
) -> SourceMatch | None:
    """按整体来源顺序逐个搜，第一个过阈值的来源决定条目；都不过时返回最高分。"""
    best: SourceMatch | None = None
    for plugin in sources:
        with open_host(ctx.services, plugin, ctx.log) as host:
            if not plugin.enabled(host):
                continue
            try:
                match = best_hit(host, plugin, list(parsed.search_titles), parsed.year)
            except SourceError as error:
                ctx.log(f"{plugin.name} 搜索「{parsed.title}」出错：{error.message}")
                continue
        if match is None:
            continue
        if match.score >= threshold:
            return match
        if best is None or match.score > best.score:
            best = match
    return best


def organize_plugin_library(
    session: Session,
    library: Library,
    data_type: DataType,
    ctx: TaskContext,
    *,
    scrape: bool | None,
) -> TaskResult:
    """插件类型按目录扫描，用来源插件识别。"""
    services = ctx.services
    if not data_type.is_enabled:
        raise TaskError(f"{data_type.name} 插件没有启用，不能扫描")
    settings = type_config(services.config_store.load_config(), data_type.id)
    recursive = isinstance(settings, PluginTypeConfig) and settings.recursive_scan
    roots = [row.path for row in library.paths]
    how = "，递归搜索子目录" if recursive else ""
    ctx.log(f"扫描「{library.name}」的 {len(roots)} 个目录{how}")
    scanned = scan_folders(roots, skip_folders=settings.skip_folders, recursive=recursive)
    for error in scanned.errors:
        ctx.log(error)
    ctx.check_cancelled()
    report = sync_media_files(session, library, scanned.movies, main_kind="game")
    session.commit()
    ctx.log(f"找到 {len(scanned.movies)} 个，新增 {report.added} 个，移除 {report.removed} 个")

    plugins = services.plugins.sources(data_type.id)
    order = field_orders(session, data_type.id, [plugin.id for plugin in plugins])[""]
    sources = sorted(plugins, key=lambda plugin: order.index(plugin.id))
    pending = [group for group in report.groups if group.item_id is None]
    events: list[Event] = []
    matched = unmatched = 0
    if not sources:
        ctx.log(f"{data_type.name} 没有启用的来源插件，只扫描不识别")
    for index, group in enumerate(pending if sources else [], start=1):
        ctx.check_cancelled()
        ctx.progress(index, len(pending), group.movie.name)
        match = _identify(ctx, sources, group.movie.parsed, settings.match_threshold)
        if match is None or match.score < settings.match_threshold:
            unmatched += 1
            best = (
                f"，最高匹配度 {match.score:.2f}，低于 {settings.match_threshold:.2f}"
                f"（{match.hit.title}）"
                if match
                else ""
            )
            ctx.log(f"未识别：{group.movie.name}{best}")
            continue
        hit = match.hit
        with services.locks.hold("catalog-write"):
            item, created = find_or_create_work(
                session,
                library,
                ExternalRef(
                    provider=match.source.provider,
                    external_id=hit.external_id,
                    title=hit.title,
                    original_title=hit.original_title,
                ),
            )
            for row in group.files:
                row.item_id = item.id
            session.commit()
            if created:
                events.append(
                    ItemCreated(data_type=data_type.id, item_id=item.id, library_id=library.id)
                )
        matched += 1
        ctx.log(
            f"已识别：{group.movie.name} → {hit.title}（{match.source.name} {hit.external_id}），"
            f"匹配度 {match.score:.2f}"
        )

    dropped = drop_fileless_works(session, library.id)
    if dropped:
        session.commit()
        ctx.log(f"去掉了 {dropped} 个已经没有文件的作品")

    should_scrape = bool(library.is_auto_scrape) if scrape is None else scrape
    followups: list[Followup] = []
    if should_scrape:
        ids = unscraped_work_ids(session, library.id)
        followups = [Followup("scrape", {"item_id": item_id, "force": False}) for item_id in ids]
        ctx.log(f"排了 {len(followups)} 个刮削任务")
    result: dict[str, Any] = {
        "found": len(scanned.movies),
        "added": report.added,
        "removed": report.removed,
        "matched": matched,
        "unmatched": unmatched,
        "errors": 0,
        "identified": bool(sources),
        "scrape_queued": len(followups),
        "dropped": dropped,
    }
    events.append(_scan_finished(library, result))
    return TaskResult(result=result, followups=[*followups, *hook_followups(ctx, *events)])


def handle_organize(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    library_id = int(payload["library_id"])
    with ctx.services.locks.hold(f"library:{library_id}"):
        with ctx.session_factory() as session:
            return organize_library(session, library_id, ctx, scrape=payload.get("scrape"))
