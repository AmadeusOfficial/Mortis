"""写出文件和重命名的任务。动磁盘和改 media_files 都在 catalog-write 锁里。"""

from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from mortis.config.models import MovieScrapeConfig
from mortis.db.models import Item, Library, MediaFile
from mortis.output.writer import WRITTEN_KINDS, write_item_files
from mortis.rename.apply import RenameError, apply_rename
from mortis.rename.plan import plan_rename
from mortis.scrape.images import ImageStore
from mortis.tasks.base import Followup, TaskContext, TaskError, TaskResult


def _config(ctx: TaskContext) -> MovieScrapeConfig:
    return ctx.services.config_store.load_config().movie


def _work(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None or item.kind != "work":
        raise TaskError("条目不存在，可能已被删除")
    return item


def has_video(session: Session, item_id: int) -> bool:
    return bool(
        session.scalar(
            select(exists().where(MediaFile.item_id == item_id, MediaFile.file_kind == "video"))
        )
    )


def _has_written(session: Session, item_id: int) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(MediaFile.item_id == item_id, MediaFile.file_kind.in_(WRITTEN_KINDS))
            )
        )
    )


def after_scrape(config: MovieScrapeConfig, item_id: int) -> list[Followup]:
    output = config.output
    if output.rename_after_scrape:
        return [Followup("rename", {"item_id": item_id, "write": output.write_after_scrape})]
    if output.write_after_scrape:
        return [Followup("write_files", {"item_id": item_id})]
    return []


def handle_write_files(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    config = _config(ctx)
    store = ImageStore(ctx.services.data_dir)
    with ctx.services.locks.hold("catalog-write"), ctx.session_factory() as session:
        item = _work(session, int(payload["item_id"]))
        report = write_item_files(session, store, item, config)
        session.commit()
        title = item.title
    for path in report.skipped:
        ctx.log(f"图片不在本地图库里，跳过：{path}")
    ctx.log(
        f"写出文件：{title}，新写 {len(report.written)} 个，未变 {len(report.unchanged)} 个，"
        f"删掉旧的 {len(report.removed)} 个"
    )
    return TaskResult(
        result={
            "item_id": int(payload["item_id"]),
            "title": title,
            "written": len(report.written),
            "unchanged": len(report.unchanged),
            "removed": len(report.removed),
            "skipped": len(report.skipped),
        }
    )


def handle_rename(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    config = _config(ctx)
    store = ImageStore(ctx.services.data_dir)
    with ctx.services.locks.hold("catalog-write"), ctx.session_factory() as session:
        item = _work(session, int(payload["item_id"]))
        title = item.title
        plan = plan_rename(session, item, config.output)
        if plan.problems:
            raise TaskError(f"「{title}」没有重命名：" + "；".join(plan.problems))
        rewrite = bool(payload.get("write")) or _has_written(session, item.id)
        if plan.changes:
            try:
                apply_rename(session, plan)
            except RenameError as error:
                raise TaskError(str(error)) from None
            session.commit()
            for move in plan.moves:
                if move.source != move.target:
                    ctx.log(f"{move.source} → {move.target}")
        else:
            ctx.log(f"「{title}」已经是目标名字")
        written = 0
        if rewrite:
            report = write_item_files(session, store, item, config)
            session.commit()
            written = len(report.written)
    return TaskResult(
        result={
            "item_id": int(payload["item_id"]),
            "title": title,
            "renamed": sum(m.source != m.target for m in plan.moves),
            "folder": plan.folder_to,
            "written": written,
        }
    )


def _library_items(session: Session, library_id: int) -> list[int]:
    if session.get(Library, library_id) is None:
        raise TaskError("媒体库不存在，可能已被删除")
    return list(
        session.scalars(
            select(Item.id)
            .where(
                Item.library_id == library_id,
                Item.kind == "work",
                exists().where(MediaFile.item_id == Item.id, MediaFile.file_kind == "video"),
            )
            .order_by(Item.id)
        )
    )


def handle_write_files_library(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    with ctx.session_factory() as session:
        ids = _library_items(session, int(payload["library_id"]))
    ctx.log(f"排了 {len(ids)} 个写出任务")
    return TaskResult(
        result={"queued": len(ids)},
        followups=[Followup("write_files", {"item_id": item_id}) for item_id in ids],
    )


def handle_rename_library(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    with ctx.session_factory() as session:
        ids = _library_items(session, int(payload["library_id"]))
    ctx.log(f"排了 {len(ids)} 个重命名任务")
    return TaskResult(
        result={"queued": len(ids)},
        followups=[Followup("rename", {"item_id": item_id}) for item_id in ids],
    )
