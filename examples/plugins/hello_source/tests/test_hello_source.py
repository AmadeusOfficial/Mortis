"""示例来源的测试：假 Host 按地址返回事先写好的响应，并记下插件发出的请求。"""

import importlib.util
import sys
from pathlib import Path

import pytest

from mortis.plugin_api import SourceError
from mortis.plugin_api.testing import FakeHost

FOLDER = Path(__file__).resolve().parents[1]


def load_plugin():
    """把插件目录当成一个包加载（和宿主的做法一样，包内可以用相对导入）。"""
    name = "example_hello_source_under_test"
    spec = importlib.util.spec_from_file_location(
        name, FOLDER / "__init__.py", submodule_search_locations=[str(FOLDER)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_plugin()
PLUGIN = MODULE.PLUGIN
API = MODULE.API

SEARCH = {"results": [{"id": 7, "title": "你好电影", "original_title": "Hello", "year": 2001}]}
MOVIE = {"id": 7, "title": "你好电影", "tagline": "打个招呼", "overview": "一部很短的片子。"}


def test_search_returns_hits() -> None:
    host = FakeHost(responses={f"{API}/search": SEARCH})
    hits = PLUGIN.search(host, "你好")
    assert [(h.external_id, h.title, h.original_title, h.year) for h in hits] == [
        ("7", "你好电影", "Hello", 2001)
    ]
    assert host.requests[0].params == {"q": "你好"}


def test_fetch_and_candidates() -> None:
    host = FakeHost(responses={f"{API}/movies/7": MOVIE})
    raw = PLUGIN.fetch(host, "7")
    assert PLUGIN.candidates(raw) == {"tagline": "打个招呼", "overview": "一部很短的片子。"}


def test_candidates_skip_missing_values() -> None:
    assert PLUGIN.candidates({"title": "x", "tagline": ""}) == {}


def test_fetch_raises_source_error_when_not_found() -> None:
    host = FakeHost(responses={f"{API}/movies/9": {}})
    with pytest.raises(SourceError, match="没有这一条"):
        PLUGIN.fetch(host, "9")
