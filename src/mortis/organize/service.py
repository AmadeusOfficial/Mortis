from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from mortis.config.types import type_config
from mortis.db.models import DataType, Item, ItemExternalId, ItemSourceRecord, Library
from mortis.items.identity import MovieRef, find_or_create_movie, work_of
from mortis.organize.files import sync_media_files
from mortis.organize.scan import scan_paths, scan_shows
from mortis.sources.tmdb.client import TmdbAuthError, TmdbError, open_tmdb
from mortis.sources.tmdb.search import find_best
from mortis.tasks.base import Followup, TaskContext, TaskError, TaskResult


def unscraped_work_ids(session: Session, library_id: int) -> list[int]:
    return list(
        session.scalars(
            select(Item.id)
            .where(
                Item.library_id == library_id,
                Item.kind == "work",
                exists().where(
                    ItemExternalId.item_id == Item.id, ItemExternalId.provider == "tmdb"
                ),
                ~exists().where(
                    ItemSourceRecord.item_id == Item.id, ItemSourceRecord.source == "tmdb"
                ),
            )
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
    matched = unmatched = errors = 0
    identified = bool(services.config_store.load_secrets().tmdb_api_key.strip())
    if not identified:
        ctx.log("没有填 TMDB API Key，只扫描不识别")
    elif pending:
        with open_tmdb(services) as tmdb:
            for index, group in enumerate(pending, start=1):
                ctx.check_cancelled()
                ctx.progress(index, len(pending), group.movie.name)
                try:
                    match = find_best(tmdb, group.movie.parsed, kind="tv" if is_series else "movie")
                except TmdbAuthError:
                    raise TaskError("TMDB 拒绝了这个 API Key") from None
                except TmdbError as error:
                    errors += 1
                    ctx.log(f"识别「{group.movie.name}」出错：{error.message}")
                    continue
                if match is None or match.score < settings.match_threshold:
                    unmatched += 1
                    best = f"，最高匹配度 {match.score:.2f}（{match.hit.title}）" if match else ""
                    ctx.log(f"未识别：{group.movie.name}{best}")
                    continue
                with services.locks.hold("catalog-write"):
                    item, _created = find_or_create_movie(
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
                matched += 1
                year = f"（{match.hit.year}）" if match.hit.year else ""
                ctx.log(
                    f"已识别：{group.movie.name} → {match.hit.title}{year}，"
                    f"匹配度 {match.score:.2f}"
                )

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
    }
    return TaskResult(result=result, followups=followups)


def handle_organize(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    library_id = int(payload["library_id"])
    with ctx.services.locks.hold(f"library:{library_id}"):
        with ctx.session_factory() as session:
            return organize_library(session, library_id, ctx, scrape=payload.get("scrape"))
