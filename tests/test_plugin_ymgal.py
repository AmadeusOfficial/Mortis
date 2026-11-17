import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from mortis.plugin_api import SourceAuthError, SourceError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "ymgal"

TOKEN_URL = "https://www.ymgal.games/oauth/token"
SEARCH_URL = "https://www.ymgal.games/open/archive/search-game"
ARCHIVE_URL = "https://www.ymgal.games/open/archive"

KEYWORD = "Dies irae ～Amantes amentes～"
GID = "10957"
ORG_ID = "10256"


def load_module(source_id):
    folder = ROOT / "plugins" / "galgame" / "sources"
    name = f"mortis_plugins_test_source.{source_id}"
    spec = importlib.util.spec_from_file_location(name, folder / f"{source_id}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def key(url: str, params: dict[str, Any] | None) -> tuple[str, tuple[tuple[str, Any], ...]]:
    return url, tuple(sorted((params or {}).items()))


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


class FakeHost:
    """按「地址 + 参数」回放录好的接口数据，令牌地址返回假令牌。"""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.secrets = type("Secrets", (), {})()
        self.calls: list[dict[str, Any]] = []
        self.logs: list[str] = []
        self.routes: dict[tuple[str, tuple[tuple[str, Any], ...]], Any] = {}
        self.auth_failures = 0
        self.token_issues = 0

    def route(self, url: str, params: dict[str, Any] | None, body: Any) -> None:
        self.routes[key(url, params)] = body

    def access_token(self) -> str | None:
        return None

    def log(self, message: str) -> None:
        self.logs.append(message)

    def token_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["url"] == TOKEN_URL]

    def api_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["url"] != TOKEN_URL]

    def get_json(self, url, *, params=None, headers=None):
        self.calls.append({"method": "GET", "url": url, "params": params, "headers": headers})
        if self.auth_failures > 0:
            self.auth_failures -= 1
            raise SourceAuthError("令牌失效")
        return self.routes[key(url, params)]

    def post_json(self, url, *, json=None, data=None, headers=None):
        self.calls.append(
            {"method": "POST", "url": url, "json": json, "data": data, "headers": headers}
        )
        if url != TOKEN_URL:
            raise AssertionError(f"这个插件只该向令牌地址发 POST，不该请求 {url}")
        self.token_issues += 1
        return {
            "access_token": f"token-{self.token_issues}",
            "token_type": "bearer",
            "expires_in": 3600,
            "scope": "public",
        }

    def get_text(self, url, *, params=None, headers=None):
        raise AssertionError("这个插件不该用 get_text")


@pytest.fixture
def module():
    return load_module("ymgal")


@pytest.fixture
def plugin(module):
    return module.PLUGIN


@pytest.fixture
def clock(module, monkeypatch):
    made = FakeClock()
    monkeypatch.setattr(module, "time", made)
    return made


@pytest.fixture
def host(plugin):
    made = FakeHost(plugin.Settings())
    made.route(
        SEARCH_URL,
        {
            "mode": "list",
            "keyword": KEYWORD,
            "pageNum": 1,
            "pageSize": 10,
        },
        fixture("search.json"),
    )
    made.route(ARCHIVE_URL, {"gid": GID}, fixture("archive.json"))
    made.route(ARCHIVE_URL, {"orgId": ORG_ID}, fixture("org.json"))
    return made


def test_插件元数据(plugin):
    assert type(plugin).__name__ == "YmgalSource"
    assert plugin.id == "ymgal"
    assert plugin.name == "月幕 Galgame"
    assert plugin.data_type == "galgame"
    assert plugin.provider == "ymgal"
    assert plugin.requests_per_second == 1


def test_设置默认是公用客户端且启用(plugin, host):
    settings = plugin.Settings()
    assert settings.client_id == "ymgal"
    assert settings.client_secret == "luna0327"
    assert plugin.enabled(host) is True
    assert plugin.disabled_reason(host) is None


def test_没填凭据时禁用(plugin):
    host = FakeHost(plugin.Settings(client_id="", client_secret=""))
    assert plugin.enabled(host) is False
    assert "申请" in plugin.disabled_reason(host)


def test_设置忽略多余键(plugin):
    settings = plugin.Settings(client_id="a", client_secret="b", 多余=1)
    assert (settings.client_id, settings.client_secret) == ("a", "b")


def test_两次搜索只取一次令牌(plugin, host):
    plugin.search(host, KEYWORD)
    plugin.search(host, KEYWORD)

    assert len(host.token_calls()) == 1
    token_call = host.token_calls()[0]
    assert token_call["method"] == "POST"
    assert token_call["data"] == {
        "grant_type": "client_credentials",
        "client_id": "ymgal",
        "client_secret": "luna0327",
        "scope": "public",
    }
    assert host.token_issues == 1


def test_令牌过期后重取(plugin, host, clock):
    plugin.search(host, KEYWORD)
    clock.now += 3600 - 30
    plugin.search(host, KEYWORD)

    assert host.token_issues == 2
    assert host.api_calls()[-1]["headers"]["Authorization"] == "Bearer token-2"


def test_令牌未过期时提前六十秒重取(plugin, host, clock):
    plugin.search(host, KEYWORD)
    clock.now += 3600 - 61
    plugin.search(host, KEYWORD)
    assert host.token_issues == 1

    clock.now += 2
    plugin.search(host, KEYWORD)
    assert host.token_issues == 2


def test_遇到认证失败重取令牌后成功(plugin, host):
    plugin.search(host, KEYWORD)
    host.auth_failures = 1
    hits = plugin.search(host, KEYWORD)

    assert hits
    assert host.token_issues == 2
    assert len(host.api_calls()) == 3
    assert host.api_calls()[-2]["headers"]["Authorization"] == "Bearer token-1"
    assert host.api_calls()[-1]["headers"]["Authorization"] == "Bearer token-2"


def test_重取令牌后仍失败则抛出(plugin, host):
    host.auth_failures = 2
    with pytest.raises(SourceAuthError):
        plugin.search(host, KEYWORD)
    assert host.token_issues == 2


def test_搜索请求地址和参数(plugin, host):
    plugin.search(host, KEYWORD)
    call = host.api_calls()[0]
    assert call["method"] == "GET"
    assert call["url"] == SEARCH_URL
    assert call["params"] == {
        "mode": "list",
        "keyword": KEYWORD,
        "pageNum": 1,
        "pageSize": 10,
    }


def test_搜索请求头(plugin, host):
    plugin.search(host, KEYWORD)
    headers = host.api_calls()[0]["headers"]
    assert headers["Authorization"] == "Bearer token-1"
    assert headers["Accept"] == "application/json;charset=utf-8"
    assert headers["version"] == "1"


def test_搜索解析出命中(plugin, host):
    hits = plugin.search(host, KEYWORD)
    rows = fixture("search.json")["data"]["result"]

    assert len(hits) == len(rows)
    first = rows[0]
    assert hits[0].external_id == str(first["id"]) == GID
    assert hits[0].title == first["chineseName"]
    assert hits[0].original_title == first["name"]
    assert hits[0].year == 2007
    assert hits[0].poster_url == first["mainImg"]


def test_搜索失败时带出消息(plugin, host):
    host.route(
        SEARCH_URL,
        {"mode": "list", "keyword": "找不到的作品", "pageNum": 1, "pageSize": 10},
        {"success": False, "code": 614, "msg": "根据关键字找不到对应档案"},
    )
    with pytest.raises(SourceError) as error:
        plugin.search(host, "找不到的作品")
    assert error.value.message == "根据关键字找不到对应档案"


def test_详情请求游戏和机构(plugin, host):
    raw = plugin.fetch(host, GID)

    urls = [(call["url"], call["params"]) for call in host.api_calls()]
    assert urls == [(ARCHIVE_URL, {"gid": GID}), (ARCHIVE_URL, {"orgId": ORG_ID})]
    assert raw["game"]["gid"] == int(GID)
    assert raw["developer"] == fixture("org.json")["data"]["org"]["name"] == "light"
    assert raw["cidMapping"] and raw["pidMapping"]


def test_详情里已带机构名时不再查询机构(plugin, host):
    archive = fixture("archive.json")
    archive["data"]["game"]["orgName"] = "light"
    host.route(ARCHIVE_URL, {"gid": GID}, archive)

    raw = plugin.fetch(host, GID)

    assert raw["developer"] == "light"
    assert [call["params"] for call in host.api_calls()] == [{"gid": GID}]


def test_详情失败时抛出(plugin, host):
    host.route(ARCHIVE_URL, {"gid": "1"}, {"success": False, "code": 404, "msg": "找不到档案"})
    with pytest.raises(SourceError) as error:
        plugin.fetch(host, "1")
    assert error.value.message == "找不到档案"


@pytest.fixture
def candidates(plugin, host):
    return plugin.candidates(plugin.fetch(host, GID))


def test_候选标题与原名(candidates):
    assert candidates["title"] == "神怒之日"
    assert candidates["original_title"] == "Dies irae"


def test_候选简介和发售日(candidates):
    game = fixture("archive.json")["data"]["game"]
    assert candidates["overview"] == game["introduction"]
    assert candidates["release_date"] == "2007-12-21"


def test_候选封面(candidates):
    game = fixture("archive.json")["data"]["game"]
    assert candidates["poster"] == [
        {"url": game["mainImg"], "language": None, "width": None, "height": None}
    ]


def test_候选品牌来自机构(candidates):
    assert candidates["brands"] == ["light"]


def test_候选声优带角色与中文名(candidates):
    actors = candidates["voice_actors"]
    by_id = {actor["external_id"]: actor for actor in actors}

    assert len(by_id) == 16
    # 鳥海浩輔在档案里配了藤井蓮和メルクリウス两个角色。
    merged = by_id["10233"]
    assert merged["name"] == "鳥海 浩輔"
    assert merged["original_name"] is None
    assert merged["character"] == "藤井莲 / メルクリウス"
    assert merged["photo_url"] == "https://cdn.ymgal.games/archive/def_img.webp"

    # 有中文名的人物用中文名，原名留在 original_name。
    chinese = by_id["10408"]
    assert chinese["name"] == "生天目仁美"
    assert chinese["original_name"] == "生天目 仁美"
    assert chinese["character"] == "冰室玲爱"
    assert chinese["photo_url"].startswith("https://cdn.ymgal.games/archive/main/")

    for actor in actors:
        assert set(actor) == {"external_id", "name", "original_name", "character", "photo_url"}
        assert actor["character"]


def test_候选剧本(candidates):
    assert [person["name"] for person in candidates["scenario"]] == [
        "中島 聖",
        "上野 研太郎",
        "夢野 Q作",
        "嘘屋 佐々木 酒人",
        "奈落 ハジメ",
        "門松 和哉",
        "正田 崇",
    ]
    assert candidates["scenario"][0]["external_id"] == "26287"


def test_候选原画(candidates):
    assert [person["name"] for person in candidates["artists"]] == ["夕薙", "Gユウスケ"]


def test_候选官网取第一个链接(plugin):
    raw = fixture("archive.json")["data"]
    raw["game"]["website"] = [
        {"title": "官中网站", "link": "https://example.com/cn"},
        {"title": "官网", "link": "https://example.com/jp"},
    ]
    assert plugin.candidates(raw)["website"] == "https://example.com/cn"


def test_候选去掉空值(plugin):
    result = plugin.candidates(
        {
            "game": {
                "gid": 1,
                "name": "原名",
                "chineseName": "",
                "introduction": "",
                "releaseDate": "2007",
                "mainImg": "",
                "characters": [],
                "staff": [],
                "website": [],
                "developerId": 0,
            },
            "developer": None,
            "cidMapping": {},
            "pidMapping": {},
        }
    )
    assert result == {"title": "原名", "original_title": "原名"}
