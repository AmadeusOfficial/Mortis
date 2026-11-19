"""事件模型、注册表的钩子校验、假 HookHost、额度和联网防护。不需要起应用。"""

import textwrap
from pathlib import Path

import httpx2 as httpx
import pytest
from pydantic import ValidationError

from mortis.hooks import netguard
from mortis.plugin_api import (
    EventType,
    HookLimitError,
    HookPermissionError,
    ItemView,
    ScrapeFinished,
)
from mortis.plugin_api.events import event_from_dict, event_model
from mortis.plugin_api.hooks import HookBudget, HookError, HookLimits
from mortis.plugin_api.testing import FakeHookHost, FakeRequest
from mortis.plugins.registry import PluginRegistry

HEADER = """
from mortis.plugin_api import HookPlugin, Plugin


class Hook(HookPlugin):
    id = "me.hook"
    name = "钩子"

    def on_event(self, host, event):
        pass


PLUGIN = Plugin(hooks=(Hook(),))
"""


def _load(tmp_path: Path, hooks_toml: str) -> PluginRegistry:
    folder = tmp_path / "pack"
    folder.mkdir()
    (folder / "plugin.toml").write_text(
        textwrap.dedent(
            """
            id = "me.pack"
            name = "包"
            version = "1"

            [mortis]
            api_version = 1
            """
        )
        + hooks_toml,
        encoding="utf-8",
    )
    (folder / "__init__.py").write_text(HEADER, encoding="utf-8")
    registry = PluginRegistry([(tmp_path, "user")])
    registry.load()
    return registry


def test_event_list_is_fixed() -> None:
    assert {event.value for event in EventType} == {
        "item.created",
        "scan.finished",
        "scrape.started",
        "scrape.finished",
        "scrape.failed",
        "arrange.finished",
        "play.started",
        "task.failed",
    }
    for event in EventType:
        model = event_model(event.value)
        assert model is not None
        assert model().type == event  # 每个字段都有默认值


def test_events_are_frozen_and_ignore_unknown_fields() -> None:
    event = event_from_dict(
        {"type": "scrape.finished", "item_id": 3, "data_type": "movie", "added_later": "x"}
    )
    assert isinstance(event, ScrapeFinished)
    assert (event.item_id, event.data_type, event.type == "scrape.finished") == (3, "movie", True)
    with pytest.raises(ValidationError):
        event.item_id = 4  # type: ignore[misc]
    assert not hasattr(event, "added_later")
    with pytest.raises(ValidationError):
        event_from_dict({"type": "no.such.event"})
    assert event_model("no.such.event") is None


def test_unknown_event_is_rejected_at_load(tmp_path: Path) -> None:
    registry = _load(tmp_path, '[[hooks]]\nid = "me.hook"\nevent = "scrape.done"\n')
    info = registry.get("me.pack")
    assert info is not None and info.error is not None
    assert "scrape.done" in info.error and "不存在的事件" in info.error
    assert registry.hooks() == []


@pytest.mark.parametrize("permission", ["items:write", "tags:", "tags:Genre", "sql"])
def test_unknown_write_permission_is_rejected_at_load(tmp_path: Path, permission: str) -> None:
    registry = _load(
        tmp_path,
        f'[[hooks]]\nid = "me.hook"\nevent = "scrape.finished"\nwrites = ["{permission}"]\n',
    )
    info = registry.get("me.pack")
    assert info is not None and info.error is not None and permission in info.error


def test_subscribers_filter_by_event_and_data_type(tmp_path: Path) -> None:
    registry = _load(
        tmp_path,
        '[[hooks]]\nid = "me.hook"\nevent = "scrape.finished"\ndata_types = ["movie"]\n'
        'writes = ["tags:keyword"]\n',
    )
    assert [s.hook.id for s in registry.subscribers("scrape.finished", "movie")] == ["me.hook"]
    assert registry.subscribers("scrape.finished", "tv") == []
    assert registry.subscribers("scrape.finished", "") == []  # 写了名单就不投递没有类型的事件
    assert registry.subscribers("scrape.failed", "movie") == []
    info = registry.get("me.pack")
    assert info is not None
    assert info.subscriptions == ("scrape.finished",)
    assert info.hook_writes == ("tags:keyword",)


def test_hook_disabled_individually_and_fuse_state(tmp_path: Path) -> None:
    registry = _load(tmp_path, '[[hooks]]\nid = "me.hook"\nevent = "scrape.finished"\n')
    assert registry.hook_status("me.hook").disabled is False
    registry.load(["me.hook"])
    status = registry.hook_status("me.hook")
    assert status.disabled and status.reason
    assert registry.hooks() == [] and registry.subscribers("scrape.finished", "movie") == []
    package = registry.get("me.pack")
    assert package is not None and package.enabled and package.error is None


def test_fuse_trips_after_limit_and_resets_on_reload(tmp_path: Path) -> None:
    registry = _load(tmp_path, '[[hooks]]\nid = "me.hook"\nevent = "scrape.finished"\n')
    assert [registry.health.failed("me.hook", "x") for _ in range(3)] == [False, False, True]
    assert registry.hook_disabled("me.hook")
    assert "连续失败 3 次" in (registry.hook_status("me.hook").reason or "")
    assert registry.health.failed("me.hook", "again") is False  # 只熔断一次
    registry.load([])  # 用户把它从停用名单里去掉并重新加载 = 重新启用
    assert not registry.hook_disabled("me.hook")
    assert registry.hook_status("me.hook").failures == 0


def test_success_resets_consecutive_failures(tmp_path: Path) -> None:
    registry = _load(tmp_path, '[[hooks]]\nid = "me.hook"\nevent = "scrape.finished"\n')
    registry.health.failed("me.hook", "x")
    registry.health.failed("me.hook", "x")
    registry.health.succeeded("me.hook")
    assert registry.health.failed("me.hook", "x") is False
    assert not registry.hook_disabled("me.hook")


# ---- FakeHookHost：规则和真宿主一致 ----


def _view(**tags: tuple[str, ...]) -> ItemView:
    return ItemView(id=1, title="黑客帝国", year=1999, tags=tags)


def test_fake_host_requires_declared_write_permission() -> None:
    host = FakeHookHost(items={1: _view()}, writes=("tags:keyword",))
    assert host.add_tags(1, "keyword", ["a"]).applied == ("a",)
    with pytest.raises(HookPermissionError, match="tags:genre"):
        host.add_tags(1, "genre", ["a"])
    with pytest.raises(HookPermissionError):
        host.remove_tags(1, "genre", ["a"])
    with pytest.raises(HookPermissionError):
        FakeHookHost().add_tags(1, "keyword", ["a"])  # 没声明任何权限
    with pytest.raises(HookPermissionError):
        host.add_tags(1, "keyword/../x", ["a"])


def test_fake_host_tag_rules() -> None:
    host = FakeHookHost(
        items={1: _view(keyword=("原有",))},
        writes=("tags:keyword", "tags:genre"),
        locked_kinds={"genre"},
        removed_by_user={(1, "keyword", "被删过")},
    )
    first = host.add_tags(1, "keyword", ["新", "新", "被删过"])
    assert (first.applied, first.removed_by_user) == (("新",), ("被删过",))
    assert host.add_tags(1, "keyword", ["新"]).unchanged == ("新",)
    assert host.add_tags(1, "genre", ["动作"]).locked == ("动作",)
    removal = host.remove_tags(1, "keyword", ["新", "原有"])
    assert (removal.applied, removal.not_owned) == (("新",), ("原有",))
    view = host.get_item(1)
    assert view is not None and view.tags["keyword"] == ("原有",)
    assert host.get_item(9) is None


def test_fake_host_limits_and_requests() -> None:
    host = FakeHookHost(
        writes=("tags:keyword",),
        limits=HookLimits(http_calls=2, tag_calls=1, tags_per_call=2, tag_length=3),
        responses={"https://api.test/a": {"ok": True}},
    )
    assert host.get_json("https://api.test/a", params={"q": 1}) == {"ok": True}
    assert host.requests == [FakeRequest("GET", "https://api.test/a", params={"q": 1})]
    host.post_json("https://api.test/a", json={})
    with pytest.raises(HookLimitError, match="联网"):
        host.get_json("https://api.test/a")
    with pytest.raises(HookLimitError, match="3 个字"):
        host.add_tags(1, "keyword", ["太长太长了"])
    host.add_tags(1, "keyword", ["a"])
    with pytest.raises(HookLimitError, match="标签接口"):
        host.add_tags(1, "keyword", ["b"])


def test_budget_tag_limits() -> None:
    budget = HookBudget(HookLimits(tags_per_call=2, tags_total=3, tag_length=4))
    assert budget.spend_tags([" a ", "b"]) == ["a", "b"]
    with pytest.raises(HookLimitError, match="一次最多处理 2"):
        budget.spend_tags(["a", "b", "c"])
    with pytest.raises(HookLimitError, match="一次事件最多处理 3"):
        budget.spend_tags(["x", "y"])
    with pytest.raises(HookLimitError, match="4 个字"):
        budget.spend_tags(["超过四个字的标签"])


# ---- 联网防护 ----


def _public(host: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost:8000/x",
        "http://foo.localhost/x",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://172.16.0.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://100.64.0.1/x",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "http://[fd00::1]/x",
        "http://2130706433/x",  # 十进制写法的 127.0.0.1，靠解析结果拦
        "http://198.18.0.1/x",
        "http://0.0.0.0/x",
        "http://nas.local/x",
        "http://user:pw@example.org/x",
        "file:///etc/passwd",
        "ftp://example.org/x",
        "http:///x",
    ],
)
def test_check_url_rejects_local_and_private(url: str) -> None:
    def resolver(host: str) -> list[str]:
        return ["127.0.0.1"] if host.isdigit() else _public(host)

    with pytest.raises(HookLimitError):
        netguard.check_url(url, resolver)


def test_check_url_rejects_host_resolving_to_private_address() -> None:
    with pytest.raises(HookLimitError):
        netguard.check_url("https://evil.example.org/x", lambda host: ["93.184.216.34", "10.0.0.1"])
    with pytest.raises(HookError):
        netguard.check_url("https://nowhere.example.org/x", lambda host: [])


def test_check_url_allows_public_and_fake_ip_for_hostnames() -> None:
    netguard.check_url("https://api.example.org/x", _public)
    netguard.check_url("http://93.184.216.34:8080/x", _public)
    netguard.check_url("https://clash.example.org/x", lambda host: ["198.18.0.7"])


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_fetch_json_checks_every_redirect_hop() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "api.example.org":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/secret"})
        return httpx.Response(200, json={"secret": 1})

    with pytest.raises(HookLimitError):
        netguard.fetch_json(
            _client(httpx.MockTransport(handle)),
            "GET",
            "https://api.example.org/start",
            HookLimits(),
            resolver=_public,
        )
    assert seen == ["https://api.example.org/start"]  # 私网地址没有被请求


def test_fetch_json_follows_safe_redirects_and_limits_hops() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"})
        if request.url.path == "/go":
            return httpx.Response(301, headers={"location": "https://cdn.example.org/data"})
        return httpx.Response(200, json={"ok": request.url.host})

    client = _client(httpx.MockTransport(handle))
    got = netguard.fetch_json(
        client, "GET", "https://api.example.org/go", HookLimits(), resolver=_public
    )
    assert got == {"ok": "cdn.example.org"}
    with pytest.raises(HookLimitError, match="跳转"):
        netguard.fetch_json(
            client, "GET", "https://api.example.org/loop", HookLimits(), resolver=_public
        )


def test_fetch_json_limits_response_size_and_reports_errors() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/big":
            return httpx.Response(200, content=b'{"x": "' + b"a" * 5000 + b'"}')
        if request.url.path == "/text":
            return httpx.Response(200, content=b"<html>")
        return httpx.Response(500)

    client = _client(httpx.MockTransport(handle))
    small = HookLimits(response_bytes=1000)
    with pytest.raises(HookLimitError, match="1000"):
        netguard.fetch_json(client, "GET", "https://a.example.org/big", small, resolver=_public)
    with pytest.raises(HookError, match="不是 JSON"):
        netguard.fetch_json(client, "GET", "https://a.example.org/text", small, resolver=_public)
    with pytest.raises(HookError, match="500"):
        netguard.fetch_json(client, "GET", "https://a.example.org/err", small, resolver=_public)


def test_fetch_json_drops_credentials_across_hosts_and_post_becomes_get() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "a.example.org":
            return httpx.Response(302, headers={"location": "https://b.example.org/next"})
        return httpx.Response(200, json={})

    netguard.fetch_json(
        _client(httpx.MockTransport(handle)),
        "POST",
        "https://a.example.org/start",
        HookLimits(),
        resolver=_public,
        json_body={"k": 1},
        headers={"Authorization": "Bearer t", "X-Other": "1"},
    )
    assert requests[0].headers["authorization"] == "Bearer t"
    assert requests[1].method == "GET"
    assert "authorization" not in requests[1].headers and requests[1].headers["x-other"] == "1"
