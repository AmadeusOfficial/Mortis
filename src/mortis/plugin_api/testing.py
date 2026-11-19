"""写插件测试用的假宿主。不连网，不启动任何程序，只记下插件做了什么。

只在插件自己的测试里用（插件加载时会跳过 tests/ 目录和 test_*.py），插件代码里不要 import 它。

    host = FakeHost(responses={"https://api.example/search": {"results": []}})
    plugin.search(host, "标题")
    assert host.requests[0].url == "https://api.example/search"
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from mortis.plugin_api import NoSecrets, NoSettings, PlayerError

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
    """事件钩子拿到的宿主的占位，由事件钩子的运行时（HookHost）实现后补全。

    现在只有设置和日志；补全时在这里加上只读的条目视图、标签增删，并记下调用，
    规则（权限、锁定字段、只能删自己加的标签）和真宿主保持一致。
    """

    settings: BaseModel = field(default_factory=NoSettings)
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.logs.append(message)
