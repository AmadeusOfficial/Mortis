"""写插件测试用的假宿主。不连网，不启动任何程序，只记下插件做了什么。

只在插件自己的测试里用（插件加载时会跳过 tests/ 目录和 test_*.py），插件代码里不要 import 它。

    host = FakeHost(responses={"https://api.example/search": {"results": []}})
    plugin.search(host, "标题")
    assert host.requests[0].url == "https://api.example/search"
"""

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mortis.plugin_api import NoSecrets, NoSettings, PlayerError
from mortis.plugin_api.hooks import (
    DEFAULT_LIMITS,
    HookBudget,
    HookLimits,
    ItemView,
    TagResult,
    require_tag_permission,
    unknown_permissions,
)

__all__ = ["FakeHookHost", "FakeHost", "FakePlayerHost", "FakeRequest"]

Response = Any | Callable[["FakeRequest"], Any]
"""按地址给的响应：直接是数据；是函数就用请求算；是异常对象就抛出。"""


@dataclass(frozen=True)
class FakeRequest:
    method: str
    url: str
    params: dict[str, Any] | None = None
    json: Any = None
    data: dict[str, str] | None = None
    headers: dict[str, str] | None = None


@dataclass
class FakeHost:
    """来源插件拿到的宿主。responses 的键是完整地址（不含查询参数），没登记的地址会让测试失败。"""

    settings: BaseModel = field(default_factory=NoSettings)
    secrets: BaseModel = field(default_factory=NoSecrets)
    responses: dict[str, Response] = field(default_factory=dict)
    token: str | None = None
    requests: list[FakeRequest] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def _answer(self, request: FakeRequest) -> Any:
        self.requests.append(request)
        if request.url not in self.responses:
            raise AssertionError(f"没登记这个请求的响应：{request.method} {request.url}")
        answer = self.responses[request.url]
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._answer(FakeRequest("GET", url, params=params, headers=headers))

    def post_json(
        self,
        url: str,
        *,
        json: Any = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._answer(FakeRequest("POST", url, json=json, data=data, headers=headers))

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        return self._answer(FakeRequest("GET", url, params=params, headers=headers))

    def access_token(self) -> str | None:
        return self.token

    def log(self, message: str) -> None:
        self.logs.append(message)


@dataclass
class FakePlayerHost:
    """播放插件拿到的宿主：记下要启动的程序、参数、工作目录。fail 里的程序启动时抛 PlayerError。"""

    settings: BaseModel = field(default_factory=NoSettings)
    fail: set[str] = field(default_factory=set)
    launched: list[tuple[str, list[str], str | None]] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def launch(self, executable: str, args: list[str], cwd: str | None = None) -> None:
        if executable in self.fail:
            raise PlayerError("拒绝访问。")
        self.launched.append((executable, list(args), cwd))

    def open_default(self, path: str) -> None:
        if path in self.fail:
            raise PlayerError("打不开这个文件。")
        self.opened.append(path)

    def log(self, message: str) -> None:
        self.logs.append(message)


@dataclass
class FakeHookHost:
    """事件钩子拿到的宿主，规则和真宿主一致：写权限、锁定字段、只能删自己加的标签、
    用户删除过的标签补不回来、每次事件的额度。一个实例对应一次事件。

    - `writes`：清单里声明的权限，比如 `("tags:keyword",)`，默认什么都不能写。
    - `items`：库里的条目视图，`add_tags` 和 `remove_tags` 会同步更新它们的 `tags`。
    - `locked_kinds`：字段被锁定的标签类别，对它们的写入整批跳过。
    - `removed_by_user`：用户手动删除过的 `(条目 id, 标签类别, 标签名)`。
    - `responses`：联网用的假响应，规则同 `FakeHost`。
    - `added`：本插件加过的标签 `{(条目 id, 标签类别): [名字]}`；`tag_calls` 记下每次调用。
    """

    settings: BaseModel = field(default_factory=NoSettings)
    writes: tuple[str, ...] = ()
    items: dict[int, ItemView] = field(default_factory=dict)
    locked_kinds: set[str] = field(default_factory=set)
    removed_by_user: set[tuple[int, str, str]] = field(default_factory=set)
    responses: dict[str, Response] = field(default_factory=dict)
    limits: HookLimits = DEFAULT_LIMITS
    data_dir: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="mortis-hook-")))
    requests: list[FakeRequest] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    added: dict[tuple[int, str], list[str]] = field(default_factory=dict)
    tag_calls: list[tuple[str, int, str, list[str]]] = field(default_factory=list)
    budget: HookBudget = field(init=False)

    def __post_init__(self) -> None:
        bad = unknown_permissions(self.writes)
        if bad:
            raise ValueError(
                f"writes 里的「{bad[0]}」写法不对：真宿主会因此拒绝加载这个插件，"
                "钩子一次也不会运行。要写成 tags:<标签类别>，比如 tags:keyword"
            )
        self.budget = HookBudget(self.limits)

    def log(self, message: str) -> None:
        self.logs.append(message)

    def get_item(self, item_id: int) -> ItemView | None:
        self.budget.spend_read()
        return self.items.get(item_id)

    def add_tags(self, item_id: int, tag_kind: str, names: list[str]) -> TagResult:
        require_tag_permission(self.writes, tag_kind)
        wanted = list(dict.fromkeys(name for name in self.budget.spend_tags(names) if name))
        self.tag_calls.append(("add", item_id, tag_kind, wanted))
        if tag_kind in self.locked_kinds:
            return TagResult(locked=tuple(wanted))
        mine = self.added.setdefault((item_id, tag_kind), [])
        applied: list[str] = []
        unchanged: list[str] = []
        removed: list[str] = []
        for name in wanted:
            if (item_id, tag_kind, name) in self.removed_by_user:
                removed.append(name)
            elif name in mine:
                unchanged.append(name)
            else:
                mine.append(name)
                applied.append(name)
        self._show(item_id, tag_kind, add=applied)
        return TagResult(
            applied=tuple(applied), unchanged=tuple(unchanged), removed_by_user=tuple(removed)
        )

    def remove_tags(self, item_id: int, tag_kind: str, names: list[str]) -> TagResult:
        require_tag_permission(self.writes, tag_kind)
        wanted = list(dict.fromkeys(name for name in self.budget.spend_tags(names) if name))
        self.tag_calls.append(("remove", item_id, tag_kind, wanted))
        if tag_kind in self.locked_kinds:
            return TagResult(locked=tuple(wanted))
        mine = self.added.get((item_id, tag_kind), [])
        applied = [name for name in wanted if name in mine]
        for name in applied:
            mine.remove(name)
        self._show(item_id, tag_kind, drop=applied)
        return TagResult(
            applied=tuple(applied), not_owned=tuple(n for n in wanted if n not in applied)
        )

    def _show(
        self, item_id: int, tag_kind: str, *, add: Sequence[str] = (), drop: Sequence[str] = ()
    ) -> None:
        view = self.items.get(item_id)
        if view is None:
            return
        shown = [name for name in view.tags.get(tag_kind, ()) if name not in drop]
        shown += [name for name in add if name not in shown]
        tags = {**view.tags, tag_kind: tuple(shown)}
        self.items[item_id] = view.model_copy(update={"tags": tags})

    def _answer(self, request: FakeRequest) -> Any:
        self.budget.spend_http()
        self.requests.append(request)
        if request.url not in self.responses:
            raise AssertionError(f"没登记这个请求的响应：{request.method} {request.url}")
        answer = self.responses[request.url]
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._answer(FakeRequest("GET", url, params=params, headers=headers))

    def post_json(
        self,
        url: str,
        *,
        json: Any = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._answer(FakeRequest("POST", url, json=json, data=data, headers=headers))
