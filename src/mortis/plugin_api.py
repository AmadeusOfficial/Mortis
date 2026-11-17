"""宿主公布给插件的接口。插件只能 import 标准库、pydantic 和这个模块。

来源插件的 `candidates` 返回 {字段键: 值}，值的格式按字段的值类型：

- text、longtext、url：字符串；date：`YYYY-MM-DD`；number：数字。
- tags：字符串列表。
- people：`{"external_id", "name", "original_name", "character", "photo_url"}` 的列表，
  `external_id` 是这个来源里的人物编号，宿主按「来源 + 编号」认人。
- images：`{"url", "language", "width", "height"}` 的列表，排第一的会被选用。
- rating：`{"source", "rating", "max_rating", "vote_count"}` 的列表。

播放插件只调用本机播放器打开本地文件，经 `PlayerHost.launch` 或 `open_default` 启动，
不自己 import subprocess，不转码，也不向浏览器输出视频。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from mortis.catalog.spec import DataTypeSpec, FieldSpec

__all__ = [
    "Account",
    "DataTypePlugin",
    "DataTypeSpec",
    "FieldSpec",
    "Hit",
    "Host",
    "NoSecrets",
    "NoSettings",
    "OAuth",
    "PlayerError",
    "PlayerHost",
    "PlayerPlugin",
    "SourceAuthError",
    "SourceError",
    "SourcePlugin",
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


@dataclass(frozen=True)
class DataTypePlugin:
    """数据类型插件。default_orders 的键是 ""（整体）或字段键，值是来源 id 的顺序。"""

    spec: DataTypeSpec
    default_orders: dict[str, list[str]] = field(default_factory=dict)
    scan: Literal["folder"] = "folder"


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


class PlayerHost(Protocol):
    """宿主给播放插件的能力：读设置、启动本机程序。"""

    settings: BaseModel

    def launch(self, executable: str, args: list[str]) -> None:
        """不经 shell 启动程序，不等它结束，也不接管它的输出。失败时抛 PlayerError。"""
        ...

    def open_default(self, path: str) -> None:
        """用系统给这种文件设的默认程序打开。失败时抛 PlayerError。"""
        ...

    def log(self, message: str) -> None: ...


class PlayerPlugin(ABC):
    """播放插件：用本机播放器打开一个本地视频文件。"""

    id: ClassVar[str]
    name: ClassVar[str]
    Settings: ClassVar[type[BaseModel]] = NoSettings

    def available(self, host: PlayerHost) -> bool:
        return self.unavailable_reason(host) is None

    def unavailable_reason(self, host: PlayerHost) -> str | None:
        """不可用时给用户看的原因，比如「没找到播放器」。"""
        return None

    def detected_path(self, host: PlayerHost) -> str | None:
        """实际会启动的程序路径，设置页显示；不适用时返回 None。"""
        return None

    @abstractmethod
    def play(self, host: PlayerHost, path: str) -> None:
        """打开 path。宿主已确认文件存在。失败时抛 PlayerError。"""
