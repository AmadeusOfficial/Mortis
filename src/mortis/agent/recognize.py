"""识别纠错用的工具：搜外部候选、列未识别文件、换匹配、绑文件。换匹配和绑文件每次都要用户批准。"""

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.agent.context import Result, ToolContext
from mortis.api.routes.items import bind_media, file_guess, rematch_work, unmatched_files
from mortis.api.routes.sources import search_plugin
from mortis.api.routes.tmdb import proxy_url
from mortis.db.models import DataType, Item, ItemExternalId, Library, MediaFile
from mortis.errors import UserError
from mortis.items.detail import year_of
from mortis.items.edit import ItemError
from mortis.items.matching import guess_file
from mortis.sources.tmdb.client import open_tmdb
from mortis.sources.tmdb.search import MovieHit, search_movies, search_tv

CANDIDATE_LIMIT = 10
UNMATCHED_LIMIT = 50
OVERVIEW_LIMIT = 120
REMEMBER_LIMIT = 500
KEEP_LOCKED = "锁定的字段保留"
TMDB = "tmdb"

_remembered: OrderedDict[tuple[str, str], Result] = OrderedDict()
_remembered_lock = threading.Lock()


def remember(candidate: Result) -> None:
    """记下最近搜到的候选，批准卡片和绑定时用它的标题，不用再请求一次来源。"""
    key = (candidate["source"], candidate["external_id"])
    with _remembered_lock:
        _remembered[key] = candidate
        _remembered.move_to_end(key)
        while len(_remembered) > REMEMBER_LIMIT:
            _remembered.popitem(last=False)


def recalled(source: str, external_id: str) -> Result | None:
    with _remembered_lock:
        return _remembered.get((source, external_id))


def forget_all() -> None:
    with _remembered_lock:
        _remembered.clear()


def file_of_item(session: Session, item: Item) -> Result | None:
    guess = file_guess(session, item)
    return guess.model_dump() if guess is not None else None


def _work(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise ItemError(f"没有这个条目：{item_id}", status_code=404)
    if item.kind != "work":
        raise ItemError("只有作品能重新识别；系列选其中一部，季和集用所在的剧")
    return item


def _unmatched_file(session: Session, file_id: int) -> MediaFile:
    media_file = session.get(MediaFile, file_id)
    if media_file is None:
        raise ItemError(f"没有这个文件：{file_id}", status_code=404)
    return media_file


def _data_type(session: Session, data_type_id: str) -> DataType:
    data_type = session.get(DataType, data_type_id)
    if data_type is None:
        raise ItemError(f"没有这种数据类型：{data_type_id}")
    return data_type


def _clip(text: str | None) -> str | None:
    if not text:
        return None
    return text if len(text) <= OVERVIEW_LIMIT else text[:OVERVIEW_LIMIT] + "…"


def _tmdb_candidate(hit: MovieHit) -> Result:
    return {
        "source": TMDB,
        "external_id": str(hit.tmdb_id),
        "title": hit.title,
        "original_title": hit.original_title or None,
        "year": hit.year,
        "release_date": hit.release_date,
        "poster_url": proxy_url(hit.poster_path),
        "overview": _clip(hit.overview),
    }


def _search_tmdb(
    ctx: ToolContext, data_type: DataType, query: str, year: int | None
) -> list[Result]:
    search = search_tv if data_type.structure == "series" else search_movies
    with open_tmdb(ctx.services, data_type.id) as tmdb:
        hits = search(tmdb, query, year)
        if not hits and year is not None:
            hits = search(tmdb, query, None)
    return [_tmdb_candidate(hit) for hit in hits]


def _search_sources(
    ctx: ToolContext, data_type: DataType, query: str, year: int | None
) -> tuple[list[Result], list[str]]:
    plugins = ctx.services.plugins.sources(data_type.id)
    if not plugins:
        raise ItemError(f"{data_type.name}没有启用的来源插件")
    found: list[Result] = []
    problems: list[str] = []
    for plugin in plugins:
        try:
            hits = search_plugin(ctx.services, plugin, query)
        except UserError as error:
            problems.append(f"{plugin.name}：{error.message}")
            continue
        found.extend(
            {
                "source": plugin.id,
                "source_name": plugin.name,
                "external_id": hit.external_id,
                "title": hit.title,
                "original_title": hit.original_title,
                "year": hit.year,
                "release_date": None,
                "poster_url": hit.poster_url,
                "overview": _clip(hit.overview),
            }
            for hit in hits
        )
    if year is not None:
        found.sort(key=lambda candidate: candidate["year"] != year)
    return found, problems


def search_candidates(ctx: ToolContext, args: dict[str, Any]) -> Result:
    session = ctx.session
    if ("item_id" in args) == ("file_id" in args):
        raise ItemError("item_id 和 file_id 要给且只给一个")
    if "item_id" in args:
        item = _work(session, args["item_id"])
        data_type = _data_type(session, item.data_type_id)
        guess = file_guess(session, item)
        default_query = guess.title if guess is not None else item.title
        default_year = guess.year if guess is not None else year_of(item.release_date)
    else:
        media_file = _unmatched_file(session, args["file_id"])
        library = session.get(Library, media_file.library_id)
        if library is None:
            raise ItemError("这个文件所在的库已经不在了")
        data_type = _data_type(session, library.data_type_id)
        parsed = guess_file(session, media_file)
        default_query, default_year = parsed.title, parsed.year
    query = (args.get("query") or default_query or "").strip()
    if not query:
        raise ItemError("没有能搜的标题，给一个 query")
    year = args.get("year", default_year)
    problems: list[str] = []
    if data_type.plugin_id is None:
        candidates = _search_tmdb(ctx, data_type, query, year)
    else:
        candidates, problems = _search_sources(ctx, data_type, query, year)
    total = len(candidates)
    candidates = candidates[:CANDIDATE_LIMIT]
    for candidate in candidates:
        remember(candidate)
    result: Result = {"query": query, "year": year, "candidates": candidates}
    if problems:
        result["problems"] = problems
    if total > len(candidates):
        result["note"] = f"共 {total} 个，只列出前 {CANDIDATE_LIMIT} 个"
    if not candidates:
        result["note"] = "没搜到，换个搜索词或去掉年份再试"
    return result


def list_unmatched(ctx: ToolContext, args: dict[str, Any]) -> Result:
    rows = unmatched_files(ctx.session, args.get("library_id"))
    files = [
        {
            "file_id": row.id,
            "path": row.path,
            "file_name": Path(row.path).name,
            "guess_title": row.guess_title,
            "guess_year": row.guess_year,
            "library_id": row.library_id,
            "library_name": row.library_name,
            "video_count": row.video_count,
        }
        for row in rows[:UNMATCHED_LIMIT]
    ]
    result: Result = {"files": files, "total": len(rows)}
    if len(rows) > len(files):
        result["note"] = f"共 {len(rows)} 个，只列出前 {UNMATCHED_LIMIT} 个"
    return result


def _external_ids(session: Session, item: Item) -> dict[str, str]:
    return dict(
        session.execute(
            select(ItemExternalId.provider, ItemExternalId.external_id).where(
                ItemExternalId.item_id == item.id
            )
        ).all()
    )


def _tmdb_id(data_type: DataType, source: str, external_id: str) -> int | None:
    """内置类型只能用 TMDB 编号；插件类型返回 None，交给来源插件。"""
    if data_type.plugin_id is not None:
        return None
    if source != TMDB:
        raise ItemError(f"{data_type.name}的来源只能是 tmdb")
    if not external_id.strip().isdigit():
        raise ItemError("TMDB 编号要是数字")
    return int(external_id.strip())


def _target(ctx: ToolContext, data_type: DataType, source: str, external_id: str) -> Result:
    """要换成的作品。先用最近搜到的候选，再问 TMDB；都拿不到时只有编号。"""
    known = recalled(source, external_id)
    if known is not None:
        return {
            key: known.get(key)
            for key in ("source", "external_id", "title", "original_title", "year", "release_date")
        }
    target: Result = {"source": source, "external_id": external_id}
    if source != TMDB or data_type.plugin_id is not None or not external_id.isdigit():
        return target
    series = data_type.structure == "series"
    try:
        with open_tmdb(ctx.services, data_type.id) as tmdb:
            body = tmdb.get(
                f"/{'tv' if series else 'movie'}/{external_id}", {"language": tmdb.language}
            )
    except UserError:
        return target
    date = body.get("first_air_date" if series else "release_date") or None
    target.update(
        title=body.get("name" if series else "title"),
        original_title=body.get("original_name" if series else "original_title"),
        year=year_of(date),
        release_date=date,
    )
    return target


def describe_rematch(ctx: ToolContext, args: dict[str, Any]) -> Result:
    session = ctx.session
    item = _work(session, args["item_id"])
    data_type = _data_type(session, item.data_type_id)
    return {
        "item_id": item.id,
        "title": item.title,
        "current": {
            "title": item.title,
            "year": year_of(item.release_date),
            "external_ids": _external_ids(session, item),
        },
        "target": _target(ctx, data_type, args["source"], args["external_id"].strip()),
        "note": KEEP_LOCKED,
    }


def describe_bind(ctx: ToolContext, args: dict[str, Any]) -> Result:
    session = ctx.session
    media_file = _unmatched_file(session, args["file_id"])
    library = session.get(Library, media_file.library_id)
    data_type = _data_type(session, library.data_type_id if library else "")
    parsed = guess_file(session, media_file)
    return {
        "file_id": media_file.id,
        "title": Path(media_file.path).name,
        "current": {
            "file_name": Path(media_file.path).name,
            "guess_title": parsed.title,
            "guess_year": parsed.year,
        },
        "target": _target(ctx, data_type, args["source"], args["external_id"].strip()),
        "note": KEEP_LOCKED,
    }


def rematch_item(ctx: ToolContext, args: dict[str, Any]) -> Result:
    session = ctx.session
    item = _work(session, args["item_id"])
    data_type = _data_type(session, item.data_type_id)
    source, external_id = args["source"].strip(), args["external_id"].strip()
    tmdb_id = _tmdb_id(data_type, source, external_id)
    task_id = rematch_work(
        session,
        ctx.runner,
        ctx.services,
        item,
        tmdb_id=tmdb_id,
        source=None if tmdb_id is not None else source,
        external_id=external_id,
    )
    return {
        "item_id": item.id,
        "source": source,
        "external_id": external_id,
        "task_id": task_id,
        "note": f"已换成新的匹配，正在重新刮削，{KEEP_LOCKED}",
    }


def bind_file(ctx: ToolContext, args: dict[str, Any]) -> Result:
    session = ctx.session
    media_file = _unmatched_file(session, args["file_id"])
    if media_file.item_id is not None:
        raise ItemError("这个文件已经识别过了，要改用 rematch_item")
    library = session.get(Library, media_file.library_id)
    data_type = _data_type(session, library.data_type_id if library else "")
    source, external_id = args["source"].strip(), args["external_id"].strip()
    tmdb_id = _tmdb_id(data_type, source, external_id)
    target = _target(ctx, data_type, source, external_id)
    bound = bind_media(
        session,
        ctx.runner,
        ctx.services,
        media_file,
        tmdb_id=tmdb_id,
        source=None if tmdb_id is not None else source,
        external_id=external_id,
        title=target.get("title") or f"{source} {external_id}",
        original_title=target.get("original_title"),
        release_date=target.get("release_date"),
    )
    return {
        "file_id": media_file.id,
        "item_id": bound.item_id,
        "task_id": bound.task_id,
        "title": target.get("title"),
        "note": "已绑定，正在刮削",
    }
