"""整理任务：按设置改名，并把 NFO 和图片写到视频旁边。

动磁盘和改 media_files 都在 catalog-write 锁里。
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from mortis.config.models import AppConfig, MovieScrapeConfig, TvScrapeConfig
from mortis.config.types import type_config
from mortis.db.models import Item, ItemSourceRecord, Library, MediaFile
from mortis.hooks.dispatch import hook_followups
from mortis.items.identity import work_of
from mortis.output.tv import write_show_files
from mortis.output.writer import WRITTEN_KINDS, WriteReport, write_item_files
from mortis.plugin_api.events import ArrangeFinished
from mortis.rename.apply import RenameError, apply_rename
from mortis.rename.plan import RenamePlan, plan_rename
from mortis.rename.tv_plan import plan_show_rename
from mortis.scrape.images import ImageStore
from mortis.tasks import repo as task_repo
from mortis.tasks.base import Followup, TaskContext, TaskError, TaskResult

if TYPE_CHECKING:
    from mortis.services import Services
    from mortis.tasks.runner import TaskRunner


type ArrangeConfig = MovieScrapeConfig | TvScrapeConfig


def arrange_config(config: AppConfig, data_type_id: str) -> ArrangeConfig:
    """插件类型不写 NFO、不重命名；预览按电影模板算，界面上不给它们整理。"""
    section = type_config(config, data_type_id)
    if isinstance(section, MovieScrapeConfig | TvScrapeConfig):
        return section
    return config.types.movie


def config_for(services: Services, item: Item) -> ArrangeConfig:
    return arrange_config(services.config_store.load_config(), item.data_type_id)


def plan_for(session: Session, item: Item, config: ArrangeConfig) -> RenamePlan:
    if isinstance(config, TvScrapeConfig):
        return plan_show_rename(session, item, config.output)
    return plan_rename(session, item, config.output)


def write_for(
    session: Session, store: ImageStore, item: Item, config: ArrangeConfig
) -> WriteReport:
    if isinstance(config, TvScrapeConfig):
        return write_show_files(session, store, item, config)
    return write_item_files(session, store, item, config)


def _video_owner(item_id: Any) -> Any:
    """视频挂在这个条目上，或挂在它下面的集上。"""
    seasons = select(Item.id).where(Item.parent_id == item_id, Item.kind == "season")
    episodes = select(Item.id).where(Item.parent_id.in_(seasons))
    return (MediaFile.item_id == item_id) | MediaFile.item_id.in_(episodes)


def _work(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None or item.kind != "work":
        raise TaskError("条目不存在，可能已被删除")
    return item


def has_video(session: Session, item_id: int) -> bool:
    return bool(
        session.scalar(
            select(exists().where(_video_owner(item_id), MediaFile.file_kind == "video"))
        )
    )


def is_scraped(session: Session, item_id: int) -> bool:
    return bool(session.scalar(select(exists().where(ItemSourceRecord.item_id == item_id))))


def has_written(session: Session, item_id: int) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(MediaFile.item_id == item_id, MediaFile.file_kind.in_(WRITTEN_KINDS))
            )
        )
    )


def after_scrape(config: ArrangeConfig, item_id: int) -> list[Followup]:
    if config.output.organize_after_scrape:
        return [Followup("arrange", {"item_id": item_id})]
    return []


def arrange_steps(
    session: Session, item_id: int, config: ArrangeConfig, payload: dict[str, Any]
) -> tuple[bool, bool]:
    """返回（改不改名，写不写文件）。已经写过文件的条目改名后一定重写，免得留下旧名字的文件。"""
    output = config.output
    rename = output.organize_rename if payload.get("rename") is None else bool(payload["rename"])
    write = payload.get("write")
    if write is None:
        write = output.organize_write_files or has_written(session, item_id)
    return rename, bool(write)


def handle_arrange(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    store = ImageStore(ctx.services.data_dir)
    item_id = int(payload["item_id"])
    problems: list[str] = []
    renamed = 0
    written = 0
    folder: str | None = None
    with ctx.services.locks.hold("catalog-write"), ctx.session_factory() as session:
        item = _work(session, item_id)
        config = config_for(ctx.services, item)
        title = item.title
        library_id, data_type_id = item.library_id, item.data_type_id
        rename, write = arrange_steps(session, item_id, config, payload)
        if rename:
            plan = plan_for(session, item, config)
            problems = plan.problems
            if not problems and plan.changes:
                try:
                    apply_rename(session, plan)
                except RenameError as error:
                    problems = [str(error)]
                else:
                    session.commit()
                    folder = plan.folder_to
                    for move in plan.moves:
                        if move.source != move.target:
                            renamed += 1
                            ctx.log(f"{move.source} → {move.target}")
            elif not problems:
                ctx.log(f"「{title}」已经是目标名字")
        if write:
            report = write_for(session, store, item, config)
            session.commit()
            written = len(report.written)
            for path in report.skipped:
                ctx.log(f"图片不在本地图库里，跳过：{path}")
            ctx.log(
                f"写出 NFO 和图片：新写 {len(report.written)} 个，未变 {len(report.unchanged)} 个，"
                f"删掉旧的 {len(report.removed)} 个"
            )
    if problems:
        done = "，NFO 和图片已写出" if write else ""
        raise TaskError(f"「{title}」没有重命名：" + "；".join(problems) + done)
    followups: list[Followup] = []
    if not payload.get("silent"):
        followups = hook_followups(
            ctx,
            ArrangeFinished(
                data_type=data_type_id,
                item_id=item_id,
                library_id=library_id,
                task_id=ctx.task_id,
                renamed=renamed,
                written=written,
            ),
        )
    return TaskResult(
        result={
            "item_id": item_id,
            "title": title,
            "renamed": renamed,
            "folder": folder,
            "written": written,
        },
        followups=followups,
    )


def enqueue_arrange(session: Session, runner: TaskRunner, item_id: int, **options: object) -> int:
    queued = task_repo.find_queued(session, "arrange", "item_id", item_id)
    if queued is not None:
        return queued.id
    task = task_repo.enqueue(session, "arrange", {"item_id": item_id, **options}, priority=1)
    session.commit()
    runner.wake()
    return task.id


def rewrite_if_arranged(
    session: Session, runner: TaskRunner, item: Item, *, silent: bool = False
) -> None:
    """库是唯一的依据：整理过的条目手动改了字段，就重写它的 NFO，不改名。

    silent 的整理任务不发布 arrange.finished，失败也不触发 task.failed（钩子写标签用）。
    """
    if item.kind == "collection":
        return
    work_id = work_of(session, item.id)
    if has_written(session, work_id):
        options: dict[str, object] = {"rename": False, "write": True}
        if silent:
            options["silent"] = True
        enqueue_arrange(session, runner, work_id, **options)


def arrangeable_items(session: Session, library_id: int) -> list[int]:
    """整库整理只管刮削过、有视频的电影和剧。"""
    return list(
        session.scalars(
            select(Item.id)
            .where(
                Item.library_id == library_id,
                Item.kind == "work",
                exists().where(_video_owner(Item.id), MediaFile.file_kind == "video"),
                exists().where(ItemSourceRecord.item_id == Item.id),
            )
            .order_by(Item.id)
        )
    )


def handle_arrange_library(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
    library_id = int(payload["library_id"])
    with ctx.services.locks.hold(f"library:{library_id}"), ctx.session_factory() as session:
        if session.get(Library, library_id) is None:
            raise TaskError("媒体库不存在，可能已被删除")
        ids = arrangeable_items(session, library_id)
    ctx.log(f"排了 {len(ids)} 个整理任务")
    return TaskResult(
        result={"queued": len(ids)},
        followups=[Followup("arrange", {"item_id": item_id}) for item_id in ids],
    )
