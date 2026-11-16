import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "cngal"


def load(plugin_id: str) -> Any:
    folder = ROOT / "plugins" / plugin_id
    name = f"mortis_plugins_test.{plugin_id}"
    spec = importlib.util.spec_from_file_location(
        name, folder / "__init__.py", submodule_search_locations=[str(folder)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.PLUGIN


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeHost:
    """按 URL 回固定响应，记下每次请求的地址和参数。"""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.settings = None
        self.secrets = None
        self.requests: list[tuple[str, dict[str, Any] | None]] = []
        self.messages: list[str] = []

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        self.requests.append((url, params))
        if url not in self.routes:
            raise AssertionError(f"没准备这个地址的响应：{url}")
        return self.routes[url]

    def post_json(self, url: str, **kwargs: Any) -> Any:
        raise AssertionError("CnGal 不需要 POST")

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        raise AssertionError("CnGal 不需要纯文本")

    def access_token(self) -> str | None:
        return None

    def log(self, message: str) -> None:
        self.messages.append(message)


def host_for_search() -> FakeHost:
    return FakeHost({"https://api.cngal.org/api/home/Search": fixture("search.json")})


def test_search_returns_hits() -> None:
    plugin = load("cngal")
    host = host_for_search()

    hits = plugin.search(host, "三色绘恋")

    assert host.requests == [
        ("https://api.cngal.org/api/home/Search", {"Types": "Game", "Text": "三色绘恋"})
    ]
    assert hits, "搜索应该有结果"
    first = hits[0]
    assert first.external_id == "80"
    assert first.title == "三色△绘恋"
    assert first.year == 2017
    assert first.overview
    assert first.poster_url


def test_search_without_results_returns_empty() -> None:
    plugin = load("cngal")
    empty = {"pagedResultDto": {"totalCount": 0, "data": [], "maxResultCount": 10}}
    host = FakeHost({"https://api.cngal.org/api/home/Search": empty})

    assert plugin.search(host, "没有这一款") == []


def test_fetch_uses_entry_url() -> None:
    plugin = load("cngal")
    host = FakeHost({"https://api.cngal.org/api/entries/GetEntryView/80": fixture("entry_80.json")})

    raw = plugin.fetch(host, "80")

    assert host.requests == [("https://api.cngal.org/api/entries/GetEntryView/80", None)]
    assert raw["id"] == 80


def test_candidates_cover_fields() -> None:
    plugin = load("cngal")
    values = plugin.candidates(fixture("entry_80.json"))

    assert values["title"] == "三色△绘恋"
    assert values["release_date"] == "2017-09-21"
    assert values["poster"][0]["url"].startswith("https://")
    assert values["brands"] == ["绘恋制作组"]
    assert values["overview"].startswith("2005年，江城的暖秋。")
    assert "<p>" not in values["overview"]

    voices = {person["name"]: person["character"] for person in values["voice_actors"]}
    assert voices["朱雀橙"] == "文芷"
    assert voices["小N"] == "墨小菊"
    assert values["voice_actors"][0]["external_id"] == "284"

    assert [person["name"] for person in values["scenario"]] == ["姐夫大黑手"]
    assert "Kazuzu" in [person["name"] for person in values["artists"]]
