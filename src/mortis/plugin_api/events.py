"""事件钩子收到的事件。全部不可变，只带 id 和必要字段，要更多信息请用 `host.get_item(id)`。

约定：
- 每个事件都是 Pydantic 模型，`frozen`、`extra="ignore"`；以后新增的字段都有默认值，旧插件不受影响。
- `type` 是 `EventType`（StrEnum），可以直接和字符串比较，比如 `event.type == "scrape.finished"`。
- `data_type` 是条目所属数据类型的 id，没有条目的事件为空字符串；
  清单里写了 `data_types` 的钩子只收到 `data_type` 在名单里的事件。
- 事件在库提交成功之后才发布，钩子不能中断或改写宿主的流程。
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

__all__ = [
    "AnyEvent",
    "ArrangeFinished",
    "Event",
    "EventType",
    "ItemCreated",
    "PlayStarted",
    "ScanFinished",
    "ScrapeFailed",
    "ScrapeFinished",
    "ScrapeStarted",
    "TaskFailed",
    "event_from_dict",
    "event_model",
]


class EventType(StrEnum):
    ITEM_CREATED = "item.created"
    SCAN_FINISHED = "scan.finished"
    SCRAPE_STARTED = "scrape.started"
    SCRAPE_FINISHED = "scrape.finished"
    SCRAPE_FAILED = "scrape.failed"
    ARRANGE_FINISHED = "arrange.finished"
    PLAY_STARTED = "play.started"
    TASK_FAILED = "task.failed"


class Event(BaseModel):
    """所有事件的共同部分。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    type: EventType
    data_type: str = ""


class ItemCreated(Event):
    """扫描识别后新建了一个条目（电影、剧或插件类型的条目）。"""

    type: Literal[EventType.ITEM_CREATED] = EventType.ITEM_CREATED
    item_id: int = 0
    library_id: int = 0


class ScanFinished(Event):
    """一次扫描整理任务结束。"""

    type: Literal[EventType.SCAN_FINISHED] = EventType.SCAN_FINISHED
    library_id: int = 0
    found: int = 0
    added: int = 0
    removed: int = 0
    matched: int = 0
    unmatched: int = 0


class ScrapeStarted(Event):
    """一个条目的刮削已经开始。和 finished、failed 在刮削结束后一起发布。"""

    type: Literal[EventType.SCRAPE_STARTED] = EventType.SCRAPE_STARTED
    item_id: int = 0
    library_id: int = 0
    task_id: int = 0
    force: bool = False


class ScrapeFinished(Event):
    """一个条目刮削成功，库已提交。"""

    type: Literal[EventType.SCRAPE_FINISHED] = EventType.SCRAPE_FINISHED
    item_id: int = 0
    library_id: int = 0
    task_id: int = 0
    force: bool = False


class ScrapeFailed(Event):
    """一个条目刮削失败。`error` 是给用户看的原因。"""

    type: Literal[EventType.SCRAPE_FAILED] = EventType.SCRAPE_FAILED
    item_id: int = 0
    library_id: int = 0
    task_id: int = 0
    force: bool = False
    error: str = ""


class ArrangeFinished(Event):
    """一个条目的整理（改名、写 NFO 和图片）完成。"""

    type: Literal[EventType.ARRANGE_FINISHED] = EventType.ARRANGE_FINISHED
    item_id: int = 0
    library_id: int = 0
    task_id: int = 0
    renamed: int = 0
    written: int = 0


class PlayStarted(Event):
    """已经用播放插件打开了一个文件或启动了一个程序。"""

    type: Literal[EventType.PLAY_STARTED] = EventType.PLAY_STARTED
    item_id: int = 0
    file_id: int = 0
    player: str = ""


class TaskFailed(Event):
    """一个任务失败了。钩子任务自己失败不会触发它，防止循环。

    任务的 payload 里有 `item_id` 时，`item_id` 和 `data_type` 会带上。
    """

    type: Literal[EventType.TASK_FAILED] = EventType.TASK_FAILED
    task_id: int = 0
    task_type: str = ""
    item_id: int = 0
    error: str = ""


AnyEvent = Annotated[
    ItemCreated
    | ScanFinished
    | ScrapeStarted
    | ScrapeFinished
    | ScrapeFailed
    | ArrangeFinished
    | PlayStarted
    | TaskFailed,
    Field(discriminator="type"),
]

_ADAPTER: TypeAdapter[AnyEvent] = TypeAdapter(AnyEvent)

_MODELS: dict[EventType, type[Event]] = {
    EventType.ITEM_CREATED: ItemCreated,
    EventType.SCAN_FINISHED: ScanFinished,
    EventType.SCRAPE_STARTED: ScrapeStarted,
    EventType.SCRAPE_FINISHED: ScrapeFinished,
    EventType.SCRAPE_FAILED: ScrapeFailed,
    EventType.ARRANGE_FINISHED: ArrangeFinished,
    EventType.PLAY_STARTED: PlayStarted,
    EventType.TASK_FAILED: TaskFailed,
}


def event_model(event_type: str) -> type[Event] | None:
    """事件名对应的模型；不存在的事件名返回 None。"""
    try:
        return _MODELS[EventType(event_type)]
    except ValueError:
        return None


def event_from_dict(data: dict[str, object]) -> Event:
    """从任务 payload 里存的字典还原事件。"""
    return _ADAPTER.validate_python(data)
