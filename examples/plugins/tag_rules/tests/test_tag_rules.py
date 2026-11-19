"""示例组合插件的测试：钩子用 FakeHookHost，来源用 FakeHost。

FakeHookHost 的规则和真宿主一致：写权限、锁定字段、只能撤自己加的标签、
用户删除过的标签补不回来、每次事件的额度。一个实例对应一次事件，下一次事件要新建一个。
传 `hook=` 后，`settings` 可以给 dict，按钩子的 `Settings` 校验；不给就是模型默认值。
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from mortis.plugin_api import (
    HookPermissionError,
    ItemView,
    ScanFinished,
    ScrapeFinished,
    SourceError,
)
from mortis.plugin_api.testing import FakeHookHost, FakeHost

FOLDER = Path(__file__).resolve().parents[1]


def load_plugin():
    """把插件目录当成一个包加载（和宿主的做法一样，包内可以用相对导入）。"""
    name = "example_tag_rules_under_test"
    spec = importlib.util.spec_from_file_location(
        name, FOLDER / "__init__.py", submodule_search_locations=[str(FOLDER)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_plugin()
(HOOK,) = MODULE.PLUGIN.hooks
(SOURCE,) = MODULE.PLUGIN.sources
API = sys.modules[f"{MODULE.__name__}.source"].API

WRITES = ("tags:keyword",)
EVENT = ScrapeFinished(data_type="movie", item_id=1)
ALIEN = {"rules": [{"pattern": "^(异形|alien)", "tag": "异形系列"}]}


def movie(
    runtime: int | None, year: int | None = 2005, title: str = "片", **tags: tuple[str, ...]
) -> ItemView:
    fields = {} if runtime is None else {"runtime": runtime}
    return ItemView(
        id=1, kind="work", data_type="movie", title=title, year=year, fields=fields, tags=tags
    )


def run(item: ItemView, **options) -> FakeHookHost:
    host = FakeHookHost(writes=WRITES, items={1: item}, hook=HOOK, **options)
    HOOK.on_event(host, EVENT)
    return host


def test_default_settings_tag_by_runtime_and_year() -> None:
    host = run(movie(180, year=1975))
    assert host.get_item(1).tags["keyword"] == ("长片", "老电影")
    assert host.added[(1, "keyword")] == ["长片", "老电影"]
    assert run(movie(80)).get_item(1).tags["keyword"] == ("短片",)


def test_no_matching_rule_makes_no_calls() -> None:
    host = run(movie(120))
    assert host.tag_calls == []


def test_thresholds_and_tag_names_come_from_settings() -> None:
    host = run(movie(130), settings={"long_minutes": 120, "long_tag": "大长片"})
    assert host.get_item(1).tags["keyword"] == ("大长片",)


def test_title_rule_adds_its_tag() -> None:
    host = run(movie(120, title="Alien: Covenant"), settings=ALIEN)
    assert host.get_item(1).tags["keyword"] == ("异形系列",)
    original = movie(120, title="契约").model_copy(update={"original_title": "Alien: Covenant"})
    assert run(original, settings=ALIEN).get_item(1).tags["keyword"] == ("异形系列",)
    assert run(movie(120, title="铁血战士"), settings=ALIEN).tag_calls == []


def test_disabled_rule_adds_nothing_and_retracts_its_old_tag() -> None:
    off = {"rules": [{**ALIEN["rules"][0], "enabled": False}]}
    assert run(movie(120, title="Alien"), settings=off).tag_calls == []
    first = run(movie(120, title="Alien"), settings=ALIEN)
    second = FakeHookHost(
        writes=WRITES, items=dict(first.items), added=dict(first.added), hook=HOOK, settings=off
    )
    HOOK.on_event(second, EVENT)
    assert second.get_item(1).tags["keyword"] == ()


@pytest.mark.parametrize(
    "rule",
    [{"pattern": "(未闭合", "tag": "坏"}, {"pattern": "ok", "tag": ""}],
)
def test_settings_reject_bad_rules(rule: dict) -> None:
    with pytest.raises(ValidationError) as caught:
        HOOK.Settings.model_validate({"rules": [rule]})
    if rule["tag"]:
        assert "不是合法的正则表达式" in str(caught.value)


def test_is_idempotent_when_the_event_arrives_twice() -> None:
    first = run(movie(180))
    second = FakeHookHost(
        writes=WRITES, items=dict(first.items), added=dict(first.added), hook=HOOK
    )
    HOOK.on_event(second, EVENT)
    assert second.get_item(1).tags["keyword"] == ("长片",)
    assert second.added[(1, "keyword")] == ["长片"]  # 没有重复写入


def test_retracts_own_tag_when_the_rule_stops_matching() -> None:
    first = run(movie(180))
    shown = first.get_item(1).model_copy(update={"fields": {"runtime": 80}})
    second = FakeHookHost(writes=WRITES, items={1: shown}, added=dict(first.added), hook=HOOK)
    HOOK.on_event(second, EVENT)
    assert second.get_item(1).tags["keyword"] == ("短片",)


def test_never_touches_tags_it_does_not_own() -> None:
    # 「长片」是刮削或用户加的，不在本插件名下；片长改了之后规则不再适用，但宿主不让撤。
    host = run(movie(80, keyword=("长片", "剧情")))
    assert "长片" in host.get_item(1).tags["keyword"]
    assert "剧情" in host.get_item(1).tags["keyword"]
    assert ("remove", 1, "keyword", ["长片"]) in host.tag_calls


def test_skips_locked_field_and_user_removed_tags_without_error() -> None:
    locked = run(movie(180), locked_kinds={"keyword"})
    assert locked.get_item(1).tags == {}
    removed = run(movie(180), removed_by_user={(1, "keyword", "长片")})
    assert removed.get_item(1).tags.get("keyword", ()) == ()


def test_needs_the_declared_write_permission() -> None:
    host = FakeHookHost(items={1: movie(180)}, hook=HOOK)  # 没声明 tags:keyword
    with pytest.raises(HookPermissionError):
        HOOK.on_event(host, EVENT)


def test_ignores_missing_items_and_other_events() -> None:
    missing = FakeHookHost(writes=WRITES, hook=HOOK)
    HOOK.on_event(missing, EVENT)
    other = FakeHookHost(writes=WRITES, items={1: movie(180)}, hook=HOOK)
    HOOK.on_event(other, ScanFinished(data_type="movie", library_id=1))
    assert missing.tag_calls == [] and other.tag_calls == []


def test_source_fills_runtime() -> None:
    host = FakeHost(
        responses={
            f"{API}/search": {"films": [{"id": 3, "title": "片", "year": 2005}]},
            f"{API}/3": {"id": 3, "title": "片", "minutes": 152},
            f"{API}/4": {"id": 4},
        }
    )
    assert [h.external_id for h in SOURCE.search(host, "片")] == ["3"]
    assert SOURCE.candidates(SOURCE.fetch(host, "3")) == {"runtime": 152}
    assert SOURCE.candidates({"minutes": 0}) == {}
    with pytest.raises(SourceError):
        SOURCE.fetch(host, "4")
