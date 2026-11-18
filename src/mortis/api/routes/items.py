from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from mortis import libraries
from mortis.api.deps import RunnerDep, ServicesDep, SessionDep
from mortis.db.models import Item, ItemTag, Library, MediaFile, Tag
from mortis.items import user_tags
from mortis.items.delete import Expected, check_deletable, delete_item, plan_delete
from mortis.items.detail import item_detail, wall_rows
from mortis.items.edit import ItemError, edit_fields, set_locks
from mortis.items.identity import ExternalRef, MovieRef
from mortis.items.matching import (
    add_source,
    bind_file,
    bind_folder,
    guess,
    guess_file,
    is_series,
    main_files,
    rematch,
    rematch_external,
    show_folder,
)
from mortis.output.service import rewrite_if_arranged
from mortis.plugin_api import SourcePlugin
from mortis.services import Services
from mortis.tasks import repo as task_repo
from mortis.tasks.runner import TaskRunner

router = APIRouter(prefix="/api")


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = True


class Enqueued(BaseModel):
    task_id: int


class WallItem(BaseModel):
    id: int
    kind: Literal["work", "collection"]
    title: str
    subtitle: str | None
    year: int | None
    poster_url: str | None
    is_scraped: bool
    issue: str | None


class FieldOut(BaseModel):
    key: str
    label: str
    value_type: str
    stored_in: str
    value: Any
    source: str | None
    locked: bool
    lockable: bool
    editable: bool


class SectionOut(BaseModel):
    name: str
    fields: list[FieldOut]


class CollectionRef(BaseModel):
    id: int
    title: str
    poster_url: str | None


class FileOut(BaseModel):
    id: int
    path: str
    file_kind: str
    size: int | None


class EpisodeOut(BaseModel):
    id: int
    number: int | None
    title: str
    air_date: str | None
    has_file: bool
    playable: bool = False
    """视频绑在这一集上。连播的两集共用一个文件时，只有绑定的那集能直接播放。"""
    still_url: str | None


class SeasonOut(BaseModel):
    id: int
    number: int | None
    title: str
    poster_url: str | None
    episode_count: int
    file_count: int
    episodes: list[EpisodeOut]


class FileGuess(BaseModel):
    file_id: int
    file_name: str
    folder_name: str
    title: str
    year: int | None


class ItemDetail(BaseModel):
    id: int
    library_id: int
    data_type_id: str
    library_name: str
    kind: str
    title: str
    subtitle: str | None
    year: int | None
    poster_url: str | None
    fanart_url: str | None
    external_ids: dict[str, str]
    collection: CollectionRef | None
    parts: list[WallItem]
    show: CollectionRef | None
    position: str | None
    seasons: list[SeasonOut]
    files: list[FileOut]
    scraped_at: str | None
    user_tags: list[str]
    sections: list[SectionOut]
    guess: FileGuess | None = None
    """第一个视频或游戏目录的文件名、上级目录名，和从文件名解析出的标题、年份。"""


class FieldsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


class LocksUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: list[str]


class RematchRequest(BaseModel):
    """TMDB 类型给 tmdb_id；插件类型给来源插件 id 和它的外部编号。"""

    model_config = ConfigDict(extra="forbid")

    tmdb_id: int | None = None
    source: str | None = None
    external_id: str | None = None


class SourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    external_id: str


class BindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tmdb_id: int | None = None
    source: str | None = None
    external_id: str | None = None
    title: str
    original_title: str | None = None
    release_date: str | None = None


class Bound(BaseModel):
    item_id: int
    task_id: int


class Unregistered(BaseModel):
    """独占文件夹里会一起删的未登记文件。sample 是前几个的相对路径。"""

    count: int
    total_size: int
    sample: list[str]


class DeletePreview(BaseModel):
    item_id: int
    title: str
    library_id: int
    folders: list[str]
    files: list[str]
    """不含 folders 里已经包括的文件。"""
    file_count: int
    """磁盘上要删的文件总数，含文件夹里的。"""
    total_size: int
    permanent: bool
    """所在盘没有回收站，会永久删除。"""
    unregistered: Unregistered
    reasons: list[str]


class DeleteExpected(BaseModel):
    """照预览原样交回来，执行时重新算出来的任一项不一样就 409。"""

    model_config = ConfigDict(extra="forbid")

    folders: list[str]
    files: list[str]
    file_count: int
    total_size: int
    permanent: bool
    unregistered_count: int


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_title: str
    expected: DeleteExpected


class Deleted(BaseModel):
    deleted: bool
    library_id: int


class UnmatchedFile(BaseModel):
    id: int
    library_id: int
    library_name: str
    data_type_id: str
    path: str
    size: int | None
    guess_title: str
    guess_year: int | None
    video_count: int


def get_item(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="没有这个条目")
    return item


def file_guess(session: Session, item: Item) -> FileGuess | None:
    files = main_files(session, item, limit=1) if item.kind == "work" else []
    if not files:
        return None
    media_file = files[0]
    parsed = guess_file(session, media_file)
    path = Path(media_file.path)
    return FileGuess(
        file_id=media_file.id,
        file_name=path.name,
        folder_name=path.parent.name,
        title=parsed.title,
        year=parsed.year,
    )


def detail_of(session: Session, item: Item) -> dict[str, Any]:
    return {**item_detail(session, item), "guess": file_guess(session, item)}


def enqueue_scrape(session: Session, runner: TaskRunner, item_id: int, *, force: bool) -> int:
    queued = task_repo.find_queued(session, "scrape", "item_id", item_id)
    if queued is not None:
        return queued.id
    task = task_repo.enqueue(session, "scrape", {"item_id": item_id, "force": force}, priority=1)
    session.commit()
    runner.wake()
    return task.id


WallSort = Literal["title", "release_date", "created_at", "updated_at"]
WallOrder = Literal["asc", "desc"]


@router.get("/libraries/{library_id}/items", response_model=list[WallItem])
def list_items(
    library_id: int,
    session: SessionDep,
    q: str | None = Query(default=None, max_length=200),
    sort: WallSort = "title",
    order: WallOrder = "asc",
    tag: Annotated[list[int] | None, Query()] = None,
) -> list[dict[str, Any]]:
    """tag 是这个库数据类型的自定义标签 id，可以给多个，取交集。"""
    library = libraries.get_library(session, library_id)
    tags = shelf_tags(session, library, tag or [])
    term = q.strip() if q else ""
    return wall_rows(session, *wall_items(session, library, term, sort, order, tags))


class Shelf(BaseModel):
    tag_id: int
    name: str
    count: int
    items: list[WallItem]
    """前 SHELF_SIZE 个，排序同海报墙。"""


SHELF_SIZE = 20


@router.get("/libraries/{library_id}/shelves", response_model=list[Shelf])
def list_shelves(
    library_id: int,
    session: SessionDep,
    tags: str = Query(default="", max_length=2000),
    sort: WallSort = "title",
    order: WallOrder = "asc",
) -> list[dict[str, Any]]:
    """分类视图：按传入的自定义标签顺序，每个标签一行。tags 是逗号分隔的标签 id。"""
    library = libraries.get_library(session, library_id)
    try:
        ids = [int(part) for part in tags.split(",") if part.strip()]
    except ValueError:
        raise ItemError("tags 要是逗号分隔的标签编号", status_code=400) from None
    shelves: list[dict[str, Any]] = []
    for tag in shelf_tags(session, library, list(dict.fromkeys(ids))):
        items, years = wall_items(session, library, "", sort, order, [tag])
        shelves.append(
            {
                "tag_id": tag.id,
                "name": tag.name,
                "count": len(items),
                "items": wall_rows(session, items[:SHELF_SIZE], years),
            }
        )
    return shelves


def shelf_tags(session: Session, library: Library, tag_ids: list[int]) -> list[Tag]:
    """按给出的顺序取标签；不是这个库数据类型的自定义标签就 400。"""
    kind = user_tags.tag_kind(library.data_type_id)
    found = {
        tag.id: tag
        for tag in session.scalars(select(Tag).where(Tag.id.in_(tag_ids), Tag.tag_kind == kind))
    }
    missing = [str(tag_id) for tag_id in tag_ids if tag_id not in found]
    if missing:
        raise ItemError(f"这些标签不属于这个库的类型：{'、'.join(missing)}", status_code=400)
    return [found[tag_id] for tag_id in tag_ids]


def wall_items(
    session: Session,
    library: Library,
    term: str,
    sort: WallSort,
    order: WallOrder,
    tags: list[Tag],
) -> tuple[list[Item], dict[int, str | None]]:
    """海报墙上的条目和系列算年份用的日期。搜索或按标签筛选时，折叠的系列和其中的作品都列出。"""
    library_id = library.id
    if library.data_type.structure != "item_group":
        mode = libraries.WALL_MOVIES
    elif (term or tags) and library.wall_collections == libraries.WALL_COLLAPSE:
        mode = libraries.WALL_BOTH
    else:
        mode = library.wall_collections
    parts = (
        select(
            Item.parent_id.label("parent_id"),
            func.count().label("part_count"),
            func.min(Item.release_date).label("first_date"),
        )
        .where(Item.library_id == library_id, Item.kind == "work", Item.parent_id.is_not(None))
        .group_by(Item.parent_id)
        .subquery()
    )
    own = parts.alias("own_parts")
    siblings = parts.alias("sibling_parts")
    is_work = Item.kind == "work"
    if mode == libraries.WALL_MOVIES:
        shown = is_work
    elif mode == libraries.WALL_BOTH:
        shown = is_work | ((Item.kind == "collection") & (own.c.part_count >= 1))
    else:
        shown = (is_work & (func.coalesce(siblings.c.part_count, 0) < 2)) | (
            (Item.kind == "collection") & (own.c.part_count >= 2)
        )
    query = (
        select(Item, own.c.first_date)
        .outerjoin(own, own.c.parent_id == Item.id)
        .outerjoin(siblings, siblings.c.parent_id == Item.parent_id)
        .where(Item.library_id == library_id, shown)
    )
    if term:
        query = query.where(
            Item.title.icontains(term, autoescape=True)
            | Item.original_title.icontains(term, autoescape=True)
            | Item.sort_title.icontains(term, autoescape=True)
        )
    for tag in tags:
        query = query.where(exists().where(ItemTag.item_id == Item.id, ItemTag.tag_id == tag.id))
    column = {
        "title": func.coalesce(Item.sort_title, Item.title),
        "release_date": func.coalesce(Item.release_date, own.c.first_date),
        "created_at": Item.created_at,
        "updated_at": Item.updated_at,
    }[sort]
    ordered = column.desc() if order == "desc" else column.asc()
    query = query.order_by(column.is_(None), ordered, Item.id)
    rows = session.execute(query).all()
    years = {item.id: first for item, first in rows if item.kind == "collection"}
    return [item for item, _ in rows], years


@router.get("/items/{item_id}", response_model=ItemDetail)
def read_item(item_id: int, session: SessionDep) -> dict[str, Any]:
    return detail_of(session, get_item(session, item_id))


@router.get("/items/{item_id}/delete-preview", response_model=DeletePreview)
def preview_delete(item_id: int, session: SessionDep, services: ServicesDep) -> DeletePreview:
    item = get_item(session, item_id)
    plan = plan_delete(session, item, services.trash, _video_extensions(services))
    return DeletePreview(
        item_id=item.id,
        title=item.title,
        library_id=item.library_id,
        folders=plan.folders,
        files=plan.files,
        file_count=plan.file_count,
        total_size=plan.total_size,
        permanent=plan.permanent,
        unregistered=Unregistered(
            count=plan.unregistered_count,
            total_size=plan.unregistered_size,
            sample=plan.unregistered_sample,
        ),
        reasons=plan.reasons,
    )


@router.delete("/items/{item_id}", response_model=Deleted)
def remove_item(
    item_id: int, body: DeleteRequest, session: SessionDep, services: ServicesDep
) -> Deleted:
    """部分文件没删掉时返回 409：已删的已从库里去掉，前端重新取预览让用户再确认一次。"""
    item = get_item(session, item_id)
    check_deletable(session, item)
    if body.confirm_title.strip() != item.title.strip():
        raise ItemError("输入的标题和条目不一致", status_code=400)
    library_id = delete_item(
        session,
        item,
        services.trash,
        Expected(**body.expected.model_dump()),
        video_extensions=_video_extensions(services),
        data_dir=services.data_dir,
        locks=services.locks,
    )
    return Deleted(deleted=True, library_id=library_id)


def _video_extensions(services: Services) -> frozenset[str]:
    config = services.config_store.load_config()
    return frozenset(ext.lower() for ext in config.files.video_extensions)


@router.patch("/items/{item_id}/fields", response_model=ItemDetail)
def update_fields(
    item_id: int, body: FieldsUpdate, session: SessionDep, runner: RunnerDep
) -> dict[str, Any]:
    item = get_item(session, item_id)
    edit_fields(session, item, body.values)
    rewrite_if_arranged(session, runner, item)
    return detail_of(session, item)


@router.put("/items/{item_id}/locks", response_model=ItemDetail)
def update_locks(item_id: int, body: LocksUpdate, session: SessionDep) -> dict[str, Any]:
    item = get_item(session, item_id)
    set_locks(session, item, body.locked)
    return detail_of(session, item)


@router.post("/items/{item_id}/scrape", response_model=Enqueued, status_code=202)
def scrape_item(
    item_id: int, session: SessionDep, runner: RunnerDep, body: ScrapeRequest | None = None
) -> Enqueued:
    force = body.force if body is not None else True
    return Enqueued(task_id=scrape_work(session, runner, get_item(session, item_id), force=force))


def scrape_work(session: Session, runner: TaskRunner, item: Item, *, force: bool) -> int:
    if item.kind == "collection":
        raise ItemError("系列电影跟着其中的电影一起刮削")
    if item.kind != "work":
        raise ItemError("季和集跟着剧一起刮削")
    return enqueue_scrape(session, runner, item.id, force=force)


@router.post("/items/{item_id}/rematch", response_model=Enqueued, status_code=202)
def rematch_item(
    item_id: int,
    body: RematchRequest,
    session: SessionDep,
    runner: RunnerDep,
    services: ServicesDep,
) -> Enqueued:
    task_id = rematch_work(
        session,
        runner,
        services,
        get_item(session, item_id),
        tmdb_id=body.tmdb_id,
        source=body.source,
        external_id=body.external_id,
    )
    return Enqueued(task_id=task_id)


def rematch_work(
    session: Session,
    runner: TaskRunner,
    services: Services,
    item: Item,
    *,
    tmdb_id: int | None = None,
    source: str | None = None,
    external_id: str | None = None,
) -> int:
    """TMDB 类型给 tmdb_id；插件类型给来源插件 id 和外部编号。换完入队刮削，锁定的字段保留。"""
    if item.kind != "work":
        raise ItemError("只有电影和剧能重新匹配")
    if source:
        plugin = _source_of(services, item.data_type_id, source)
        rematch_external(session, item, plugin.provider, _external_id(external_id))
    elif tmdb_id is not None:
        rematch(session, item, tmdb_id)
    else:
        raise ItemError("要给出 TMDB 编号或来源编号")
    return enqueue_scrape(session, runner, item.id, force=False)


@router.post("/items/{item_id}/sources", response_model=Enqueued, status_code=202)
def add_item_source(
    item_id: int,
    body: SourceRequest,
    session: SessionDep,
    runner: RunnerDep,
    services: ServicesDep,
) -> Enqueued:
    item = get_item(session, item_id)
    if item.kind != "work":
        raise ItemError("只有作品能补充来源")
    plugin = _source_of(services, item.data_type_id, body.source)
    add_source(session, item, plugin.id, plugin.provider, _external_id(body.external_id))
    return Enqueued(task_id=enqueue_scrape(session, runner, item.id, force=False))


@router.get("/media-files/unmatched", response_model=list[UnmatchedFile])
def list_unmatched(session: SessionDep, library_id: int | None = None) -> list[UnmatchedFile]:
    return unmatched_files(session, library_id)


def unmatched_files(session: Session, library_id: int | None) -> list[UnmatchedFile]:
    """剧库同一个剧目录的视频合成一行。"""
    query = (
        select(MediaFile, Library)
        .join(Library, Library.id == MediaFile.library_id)
        .where(MediaFile.item_id.is_(None), MediaFile.file_kind.in_(("video", "game")))
        .order_by(MediaFile.path)
    )
    if library_id is not None:
        query = query.where(MediaFile.library_id == library_id)
    rows: list[UnmatchedFile] = []
    shows: dict[tuple[int, str], UnmatchedFile] = {}
    series: dict[int, bool] = {}
    for media_file, library in session.execute(query).all():
        if library.id not in series:
            series[library.id] = is_series(session, library)
        folder = show_folder(library, media_file.path) if series[library.id] else None
        if folder is not None and (library.id, folder) in shows:
            row = shows[(library.id, folder)]
            row.video_count += 1
            row.size = (row.size or 0) + (media_file.size or 0)
            continue
        parsed = guess(
            library,
            media_file.path,
            series=series[library.id],
            folder=media_file.file_kind == "game",
        )
        row = UnmatchedFile(
            id=media_file.id,
            library_id=library.id,
            library_name=library.name,
            data_type_id=library.data_type_id,
            path=folder or media_file.path,
            size=media_file.size,
            guess_title=parsed.title,
            guess_year=parsed.year,
            video_count=1,
        )
        if folder is not None:
            shows[(library.id, folder)] = row
        rows.append(row)
    return rows


@router.post("/media-files/{file_id}/bind", response_model=Bound, status_code=202)
def bind_media_file(
    file_id: int, body: BindRequest, session: SessionDep, runner: RunnerDep, services: ServicesDep
) -> Bound:
    media_file = session.get(MediaFile, file_id)
    if media_file is None:
        raise HTTPException(status_code=404, detail="没有这个文件")
    return bind_media(
        session,
        runner,
        services,
        media_file,
        tmdb_id=body.tmdb_id,
        source=body.source,
        external_id=body.external_id,
        title=body.title,
        original_title=body.original_title,
        release_date=body.release_date,
    )


def bind_media(
    session: Session,
    runner: TaskRunner,
    services: Services,
    media_file: MediaFile,
    *,
    tmdb_id: int | None = None,
    source: str | None = None,
    external_id: str | None = None,
    title: str,
    original_title: str | None = None,
    release_date: str | None = None,
) -> Bound:
    """把未识别的视频或游戏目录绑到一个作品上，再入队刮削。title 只是刮削前的占位。"""
    if source:
        library = session.get(Library, media_file.library_id)
        plugin = _source_of(services, library.data_type_id if library else "", source)
        external = ExternalRef(
            provider=plugin.provider,
            external_id=_external_id(external_id),
            title=title.strip(),
            original_title=original_title,
            release_date=release_date,
        )
        item = bind_folder(session, media_file, external, services.locks)
    elif tmdb_id is not None:
        ref = MovieRef(
            tmdb_id=tmdb_id,
            title=title.strip(),
            original_title=original_title,
            release_date=release_date,
        )
        item = bind_file(session, media_file, ref, services.locks)
    else:
        raise ItemError("要给出 TMDB 编号或来源编号")
    return Bound(item_id=item.id, task_id=enqueue_scrape(session, runner, item.id, force=False))


def _external_id(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ItemError("缺少来源编号")
    return cleaned


def _source_of(services: Services, data_type_id: str, source_id: str) -> SourcePlugin:
    plugin = services.plugins.source(source_id)
    if plugin is None or plugin.data_type != data_type_id:
        raise ItemError("这个来源插件不存在、没有启用，或不属于这种数据类型")
    return plugin
