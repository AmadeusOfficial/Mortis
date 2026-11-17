"""VNDB 来源插件：搜索、详情候选值、BBCode 清理与过滤规则。"""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from mortis.plugin_api import SourceError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vndb"


def load_module(source_id):
    folder = ROOT / "plugins" / "galgame" / "sources"
    name = f"mortis_plugins_test_source.{source_id}"
    spec = importlib.util.spec_from_file_location(name, folder / f"{source_id}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VNDB_MODULE = load_module("vndb")
PLUGIN = VNDB_MODULE.PLUGIN


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeHost:
    """按 URL 和 filters 把录下来的响应交给插件，并记下请求。"""

    def __init__(self) -> None:
        self.settings = None
        self.secrets = None
        self.requests: list[dict[str, Any]] = []
        self.logs: list[str] = []

    def post_json(self, url, *, json=None, data=None, headers=None):  # noqa: A002
        self.requests.append({"url": url, "json": json})
        body = json or {}
        if url.endswith("/vn"):
            filters = body.get("filters") or []
            if filters and filters[0] == "search":
                return fixture("search.json")
            return fixture("vn.json")
        if url.endswith("/release"):
            return fixture("release.json")
        raise AssertionError(f"没录过这个请求：{url}")

    def get_json(self, url, *, params=None, headers=None):
        raise AssertionError(f"没录过这个请求：{url}")

    def get_text(self, url, *, params=None, headers=None):
        raise AssertionError(f"没录过这个请求：{url}")

    def access_token(self):
        return None

    def log(self, message):
        self.logs.append(message)


def test_search_returns_hits_with_chinese_title():
    host = FakeHost()
    hits = PLUGIN.search(host, "Dies irae")

    assert host.requests[0]["url"] == "https://api.vndb.org/kana/vn"
    assert host.requests[0]["json"]["filters"] == ["search", "=", "Dies irae"]
    assert host.requests[0]["json"]["results"] == 10
    assert hits[0].external_id == "v548"
    assert hits[0].title == "Dies irae ~Acta est Fabula~"
    assert hits[0].year == 2009
    assert "ディエス・イレ" in hits[0].aliases

    chinese = next(hit for hit in hits if hit.external_id == "v5844")
    assert chinese.title == "神咒神威神乐 曙光"
    assert chinese.original_title == "神咒神威神楽"
    assert "神咒神威神楽" in chinese.aliases


def test_fetch_asks_for_engine_separately():
    host = FakeHost()
    raw = PLUGIN.fetch(host, "v548")

    assert raw["vn"]["id"] == "v548"
    assert raw["engine"] == "Malie"
    assert host.requests[1]["url"] == "https://api.vndb.org/kana/release"
    assert host.requests[1]["json"]["filters"] == ["vn", "=", ["id", "=", "v548"]]
    assert "extlinks.url" in host.requests[1]["json"]["fields"]


def test_fetch_missing_entry_raises_source_error():
    class EmptyHost(FakeHost):
        def post_json(self, url, *, json=None, data=None, headers=None):  # noqa: A002
            return {"results": []}

    with pytest.raises(SourceError) as caught:
        PLUGIN.fetch(EmptyHost(), "v99999999")
    assert caught.value.message == "VNDB 上没有 v99999999"


def test_candidates_fill_all_keys():
    raw = PLUGIN.fetch(FakeHost(), "v548")
    values = PLUGIN.candidates(raw)

    assert values["title"] == "Dies irae ~Acta est Fabula~"
    assert values["original_title"] == "Dies irae ~Acta est Fabula~"
    assert values["release_date"] == "2009-07-24"
    assert values["poster"] == [
        {"url": "https://t.vndb.org/cv/16/79716.jpg", "width": 411, "height": 560}
    ]
    assert values["brands"] == ["light", "Greenwood Co., Ltd."]
    assert values["engine"] == "Malie"
    assert values["play_time"] == 82.5
    assert values["ratings"] == [
        {"source": "vndb", "rating": 81.3, "max_rating": 100, "vote_count": 3975}
    ]
    assert values["website"] == "http://www.light.gr.jp/light/products/dies/index.html"

    scenario = values["scenario"][0]
    assert scenario == {
        "external_id": "s207",
        "name": "Masada Takashi",
        "original_name": "正田 崇",
        "character": None,
        "photo_url": None,
    }
    artist_ids = [person["external_id"] for person in values["artists"]]
    assert artist_ids[0] == "s717"
    assert len(artist_ids) == len(set(artist_ids))


def test_candidates_merge_multiple_characters_per_voice_actor():
    values = PLUGIN.candidates(PLUGIN.fetch(FakeHost(), "v548"))
    voice = {person["external_id"]: person for person in values["voice_actors"]}

    assert voice["s28"]["name"] == "Kawashima Rino"
    assert voice["s28"]["original_name"] == "かわしま りの"
    assert (
        voice["s28"]["character"] == "櫻井 螢 / ベアトリス・ヴァルトルート・フォン・キルヒアイゼン"
    )
    assert len(values["voice_actors"]) == 29


def test_candidates_drop_spoiler_tags_and_keep_top_twenty():
    vn = fixture("vn.json")["results"][0]
    raw = {"vn": vn, "engine": None}
    values = PLUGIN.candidates(raw)

    assert len(values["tags"]) == 20
    spoiler_names = {tag["name"] for tag in vn["tags"] if tag["spoiler"] != 0}
    assert not spoiler_names & set(values["tags"])
    assert values["tags"][0] == "Tomboy Heroine"


def test_candidates_drop_adult_screenshots():
    values = PLUGIN.candidates({"vn": fixture("vn.json")["results"][0], "engine": None})

    urls = [image["url"] for image in values["fanart"]]
    assert "https://t.vndb.org/sf/32/6232.jpg" not in urls
    assert urls[0] == "https://t.vndb.org/sf/80/14080.jpg"
    assert values["fanart"][0]["width"] == 800
    assert values["fanart"][0]["height"] == 600
    assert len(urls) == 9


def test_candidates_skip_incomplete_dates_and_empty_values():
    vn = {
        "title": "测试",
        "released": "TBA",
        "rating": None,
        "length_minutes": None,
        "developers": [],
        "tags": [],
        "va": [],
        "staff": [],
    }
    values = PLUGIN.candidates({"vn": vn})

    assert values == {"title": "测试"}


def test_candidates_take_website_from_extlinks():
    vn = {
        "title": "测试",
        "extlinks": [
            {"label": "Wikipedia (ja)", "url": "https://ja.wikipedia.org/wiki/x"},
            {"label": "Official website", "url": "https://example.com/game"},
        ],
    }
    assert PLUGIN.candidates({"vn": vn})["website"] == "https://example.com/game"


def test_candidates_use_main_title_when_no_chinese_title():
    vn = {
        "title": "Dies irae ~Acta est Fabula~",
        "titles": [
            {"lang": "ja", "title": "Dies irae ~Acta est Fabula~", "main": True},
            {"lang": "en", "title": "Dies irae Acta est Fabula"},
        ],
    }
    values = PLUGIN.candidates({"vn": vn})

    assert values["title"] == "Dies irae ~Acta est Fabula~"
    assert values["original_title"] == "Dies irae ~Acta est Fabula~"


def test_clean_bbcode_keeps_link_text_and_drops_spoilers():
    module = VNDB_MODULE
    text = (
        "开头\n\n"
        "[url=https://example.com/x]官网[/url]\n"
        "[b]粗体[/b]和[i]斜体[/i]与[u]下划线[/u]与[s]删除线[/s]\n"
        "[spoiler]这里剧透[/spoiler]\n"
        "结尾"
    )

    result = module.clean_bbcode(text)

    assert "官网" in result
    assert "https://example.com/x" not in result
    assert "[" not in result and "]" not in result
    assert "这里剧透" not in result
    assert "粗体" in result


def test_clean_bbcode_squeezes_blank_lines_and_strips():
    module = VNDB_MODULE

    assert module.clean_bbcode("\n\n第一段\n\n\n\n第二段\n\n") == "第一段\n\n第二段"
    assert module.clean_bbcode("") == ""
    assert module.clean_bbcode(None) == ""


def test_play_time_converts_minutes_to_hours():
    module = VNDB_MODULE

    assert module._play_time(4950) == 82.5
    assert module._play_time(90) == 1.5
    assert module._play_time(0) is None
    assert module._play_time(None) is None
