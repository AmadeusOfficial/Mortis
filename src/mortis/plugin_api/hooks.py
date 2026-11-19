"""事件钩子拿到的宿主 `HookHost` 和它返回的只读对象。

钩子只能通过 `HookHost` 读条目、增删自己名下的标签、联网和读写自己的数据目录。
权限写在 plugin.toml 的 `[[hooks]] writes` 里，形如 `tags:keyword`；声明之外的写入会抛
`HookPermissionError`。每次事件的联网次数、标签调用次数、标签数量和长度、响应字节数都有上限，
超出抛 `HookLimitError`。钩子抛出的异常会被宿主记下，不影响刮削和其他钩子；
连续失败几次后宿主会自动停用这个钩子。
"""

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DEFAULT_LIMITS",
    "HookBudget",
    "HookError",
    "HookHost",
    "HookLimitError",
    "HookLimits",
    "HookPermissionError",
    "ItemView",
    "TagResult",
    "require_tag_permission",
]


class HookError(Exception):
    """钩子调用宿主能力时的失败。消息给用户看，不要带令牌。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class HookPermissionError(HookError):
    """做了清单里没声明的写入。"""


class HookLimitError(HookError):
    """超出了单次事件的调用额度，或者请求的地址不允许访问。"""


@dataclass(frozen=True)
class HookLimits:
    """宿主对钩子每次事件的限额。一个钩子处理一个事件算一次，额度不跨事件累计。"""

    http_calls: int = 5
    """联网（get_json、post_json）最多几次，跳转的每一跳不另算。"""
    item_reads: int = 50
    """`get_item` 最多几次。"""
    tag_calls: int = 20
    """`add_tags` 和 `remove_tags` 合计最多几次。"""
    tags_per_call: int = 50
    """一次调用最多几个标签。"""
    tags_total: int = 200
    """一次事件里处理的标签总数。"""
    tag_length: int = 64
    """标签名最长几个字。"""
    response_bytes: int = 1_000_000
    """一次响应最多多少字节，超出直接中断。"""
    connect_timeout: float = 5.0
    """连接超时，秒。"""
    read_timeout: float = 10.0
    """读取超时，秒。"""


DEFAULT_LIMITS = HookLimits()

_TAG_KIND = re.compile(r"[a-z][a-z0-9_]*")


def require_tag_permission(writes: Collection[str], tag_kind: str) -> None:
    """写 `tag_kind` 类标签要在清单里声明 `tags:<tag_kind>`。"""
    if not _TAG_KIND.fullmatch(tag_kind) or f"tags:{tag_kind}" not in writes:
        raise HookPermissionError(f"没有声明 tags:{tag_kind}，不能写这类标签")


class HookBudget:
    """一次事件里已经用掉的额度。超出时抛 HookLimitError。"""

    def __init__(self, limits: HookLimits = DEFAULT_LIMITS) -> None:
        self.limits = limits
        self.http_calls = 0
        self.item_reads = 0
        self.tag_calls = 0
        self.tags_total = 0

    def spend_http(self) -> None:
        if self.http_calls >= self.limits.http_calls:
            raise HookLimitError(f"一次事件最多联网 {self.limits.http_calls} 次")
        self.http_calls += 1

    def spend_read(self) -> None:
        if self.item_reads >= self.limits.item_reads:
            raise HookLimitError(f"一次事件最多读 {self.limits.item_reads} 个条目")
        self.item_reads += 1

    def spend_tags(self, names: Iterable[str]) -> list[str]:
        """检查一次标签调用，返回去掉首尾空白的名字。"""
        cleaned = [name.strip() for name in names if isinstance(name, str)]
        if self.tag_calls >= self.limits.tag_calls:
            raise HookLimitError(f"一次事件最多调用 {self.limits.tag_calls} 次标签接口")
        if len(cleaned) > self.limits.tags_per_call:
            raise HookLimitError(f"一次最多处理 {self.limits.tags_per_call} 个标签")
        if self.tags_total + len(cleaned) > self.limits.tags_total:
            raise HookLimitError(f"一次事件最多处理 {self.limits.tags_total} 个标签")
        if any(len(name) > self.limits.tag_length for name in cleaned):
            raise HookLimitError(f"标签名不能超过 {self.limits.tag_length} 个字")
        self.tag_calls += 1
        self.tags_total += len(cleaned)
        return cleaned


class ItemView(BaseModel):
    """条目的只读视图。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: int
    kind: str = ""
    data_type: str = ""
    title: str = ""
    original_title: str | None = None
    year: int | None = None
    tags: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    """各类别的标签，键是标签类别（genre、keyword……），值是展示顺序的标签名。"""
    fields: dict[str, Any] = Field(default_factory=dict)
    """字段键到字段值，不含标签、人物、图片和评分。"""
    locked_fields: tuple[str, ...] = ()
    """被锁定的字段键，写这些字段对应的标签会被宿主跳过。"""


class TagResult(BaseModel):
    """一次标签写入实际发生了什么。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    applied: tuple[str, ...] = ()
    """增：实际新增的标签；删：实际撤掉的标签。"""
    unchanged: tuple[str, ...] = ()
    """增：这个插件早就加过；删：本来就没有。"""
    locked: tuple[str, ...] = ()
    """字段被锁定，整批跳过。"""
    removed_by_user: tuple[str, ...] = ()
    """用户手动删除过，增被忽略。"""
    not_owned: tuple[str, ...] = ()
    """删：不是这个插件加的，不能删。"""


class HookHost(Protocol):
    """宿主给事件钩子的能力。"""

    @property
    def data_dir(self) -> Path:
        """插件自己的数据目录，文件只能写在这里面。目录在第一次使用时创建。"""
        ...

    def get_item(self, item_id: int) -> ItemView | None:
        """条目不存在时返回 None。"""
        ...

    def add_tags(self, item_id: int, tag_kind: str, names: list[str]) -> TagResult:
        """加标签，来源记为本插件。需要声明 `tags:<tag_kind>`。

        字段被锁定、用户手动删除过的标签不会写入，结果里分别列出。
        """
        ...

    def remove_tags(self, item_id: int, tag_kind: str, names: list[str]) -> TagResult:
        """撤标签，只能撤本插件加的。需要声明 `tags:<tag_kind>`。"""
        ...

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """联网走宿主的客户端。拒绝本机和私网地址（跳转之后也检查），响应有字节上限。"""
        ...

    def post_json(
        self,
        url: str,
        *,
        json: Any = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any: ...

    def log(self, message: str) -> None: ...
