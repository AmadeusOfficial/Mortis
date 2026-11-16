import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "bangumi"


def load(plugin_id):
    folder = ROOT / "plugins" / plugin_id
    name = f"mortis_plugins_test.{plugin_id}"
    spec = importlib.util.spec_from_file_location(
        name, folder / "__init__.py", submodule_search_locations=[str(folder)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.PLUGIN


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeHost:
    """按 URL 回放录好的接口数据，同时记录每一次请求。"""

    def __init__(self, token: str | None = None) -> None:
        self.settings = type("Settings", (), {})()
        self.secrets = type("Secrets", (), {})()
        self.calls: list[dict[str, Any]] = []
        self.logs: list[str] = []
        self.token = token
        self.routes: dict[str, Any] = {}

    def route(self, url: str, body: Any) -> None:
        self.routes[url] = body

    def access_token(self) -> str | None:
        return self.token

    def log(self, message: str) -> None:
        self.logs.append(message)

    def get_json(self, url, *, params=None, headers=None):
        self.calls.append({"method": "GET", "url": url, "params": params, "headers": headers})
        return self.routes[url]

    def post_json(self, url, *, json=None, data=None, headers=None):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "json": json,
                "data": data,
                "headers": headers,
            }
        )
        return self.routes[url]

    def get_text(self, url, *, params=None, headers=None):
        raise AssertionError("这个插件不该用 get_text")


@pytest.fixture
def plugin():
    return load("bangumi")


@pytest.fixture
def host():
    subject = fixture("subject.json")
    subject_id = str(subject["id"])
    made = FakeHost()
    made.route(
        "https://api.bgm.tv/v0/search/subjects?limit=10",
        fixture("search.json"),
    )
    made.route(f"https://api.bgm.tv/v0/subjects/{subject_id}", subject)
    made.route(
        f"https://api.bgm.tv/v0/subjects/{subject_id}/characters", fixture("characters.json")
    )
    made.route(f"https://api.bgm.tv/v0/subjects/{subject_id}/persons", fixture("persons.json"))
    return made


def test_插件元数据(plugin):
    assert type(plugin).__name__ == "BangumiSource"
    assert plugin.id == "bangumi"
    assert plugin.name == "Bangumi"
    assert plugin.data_type == "galgame"
    assert plugin.provider == "bangumi"
    assert plugin.requests_per_second == 2
    assert plugin.oauth.authorize_url == "https://bgm.tv/oauth/authorize"
    assert plugin.oauth.token_url == "https://bgm.tv/oauth/access_token"


def test_secrets_忽略多余键(plugin):
    secrets = plugin.Secrets(**{"app_id": "a", "多余": 1, "expires_at": 5})
    assert secrets.app_id == "a"
    assert secrets.app_secret == ""
    assert secrets.access_token == ""
    assert secrets.refresh_token == ""
    assert secrets.expires_at == 5


def test_搜索解析出命中(plugin, host):
    hits = plugin.search(host, "Dies irae")

    assert len(hits) == len(fixture("search.json")["data"])
    first = fixture("search.json")["data"][0]
    hit = hits[0]
    assert hit.external_id == str(first["id"])
    assert hit.title == first["name_cn"]
    assert hit.original_title == first["name"]
    assert hit.year == 2012
    assert hit.overview == first["summary"]
    assert hit.poster_url == first["images"]["large"]

    call = host.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.bgm.tv/v0/search/subjects?limit=10"
    assert call["json"] == {"keyword": "Dies irae", "filter": {"type": [4], "nsfw": True}}


def test_搜索不带令牌时不加认证头(plugin, host):
    plugin.search(host, "Dies irae")
    assert "Authorization" not in (host.calls[0]["headers"] or {})


def test_搜索带令牌时加认证头(plugin, host):
    host.token = "tk-123"
    plugin.search(host, "Dies irae")
    assert host.calls[0]["headers"]["Authorization"] == "Bearer tk-123"


def test_禁用提示随令牌变化(plugin, host):
    assert plugin.disabled_reason(host) == "没登录 Bangumi 时搜不到 18 禁条目"
    host.token = "tk-123"
    assert plugin.disabled_reason(host) is None


def test_详情请求三个地址并合并(plugin, host):
    subject_id = str(fixture("subject.json")["id"])
    raw = plugin.fetch(host, subject_id)

    urls = [call["url"] for call in host.calls]
    assert urls == [
        f"https://api.bgm.tv/v0/subjects/{subject_id}",
        f"https://api.bgm.tv/v0/subjects/{subject_id}/characters",
        f"https://api.bgm.tv/v0/subjects/{subject_id}/persons",
    ]
    assert set(raw) == {"subject", "characters", "persons"}
    assert raw["subject"]["id"] == int(subject_id)
    assert len(raw["characters"]) == len(fixture("characters.json"))
    assert len(raw["persons"]) == len(fixture("persons.json"))


def test_详情带令牌时三个请求都带认证头(plugin, host):
    host.token = "tk-123"
    plugin.fetch(host, str(fixture("subject.json")["id"]))
    assert [call["headers"]["Authorization"] for call in host.calls] == ["Bearer tk-123"] * 3


@pytest.fixture
def candidates(plugin, host):
    return plugin.candidates(plugin.fetch(host, str(fixture("subject.json")["id"])))


def test_候选标题与原名(candidates):
    assert candidates["title"] == "神怒之日：为爱痴狂"
    assert candidates["original_title"] == "Dies irae ～Amantes amentes～"


def test_候选简介和日期(candidates):
    subject = fixture("subject.json")
    assert candidates["overview"] == subject["summary"]
    assert candidates["release_date"] == "2012-06-28"


def test_候选封面(candidates):
    subject = fixture("subject.json")
    assert candidates["poster"] == [
        {"url": subject["images"]["large"], "language": None, "width": None, "height": None}
    ]


def test_候选品牌去重(candidates):
    assert candidates["brands"] == ["light", "ヴューズ"]


def test_候选标签不超过二十个且按热度(candidates):
    tags = candidates["tags"]
    counts = [tag["count"] for tag in fixture("subject.json")["tags"]]
    expected = [
        tag["name"]
        for tag in sorted(fixture("subject.json")["tags"], key=lambda t: t["count"], reverse=True)[
            :20
        ]
    ]
    assert len(tags) <= 20
    assert tags == expected
    assert counts == sorted(counts, reverse=True)


def test_候选评分(candidates):
    subject = fixture("subject.json")
    assert candidates["ratings"] == [
        {
            "source": "bangumi",
            "rating": subject["rating"]["score"],
            "max_rating": 10,
            "vote_count": subject["rating"]["total"],
        }
    ]


def test_候选官网(candidates):
    assert candidates["website"] == (
        "https://www.light.gr.jp/light/products/diesirae_cs/index.html"
    )


def test_候选声优带角色名且同人多角色合并(candidates):
    actors = candidates["voice_actors"]
    by_id = {actor["external_id"]: actor for actor in actors}

    # 鳥海浩輔配了藤井蓮和メルクリウス两个角色。
    merged = by_id["4147"]
    assert merged["name"] == "鳥海浩輔"
    assert merged["character"] == "藤井蓮 / メルクリウス"
    assert merged["original_name"] is None
    assert merged["photo_url"].startswith("https://lain.bgm.tv/")

    for actor in actors:
        assert actor["character"]
        assert set(actor) == {"external_id", "name", "original_name", "character", "photo_url"}


def test_候选剧本和原画(candidates):
    assert candidates["scenario"] == [
        {
            "external_id": "6968",
            "name": "正田崇",
            "original_name": None,
            "character": None,
            "photo_url": "https://lain.bgm.tv/r/400/pic/crt/l/bb/50/6968_prsn_zyD4j.jpg?r=1557689787",
        }
    ]
    assert [person["name"] for person in candidates["artists"]] == ["Gユウスケ"]


def test_候选去掉空值(plugin):
    raw = {
        "subject": {
            "name": "原名",
            "name_cn": "",
            "summary": "",
            "date": "2012年6月28日",
            "images": {},
            "infobox": [],
            "tags": [],
            "rating": {"score": 0, "total": 0},
        },
        "characters": [],
        "persons": [],
    }
    result = plugin.candidates(raw)
    assert result == {"title": "原名", "original_title": "原名"}
