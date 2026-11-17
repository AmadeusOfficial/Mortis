import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest

from mortis.config.models import AppConfig, Secrets, TvScrapeConfig
from mortis.errors import UserError
from mortis.sources import tvdb
from mortis.sources.builtin import FetchRequest

KEY = "tvdb-secret-key"
PIN = "4321"


def series_body(**overrides: Any) -> dict[str, Any]:
    data = {
        "id": 81189,
        "name": "Breaking Bad",
        "originalLanguage": "eng",
        "overview": "A chemistry teacher.",
        "firstAired": "2008-01-20",
        "status": {"id": 2, "name": "Ended"},
        "nameTranslations": ["eng", "zho", "zhtw"],
        "overviewTranslations": ["eng", "zho", "zhtw"],
        "genres": [{"id": 1, "name": "Drama"}, {"id": 2, "name": "Crime"}],
        "originalNetwork": {"id": 1, "name": "AMC"},
        "latestNetwork": {"id": 1, "name": "AMC"},
        "companies": [
            {"name": "AMC", "companyType": {"companyTypeName": "Network"}},
            {"name": "Sony Pictures Television", "companyType": {"companyTypeName": "Studio"}},
            {"name": "Some Distributor", "companyType": {"companyTypeName": "Distributor"}},
        ],
        "score": 999,
        "artworks": [
            {
                "type": 2,
                "image": "https://artworks.thetvdb.com/p-en.jpg",
                "language": "eng",
                "score": 10,
                "width": 680,
                "height": 1000,
            },
            {
                "type": 2,
                "image": "https://artworks.thetvdb.com/p-zh.jpg",
                "language": "zho",
                "score": 1,
                "width": 680,
                "height": 1000,
            },
            {
                "type": 3,
                "image": "https://artworks.thetvdb.com/f-en.jpg",
                "language": "eng",
                "score": 50,
                "width": 1920,
                "height": 1080,
            },
            {
                "type": 3,
                "image": "https://artworks.thetvdb.com/f-none.jpg",
                "language": None,
                "score": 5,
                "width": 1920,
                "height": 1080,
            },
            {
                "type": 1,
                "image": "/banners/b.jpg",
                "language": "eng",
                "score": 1,
                "width": 758,
                "height": 140,
            },
            {
                "type": 23,
                "image": "https://artworks.thetvdb.com/logo.png",
                "language": "eng",
                "score": 1,
                "width": 800,
                "height": 310,
            },
            {
                "type": 7,
                "seasonId": 30272,
                "image": "https://artworks.thetvdb.com/s1-art.jpg",
                "language": "eng",
                "score": 3,
                "width": 680,
                "height": 1000,
            },
        ],
        "seasons": [
            {
                "id": 30272,
                "number": 1,
                "type": {"type": "official"},
                "image": "https://artworks.thetvdb.com/s1.jpg",
            },
            {
                "id": 30273,
                "number": 2,
                "type": {"type": "official"},
                "image": "https://artworks.thetvdb.com/s2.jpg",
            },
            {
                "id": 99999,
                "number": 1,
                "type": {"type": "dvd"},
                "image": "https://artworks.thetvdb.com/dvd1.jpg",
            },
        ],
    }
    data.update(overrides)
    return {"status": "success", "data": data}


def episodes_page(episodes: list[dict[str, Any]], next_page: str | None = None) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {"series": {"id": 81189}, "episodes": episodes},
        "links": {"next": next_page},
    }


def episode(season: int, number: int, name: str) -> dict[str, Any]:
    return {"seasonNumber": season, "number": number, "name": name, "overview": f"{name} 剧情"}


class FakeTvdb:
    def __init__(self) -> None:
        self.routes: dict[str, list[tuple[int, Any]]] = {}
        self.requests: list[httpx.Request] = []
        self.logins = 0
        self.token = "token-1"
        self.login_status = 200
        self.add("/v4/series/81189/extended", series_body())
        self.add(
            "/v4/series/81189/translations/zho",
            {
                "status": "success",
                "data": {"name": "绝命毒师", "overview": "化学老师。", "language": "zho"},
            },
        )
        self.add(
            "/v4/series/81189/translations/zhtw",
            {"status": "success", "data": {"name": "絕命毒師", "overview": "化學老師。"}},
        )
        self.add("/v4/series/81189/episodes/official", episodes_page([]))

    def add(self, path: str, body: Any, status: int = 200) -> None:
        self.routes[path] = [(status, body)]

    def queue(self, path: str, *responses: tuple[int, Any]) -> None:
        self.routes[path] = list(responses)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v4/login":
            self.logins += 1
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"status": "failure"})
            token = f"token-{self.logins}"
            self.token = token
            return httpx.Response(200, json={"status": "success", "data": {"token": token}})
        responses = self.routes.get(request.url.path)
        if not responses:
            return httpx.Response(404, json={"status": "failure"})
        status, body = responses[0] if len(responses) == 1 else responses.pop(0)
        if status == 200 and request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"status": "failure"})
        return httpx.Response(status, json=body)


@pytest.fixture(autouse=True)
def clear_tokens() -> Iterator[None]:
    tvdb._tokens.clear()
    yield
    tvdb._tokens.clear()


def make_request(
    fake: FakeTvdb,
    ids: dict[str, str] | None = None,
    language: str = "zh-CN",
    logs: list[str] | None = None,
    pin: str = "",
) -> FetchRequest:
    secrets = Secrets(sources={"tvdb": {"api_key": KEY, "pin": pin}})
    services = SimpleNamespace(config_store=SimpleNamespace(load_secrets=lambda: secrets))
    sink = logs if logs is not None else []
    return FetchRequest(
        services,  # type: ignore[arg-type]
        "tv",
        TvScrapeConfig(language=language),
        ids if ids is not None else {"tvdb": "81189", "tmdb": "1396"},
        httpx.Client(transport=httpx.MockTransport(fake)),
        sink.append,
    )


def test_check_requires_key() -> None:
    assert tvdb.check(AppConfig(), Secrets()) == "Key 未填"
    assert tvdb.check(AppConfig(), Secrets(sources={"tvdb": {"api_key": "  "}})) == "Key 未填"
    assert tvdb.check(AppConfig(), Secrets(sources={"tvdb": {"api_key": KEY}})) is None
    assert tvdb.secrets_of(Secrets()) == tvdb.SecretSettings(api_key="", pin="")


def test_login_body_and_token_reused() -> None:
    fake = FakeTvdb()
    request = make_request(fake, pin=PIN)
    assert tvdb.FETCHER.fetch(request) is not None
    assert tvdb.FETCHER.fetch(request) is not None
    assert fake.logins == 1
    login = fake.requests[0]
    assert login.method == "POST"
    assert json.loads(login.content) == {"apikey": KEY, "pin": PIN}
    assert all(
        r.headers["Authorization"] == "Bearer token-1"
        for r in fake.requests
        if r.url.path != "/v4/login"
    )


def test_empty_pin_not_sent() -> None:
    fake = FakeTvdb()
    tvdb.FETCHER.fetch(make_request(fake))
    assert json.loads(fake.requests[0].content) == {"apikey": KEY}


def test_relogin_once_on_401() -> None:
    fake = FakeTvdb()
    request = make_request(fake)
    tvdb.FETCHER.fetch(request)
    fake.token = "token-rotated"  # 服务端让旧令牌失效
    raw = tvdb.FETCHER.fetch(request)
    assert raw is not None
    assert fake.logins == 2
    assert tvdb._tokens[(KEY, "")] == "token-2"


def test_repeated_401_raises_without_key() -> None:
    fake = FakeTvdb()
    fake.queue("/v4/series/81189/extended", (401, {}), (401, {}), (401, {}))
    logs: list[str] = []
    with pytest.raises(UserError) as caught:
        tvdb.FETCHER.fetch(make_request(fake, logs=logs))
    assert fake.logins == 2
    assert KEY not in caught.value.message
    assert "token" not in caught.value.message
    assert not any(KEY in line or "token-" in line for line in logs)
    assert (KEY, "") not in tvdb._tokens


def test_login_rejected() -> None:
    fake = FakeTvdb()
    fake.login_status = 401
    with pytest.raises(tvdb.TvdbAuthError) as caught:
        tvdb.FETCHER.fetch(make_request(fake))
    assert KEY not in str(caught.value)


def test_no_tvdb_id_returns_none() -> None:
    fake = FakeTvdb()
    logs: list[str] = []
    assert tvdb.FETCHER.fetch(make_request(fake, ids={"tmdb": "1396"}, logs=logs)) is None
    assert fake.requests == []
    assert any("TVDB 编号" in line for line in logs)


def test_series_not_found_returns_none() -> None:
    fake = FakeTvdb()
    assert tvdb.FETCHER.fetch(make_request(fake, ids={"tvdb": "404404"})) is None


def test_token_not_in_snapshot() -> None:
    fake = FakeTvdb()
    raw = tvdb.FETCHER.fetch(make_request(fake))
    text = json.dumps(raw, ensure_ascii=False)
    assert "token-1" not in text
    assert KEY not in text


def test_candidates_take_chinese_translation() -> None:
    fake = FakeTvdb()
    request = make_request(fake)
    raw = tvdb.FETCHER.fetch(request)
    assert raw is not None
    assert "/v4/series/81189/translations/zho" in fake.paths()
    assert "/v4/series/81189/translations/zhtw" not in fake.paths()
    values = tvdb.FETCHER.candidates(raw, request)
    assert values["title"] == "绝命毒师"
    assert values["original_title"] == "Breaking Bad"
    assert values["overview"] == "化学老师。"
    assert values["status"] == "已完结"
    assert values["release_date"] == "2008-01-20"
    assert values["genres"] == ["剧情", "犯罪"]
    assert values["studios"] == ["AMC", "Sony Pictures Television"]
    assert "ratings" not in values
    assert [i["url"] for i in values["poster"]] == [
        "https://artworks.thetvdb.com/p-zh.jpg",
        "https://artworks.thetvdb.com/p-en.jpg",
    ]
    assert values["poster"][0] == {
        "url": "https://artworks.thetvdb.com/p-zh.jpg",
        "language": "zh",
        "width": 680,
        "height": 1000,
    }
    assert values["fanart"][0]["url"] == "https://artworks.thetvdb.com/f-none.jpg"
    assert values["fanart"][0]["language"] is None
    assert values["banner"][0]["url"] == "https://artworks.thetvdb.com/banners/b.jpg"
    assert values["clearlogo"][0]["url"] == "https://artworks.thetvdb.com/logo.png"


def test_missing_chinese_leaves_text_empty_and_skips_traditional() -> None:
    fake = FakeTvdb()
    fake.add(
        "/v4/series/81189/extended",
        series_body(nameTranslations=["eng", "zhtw"], overviewTranslations=["eng", "zhtw"]),
    )
    request = make_request(fake)
    raw = tvdb.FETCHER.fetch(request)
    assert raw is not None
    assert not any("/translations/" in path for path in fake.paths())
    values = tvdb.FETCHER.candidates(raw, request)
    assert "title" not in values
    assert "overview" not in values
    assert values["original_title"] == "Breaking Bad"


def test_translation_404_leaves_text_empty() -> None:
    fake = FakeTvdb()
    del fake.routes["/v4/series/81189/translations/zho"]
    request = make_request(fake)
    raw = tvdb.FETCHER.fetch(request)
    assert raw is not None
    assert raw["translation"] is None
    values = tvdb.FETCHER.candidates(raw, request)
    assert "title" not in values
    assert "overview" not in values


def test_original_language_used_without_translation() -> None:
    fake = FakeTvdb()
    fake.add(
        "/v4/series/81189/extended",
        series_body(
            name="请回答1988",
            originalLanguage="zho",
            overview="原文剧情",
            nameTranslations=[],
            overviewTranslations=[],
        ),
    )
    request = make_request(fake)
    raw = tvdb.FETCHER.fetch(request)
    assert raw is not None
    values = tvdb.FETCHER.candidates(raw, request)
    assert values["title"] == "请回答1988"
    assert values["overview"] == "原文剧情"


def test_language_mapping() -> None:
    assert tvdb.tvdb_language("zh-CN") == "zho"
    assert tvdb.tvdb_language("zh-TW") == "zhtw"
    assert tvdb.tvdb_language("en-US") == "eng"
    assert tvdb.tvdb_language("ja-JP") == "jpn"


def test_english_language_keeps_english_names() -> None:
    fake = FakeTvdb()
    fake.add(
        "/v4/series/81189/translations/eng",
        {"status": "success", "data": {"name": "Breaking Bad", "overview": "Teacher."}},
    )
    request = make_request(fake, language="en-US")
    raw = tvdb.FETCHER.fetch(request)
    assert raw is not None
    values = tvdb.FETCHER.candidates(raw, request)
    assert values["title"] == "Breaking Bad"
    assert values["status"] == "Ended"
    assert values["genres"] == ["Drama", "Crime"]


def test_episodes_paged_in_aired_order() -> None:
    fake = FakeTvdb()
    fake.queue(
        "/v4/series/81189/episodes/official",
        (200, episodes_page([episode(1, 1, "Pilot")], next_page="https://next")),
        (200, episodes_page([episode(2, 3, "Bit by a Dead Bee")])),
    )
    raw = tvdb.FETCHER.fetch(make_request(fake, language="en-US"))
    assert raw is not None
    pages = [r.url.params.get("page") for r in fake.requests if r.url.path.endswith("/official")]
    assert pages == ["0", "1"]
    assert tvdb.episode_candidates(raw, 2, 3) == {
        "title": "Bit by a Dead Bee",
        "overview": "Bit by a Dead Bee 剧情",
    }
    assert tvdb.episode_candidates(raw, 1, 1)["title"] == "Pilot"
    assert tvdb.episode_candidates(raw, 1, 2) == {}
    assert tvdb.episode_candidates(raw, 9, 1) == {}


def test_episode_names_in_other_language_left_empty() -> None:
    fake = FakeTvdb()
    fake.add("/v4/series/81189/episodes/official", episodes_page([episode(1, 1, "Pilot")]))
    raw = tvdb.FETCHER.fetch(make_request(fake))
    assert raw is not None
    assert tvdb.episode_candidates(raw, 1, 1) == {}


def test_season_candidates_use_aired_numbers() -> None:
    fake = FakeTvdb()
    raw = tvdb.FETCHER.fetch(make_request(fake))
    assert raw is not None
    first = tvdb.season_candidates(raw, 1)
    assert [i["url"] for i in first["poster"]] == ["https://artworks.thetvdb.com/s1-art.jpg"]
    second = tvdb.season_candidates(raw, 2)
    assert second["poster"][0]["url"] == "https://artworks.thetvdb.com/s2.jpg"
    assert tvdb.season_candidates(raw, 7) == {}
