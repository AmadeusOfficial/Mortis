"""宿主公布给插件的接口。插件只能 import 标准库、pydantic 和这个模块。

这里是接口的完整说明；约定、清单和打包的规则写在 docs/ 下的《插件开发指南》《插件规范》
《事件参考》《打包与导入》里。

字段目录（`DataTypeSpec` 和 `FieldSpec`）由数据类型插件提交，宿主校验：

- `DataTypeSpec.structure` 只能是 `item`（单条目）、`item_group`（单条目加分组）、
  `series`（作品、季、集）三选一，它决定有哪几层：`item` 只有 `work`，`item_group` 是
  `collection` 加 `work`，`series` 是 `work`、`season`、`episode`。
- `FieldSpec.value_type` 只能是 text、longtext、number、date、rating、people、tags、images、url。
- `FieldSpec.stored_in` 决定值存在哪里，并要求填对应的那一项：`items_column` 填 `column_name`
  （只有 title、original_title、sort_title、overview、release_date、poster_path 这几个共有列）、
  `tags` 填 `tag_kind`、`people` 填 `person_role`、`images` 填 `image_kind`；`ratings` 和
  `fields`（文档里的自由字段，只放 text、longtext、number、date、url）什么都不填。
- `applies_to` 是字段所在的层；每一层都要有存进 items 的 title 列的 `title` 字段。
- `is_lockable` 能否锁定（默认能）；`is_filterable` 能否筛选（放在 `fields` 的不行）；
  `is_sortable` 能否排序（只有共有列和评分）；`detail_section` 是详情页的分区名。
- `subtitle_field` 是海报墙副标题用的字段，必须是作品层的共有列或标签字段。
- 字段键和类型 id 只能用小写字母、数字和下划线，字母开头；不能和内置类型同 id。

来源插件的 `candidates` 返回 {字段键: 值}，值的格式按字段的值类型：

- text、longtext、url：字符串；date：`YYYY-MM-DD`；number：数字。
- tags：字符串列表。
- people：`{"external_id", "name", "original_name", "character", "photo_url"}` 的列表，
  `external_id` 是这个来源里的人物编号，宿主按「来源 + 编号」认人。
- images：`{"url", "language", "width", "height"}` 的列表，排第一的会被选用。
- rating：`{"source", "rating", "max_rating", "vote_count"}` 的列表。

播放插件用本机播放器打开本地视频，或启动游戏文件夹里的程序，经 `PlayerHost.launch` 或
`open_default` 启动，不自己 import subprocess，不转码，也不向浏览器输出视频。

一个插件包可以带来源、播放、事件钩子和数据类型，想带几样带几样：

- `PLUGIN = MySource()` 或 `PLUGIN = MyPlayer()`：单个对象的简写，plugin.toml 里不再列子项。
- `PLUGIN = DataTypePlugin(...)`：数据类型，可带它专用的来源和播放。
- `PLUGIN = Plugin(sources=(...), players=(...), hooks=(...), data_type=...)`：任意组合。

plugin.toml 的 `[[sources]]`、`[[players]]`、`[[hooks]]` 要和代码里的对象按 id 一一对上
（单个对象的简写除外：对象的 id 就是包 id，不再列子项）。事件钩子见 `HookPlugin`、`HookHost`
和 `mortis.plugin_api.events`，写测试用 `mortis.plugin_api.testing` 里的假宿主。

设置表单（`Settings`、`Secrets` 的 JSON Schema）只支持下面这个子集，其余写法按文本框处理：

- `type: boolean`：开关；`type: integer` 或 `number`：数字框；`type: string`：文本框。
- `format: "file-path"`（字符串字段，用 `json_schema_extra={"format": "file-path"}` 写）：
  带文件选择的路径框。
- `title` 当标签，`description` 当提示，`default` 当初始值。
- `Secrets` 里的字段一律按密码框显示，只存字符串。
- 不支持嵌套对象、数组、`enum`、`oneOf`；界面不会渲染它们。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict

# 字段目录的两个类型定义在宿主的 catalog 模块里；作者只从本模块引用。
from mortis.catalog.spec import DataTypeSpec, FieldSpec
from mortis.plugin_api.events import (
    AnyEvent,
    ArrangeFinished,
    Event,
    EventType,
    ItemCreated,
    PlayStarted,
    ScanFinished,
    ScrapeFailed,
    ScrapeFinished,
    ScrapeStarted,
    TaskFailed,
)
from mortis.plugin_api.hooks import (
    HookError,
    HookHost,
    HookLimitError,
    HookPermissionError,
    ItemView,
    TagResult,
)

__all__ = [
    "Account",
    "AnyEvent",
    "ArrangeFinished",
    "DataTypePlugin",
    "DataTypeSpec",
    "Event",
    "EventType",
    "FieldSpec",
    "Hit",
    "HookError",
    "HookHost",
    "HookLimitError",
    "HookPermissionError",
    "HookPlugin",
    "Host",
    "ItemCreated",
    "ItemView",
    "NoSecrets",
    "NoSettings",
    "OAuth",
    "PlayerError",
    "PlayerHost",
    "PlayStarted",
    "PlayerPlugin",
    "Plugin",
    "ScanFinished",
    "ScrapeFailed",
    "ScrapeFinished",
    "ScrapeStarted",
    "SourceAuthError",
    "SourceError",
    "SourcePlugin",
    "TagResult",
    "TaskFailed",
]


class SourceError(Exception):
    """请求来源失败。消息给用户看，不要带令牌。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SourceAuthError(SourceError):
    """来源拒绝了令牌或密钥。"""


class PlayerError(Exception):
    """播放器没能打开文件。消息给用户看。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NoSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")


class NoSecrets(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Hit(BaseModel):
    """搜索结果。宿主用 title、original_title、aliases 和年份算匹配度。"""

    external_id: str
    title: str
    original_title: str | None = None
    aliases: list[str] = []
    year: int | None = None
    overview: str | None = None
    poster_url: str | None = None


class Account(BaseModel):
    """登录来源后的账号，设置页显示。"""

    name: str
    avatar_url: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class OAuth:
    """需要用户登录的来源。client_id、client_secret 从插件密钥的这两个键里取。"""

    authorize_url: str
    token_url: str
    scope: str = ""
    client_id_key: str = "app_id"
    client_secret_key: str = "app_secret"
    register_url: str = ""
    """用户自己建 OAuth 应用的页面，设置页给出链接。"""
    token_page_url: str = ""
    """能直接生成访问令牌的页面；为空时设置页不提供手填令牌。"""


class Host(Protocol):
    """宿主给插件的能力。网络请求必须走这里，宿主负责代理、限速和任务记录。"""

    settings: BaseModel
    secrets: BaseModel

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any: ...

    def post_json(
        self,
        url: str,
        *,
        json: Any = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any: ...

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str: ...

    def access_token(self) -> str | None:
        """OAuth 来源的当前令牌，快过期时宿主先续期；没登录时是 None。"""
        ...

    def log(self, message: str) -> None: ...


class SourcePlugin(ABC):
    """元数据来源插件，只给一种数据类型填字段。"""

    id: ClassVar[str]
    name: ClassVar[str]
    data_type: ClassVar[str]
    provider: ClassVar[str]
    """外部编号写进 item_external_ids 用的名字，通常和 id 相同。"""
    Settings: ClassVar[type[BaseModel]] = NoSettings
    Secrets: ClassVar[type[BaseModel]] = NoSecrets
    oauth: ClassVar[OAuth | None] = None
    requests_per_second: ClassVar[float] = 2.0

    def enabled(self, host: Host) -> bool:
        """需要 key 的来源，没填 key 时返回 False，宿主就跳过它。"""
        return True

    def disabled_reason(self, host: Host) -> str | None:
        """设置页上显示的提示，比如「没登录时搜不到 18 禁条目」。"""
        return None

    def account(self, host: Host) -> Account | None:
        """需要登录的来源返回当前令牌对应的账号；令牌失效时抛 SourceAuthError。"""
        return None

    @abstractmethod
    def search(self, host: Host, title: str) -> list[Hit]: ...

    @abstractmethod
    def fetch(self, host: Host, external_id: str) -> dict[str, Any]:
        """请求详情，返回的整份结果存成快照。再次刮削只对快照调用 candidates。"""

    @abstractmethod
    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DataTypePlugin:
    """数据类型插件。default_orders 的键是 ""（整体）或字段键，值是来源 id 的顺序。

    sources 是这个数据类型自带的来源插件，每个的 data_type 必须等于本插件 spec.id。
    players 是它自带的播放插件，比如启动游戏的程序。两者的 id 都要列进 plugin.toml 的
    [[sources]]、[[players]]，停用本插件时一起停用。
    """

    spec: DataTypeSpec
    default_orders: dict[str, list[str]] = field(default_factory=dict)
    scan: Literal["folder"] = "folder"
    sources: tuple[SourcePlugin, ...] = ()
    players: tuple[PlayerPlugin, ...] = ()


class PlayerHost(Protocol):
    """宿主给播放插件的能力：读设置、启动本机程序。"""

    settings: BaseModel

    def launch(self, executable: str, args: list[str], cwd: str | None = None) -> None:
        """不经 shell 启动程序，不等它结束，也不接管它的输出。失败时抛 PlayerError。

        cwd 是程序的工作目录，为 None 时沿用宿主的。游戏通常要传 exe 所在的文件夹，
        否则找不到自己的资源。程序要求管理员权限时宿主改用系统的方式打开，会弹出提权确认。
        """
        ...

    def open_default(self, path: str) -> None:
        """用系统给这种文件设的默认程序打开。失败时抛 PlayerError。"""
        ...

    def log(self, message: str) -> None: ...


class PlayerPlugin(ABC):
    """播放插件：用本机播放器打开一个本地视频文件，或启动游戏文件夹里的程序。

    file_kinds 声明能打开哪种本地文件：`video` 是视频文件，`game` 是整个游戏文件夹。
    打开 game 的插件实现 targets 给出可启动的程序；宿主只有一个候选时直接启动，
    多个时交给页面选。选过的记在本插件设置的 `executables` 里（键是游戏文件夹的绝对路径，
    值是相对它的程序路径），由宿主读写，设置页不显示；Settings 里声明
    `executables: dict[str, str] = {}` 才会记住。
    """

    id: ClassVar[str]
    name: ClassVar[str]
    Settings: ClassVar[type[BaseModel]] = NoSettings
    file_kinds: ClassVar[tuple[str, ...]] = ("video",)

    def available(self, host: PlayerHost) -> bool:
        return self.unavailable_reason(host) is None

    def unavailable_reason(self, host: PlayerHost) -> str | None:
        """不可用时给用户看的原因，比如「没找到播放器」。"""
        return None

    def detected_path(self, host: PlayerHost) -> str | None:
        """实际会启动的程序路径，设置页显示；不适用时返回 None。"""
        return None

    def targets(self, host: PlayerHost, folder: str) -> list[str]:
        """game 插件：folder 里可以启动的程序，返回相对 folder 的路径，最可能的排在前面。

        宿主会丢掉不在 folder 里或不存在的路径。
        """
        return []

    @abstractmethod
    def play(self, host: PlayerHost, path: str) -> None:
        """打开 path。宿主已确认文件存在。失败时抛 PlayerError。

        video 插件收到视频文件的绝对路径；game 插件收到要启动的程序的绝对路径。
        """


class HookPlugin(ABC):
    """事件钩子扩展点。宿主在库提交成功之后发布事件，处理函数不能中断或改写宿主流程。

    订阅哪个事件（必须是 `EventType` 里有的）、限定哪些数据类型、要写什么权限，都写在
    plugin.toml 的 `[[hooks]]` 里（一个钩子对象订阅一个事件）；代码里的对象只用 id 和清单对上。
    `on_event(host, event)` 的两个槽位固定不变，新增的上下文挂在 host 或 event 的属性上。

    钩子在后台任务里运行：同一个插件同时只跑一个，全局同时最多跑两个钩子任务，排队上限 200；
    连续失败 3 次会被自动停用；一次事件处理超过 60 秒只记一次失败，宿主不会强行结束它。
    钩子里不再产生事件，钩子失败也不触发 `task.failed`。事件会重复投递（比如重新刮削），
    处理函数要写成幂等的。
    """

    id: ClassVar[str]
    name: ClassVar[str]

    @abstractmethod
    def on_event(self, host: HookHost, event: Event) -> None: ...


@dataclass(frozen=True)
class Plugin:
    """一个插件包的全部扩展项，想带几样带几样。

    data_type 自己带的来源和播放（DataTypePlugin.sources、players）也算本包的。
    每个对象的 id 要列进 plugin.toml 的 `[[sources]]`、`[[players]]`、`[[hooks]]`，
    且包内、以及和其他插件之间都不能重名。
    """

    sources: tuple[SourcePlugin, ...] = ()
    players: tuple[PlayerPlugin, ...] = ()
    hooks: tuple[HookPlugin, ...] = ()
    data_type: DataTypePlugin | None = None
