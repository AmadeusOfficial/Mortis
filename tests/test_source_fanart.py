import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest

from mortis.config.models import AppConfig, MovieScrapeConfig, Secrets, TvScrapeConfig
from mortis.sources import fanart
from mortis.sources.builtin import FetchRequest

API_KEY = "proj-key-123456"
CLIENT_KEY = "personal-key-654321"
ASSETS = "https://assets.fanart.tv/"


def art(url: str, lang: str = "en", likes: int = 0, **extra: Any) -> dict[str, Any]:
    return {"id": url, "url": f"{ASSETS}{url}.jpg", "lang": lang, "likes": str(likes), **extra}


def urls(images: list[dict[str, Any]]) -> list[str]:
    return [image["url"].removeprefix(ASSETS).removesuffix(".jpg") for image in images]


class FakeFanart:
    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, Any]] = {}
        self.requests: list[httpx.Request] = []

    def add(self, path: str, body: Any, status: int = 200) -> None:
        self.routes[path] = (status, body)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.routes.get(
            request.url.path, (404, {"status": "error", "error message": "Not found"})
        )
        return httpx.Response(status, json=body)


def make_request(
    fake: FakeFanart,
    ids: dict[str, str],
    *,
    data_type_id: str = "movie",
    config: MovieScrapeConfig | TvScrapeConfig | None = None,
    keys: dict[str, str] | None = None,
) -> tuple[FetchRequest, list[str]]:
    secrets = Secrets(sources={"fanart": keys if keys is not None else {"api_key": API_KEY}})
    services = SimpleNamespace(config_store=SimpleNamespace(load_secrets=lambda: secrets))
    logs: list[str] = []
    if config is None:
        config = MovieScrapeConfig() if data_type_id == "movie" else TvScrapeConfig()
    request = FetchRequest(
        services=services,  # type: ignore[arg-type]
        data_type_id=data_type_id,
        config=config,
        ids=ids,
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        log=logs.append,
    )
    return request, logs


MOVIE = {
    "name": "示例电影",
    "tmdb_id": "603",
    "imdb_id": "tt0133093",
    "movieposter": [art("p-en", "en", 9), art("p-zh", "zh", 1), art("p-none", "00", 5)],
    "moviebackground": [
        art("bg-en", "en", 20),
        art("bg-none-low", "00", 1),
        art("bg-zh", "zh", 2),
        art("bg-none-high", "", 7),
    ],
    "moviebanner": [art("banner", "en")],
    "hdmovielogo": [art("logo-hd-en", "en", 1)],
    "movielogo": [art("logo-sd-en", "en", 50), art("logo-sd-zh", "zh", 0)],
    "hdmovieclearart": [art("ca-hd", "en")],
    "movieart": [art("ca-sd", "en")],
    "moviedisc": [art("disc", "en", disc="1", disc_type="bluray")],
    "moviethumb": [art("thumb", "en")],
}

SHOW = {
    "name": "示例剧",
    "thetvdb_id": "81189",
    "tvposter": [art("tp-en", "en", 3), art("tp-zh", "zh", 0)],
    "showbackground": [art("sb-en", "en", 9), art("sb-none", "", 0, season="all")],
    "tvbanner": [art("tb", "en")],
    "hdtvlogo": [art("tl-hd", "en")],
    "clearlogo": [art("tl-sd", "en")],
    "hdclearart": [art("tc-hd", "en")],
    "clearart": [art("tc-sd", "en")],
    "tvthumb": [art("tt", "en")],
    "characterart": [art("char", "00")],
    "seasonposter": [
        art("s1-en", "en", 10, season="1"),
        art("s1-zh", "zh", 0, season="1"),
        art("s2-zh", "zh", 99, season="2"),
        art("all-zh", "zh", 50, season="all"),
        art("s0-en", "en", 1, season="0"),
    ],
    "seasonbanner": [art("sban-all", "en", 0, season="all")],
    "seasonthumb": [art("sth-1", "00", 0, season="1")],
}


def test_movie_mapping_and_sizes() -> None:
    values = fanart.movie_candidates(MOVIE, "zh-CN", textless_fanart=False)
    assert set(values) == set(fanart.MOVIE_FIELDS) | {"extrafanart"}
    assert urls(values["banner"]) == ["banner"]
    assert urls(values["clearart"]) == ["ca-hd", "ca-sd"]
    assert urls(values["discart"]) == ["disc"]
    assert urls(values["thumb"]) == ["thumb"]
    assert values["poster"][0] == {
        "url": "https://assets.fanart.tv/p-zh.jpg",
        "language": "zh",
        "width": 1000,
        "height": 1426,
    }
    assert (values["discart"][0]["width"], values["discart"][0]["height"]) == (1000, 1000)
    assert (values["clearlogo"][0]["width"], values["clearlogo"][0]["height"]) == (400, 155)


def test_movie_language_and_likes_order() -> None:
    values = fanart.movie_candidates(MOVIE, "zh-CN", textless_fanart=False)
    assert urls(values["poster"]) == ["p-zh", "p-none", "p-en"]
    assert urls(values["fanart"]) == ["bg-zh", "bg-none-high", "bg-none-low", "bg-en"]
    assert [image["language"] for image in values["fanart"]] == ["zh", None, None, "en"]
    # 同一语言档里先 HD 后普通；中文的普通标志排在英文 HD 前面。
    assert urls(values["clearlogo"]) == ["logo-sd-zh", "logo-hd-en", "logo-sd-en"]


def test_movie_other_language_after_english() -> None:
    raw = {"movieposter": [art("de", "de", 100), art("en", "en", 0), art("ja", "ja", 5)]}
    values = fanart.movie_candidates(raw, "zh-CN", textless_fanart=False)
    assert urls(values["poster"]) == ["en", "de", "ja"]


def test_movie_textless_fanart_first_and_extrafanart() -> None:
    values = fanart.movie_candidates(MOVIE, "zh-CN", textless_fanart=True)
    assert urls(values["fanart"]) == ["bg-none-high", "bg-none-low", "bg-zh", "bg-en"]
    assert urls(values["extrafanart"]) == ["bg-none-low", "bg-zh", "bg-en"]
    # 只有背景类优先无文字，海报不受影响。
    assert urls(values["poster"]) == ["p-zh", "p-none", "p-en"]


def test_show_mapping() -> None:
    values = fanart.show_candidates(SHOW, "zh-CN", textless_fanart=True)
    assert set(values) == set(fanart.TV_FIELDS)
    assert urls(values["poster"]) == ["tp-zh", "tp-en"]
    assert urls(values["fanart"]) == ["sb-none", "sb-en"]
    assert urls(values["banner"]) == ["tb"]
    assert urls(values["clearlogo"]) == ["tl-hd", "tl-sd"]
    assert urls(values["clearart"]) == ["tc-hd", "tc-sd"]
    assert urls(values["thumb"]) == ["tt"]
    assert urls(values["characterart"]) == ["char"]
    assert values["characterart"][0]["language"] is None
    assert (values["thumb"][0]["width"], values["thumb"][0]["height"]) == (500, 281)
    assert "extrafanart" not in values


def test_show_without_textless_preference() -> None:
    values = fanart.show_candidates(SHOW, "zh-CN", textless_fanart=False)
    assert urls(values["fanart"]) == ["sb-none", "sb-en"]
    raw = {"showbackground": [art("en", "en", 1), art("zh", "zh", 0), art("none", "00", 9)]}
    assert urls(fanart.show_candidates(raw, "zh-CN", False)["fanart"]) == ["zh", "none", "en"]
    assert urls(fanart.show_candidates(raw, "zh-CN", True)["fanart"]) == ["none", "zh", "en"]


def test_season_candidates_by_number_then_all() -> None:
    first = fanart.season_candidates(SHOW, 1, "zh-CN")
    assert urls(first["poster"]) == ["s1-zh", "s1-en", "all-zh"]
    assert urls(first["banner"]) == ["sban-all"]
    assert urls(first["thumb"]) == ["sth-1"]
    assert first["thumb"][0]["language"] is None
    assert (first["banner"][0]["width"], first["banner"][0]["height"]) == (1000, 185)

    second = fanart.season_candidates(SHOW, 2, "zh-CN")
    assert urls(second["poster"]) == ["s2-zh", "all-zh"]
    assert "thumb" not in second

    specials = fanart.season_candidates(SHOW, 0, "en-US")
    assert urls(specials["poster"]) == ["s0-en", "all-zh"]


def test_fetch_movie_by_tmdb_then_imdb() -> None:
    fake = FakeFanart()
    fake.add("/v3/movies/603", MOVIE)
    fake.add("/v3/movies/tt0133093", MOVIE)

    request, logs = make_request(fake, {"tmdb": "603", "imdb": "tt0133093"})
    assert fanart.FETCHER.fetch(request) == MOVIE
    request, _ = make_request(fake, {"imdb": "tt0133093"})
    assert fanart.FETCHER.fetch(request) == MOVIE

    assert [r.url.path for r in fake.requests] == ["/v3/movies/603", "/v3/movies/tt0133093"]
    assert fake.requests[0].url.host == "webservice.fanart.tv"
    assert fake.requests[0].url.params["api_key"] == API_KEY
    assert "client_key" not in fake.requests[0].url.params
    assert logs == ["请求 Fanart.tv 电影 603"]


def test_fetch_movie_without_ids() -> None:
    fake = FakeFanart()
    request, logs = make_request(fake, {})
    assert fanart.FETCHER.fetch(request) is None
    assert fake.requests == []
    assert logs == ["没有 TMDB 或 IMDb 编号，跳过 Fanart.tv"]


@pytest.mark.parametrize("data_type_id", ["tv", "anime"])
def test_fetch_series_needs_tvdb(data_type_id: str) -> None:
    fake = FakeFanart()
    fake.add("/v3/tv/81189", SHOW)

    request, logs = make_request(fake, {"tmdb": "1396"}, data_type_id=data_type_id)
    assert fanart.FETCHER.fetch(request) is None
    assert fake.requests == []
    assert logs == ["没有 TVDB 编号，跳过 Fanart.tv"]

    request, logs = make_request(fake, {"tmdb": "1396", "tvdb": "81189"}, data_type_id=data_type_id)
    raw = fanart.FETCHER.fetch(request)
    assert raw == SHOW
    assert [r.url.path for r in fake.requests] == ["/v3/tv/81189"]
    assert logs == ["请求 Fanart.tv 剧 81189"]
    assert urls(fanart.FETCHER.candidates(raw, request)["poster"]) == ["tp-zh", "tp-en"]


def test_fetch_not_found_returns_none() -> None:
    fake = FakeFanart()
    request, _ = make_request(fake, {"tmdb": "999"})
    assert fanart.FETCHER.fetch(request) is None
    fake.add("/v3/movies/998", {"status": "error", "error message": "Not found"})
    request, _ = make_request(fake, {"tmdb": "998"})
    assert fanart.FETCHER.fetch(request) is None


def test_fetch_rejected_key() -> None:
    fake = FakeFanart()
    fake.add("/v3/movies/603", {"status": "error", "error message": "Invalid API key"}, 401)
    request, _ = make_request(fake, {"tmdb": "603"})
    with pytest.raises(fanart.FanartError) as error:
        fanart.FETCHER.fetch(request)
    assert error.value.message == "Fanart.tv 拒绝了这个 Key"


def test_keys_stay_out_of_logs_errors_and_snapshot() -> None:
    fake = FakeFanart()
    fake.add("/v3/movies/603", MOVIE)
    keys = {"api_key": API_KEY, "client_key": CLIENT_KEY}
    request, logs = make_request(fake, {"tmdb": "603"}, keys=keys)
    raw = fanart.FETCHER.fetch(request)
    assert fake.requests[0].url.params["client_key"] == CLIENT_KEY
    values = fanart.FETCHER.candidates(raw, request)
    dumped = json.dumps([raw, values, logs], ensure_ascii=False)
    assert API_KEY not in dumped
    assert CLIENT_KEY not in dumped

    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}")

    request, _ = make_request(FakeFanart(), {"tmdb": "603"}, keys=keys)
    request = replace(request, http=httpx.Client(transport=httpx.MockTransport(explode)))
    with pytest.raises(fanart.FanartError) as error:
        fanart.FETCHER.fetch(request)
    assert error.value.message.startswith("连不上 Fanart.tv")
    assert API_KEY not in error.value.message
    assert CLIENT_KEY not in error.value.message
    assert "***" in error.value.message


def test_check_and_settings() -> None:
    config = AppConfig()
    assert fanart.check(config, Secrets()) == "Key 未填"
    assert fanart.check(config, Secrets(sources={"fanart": {"api_key": "  "}})) == "Key 未填"
    assert fanart.check(config, Secrets(sources={"fanart": {"client_key": "x"}})) == "Key 未填"
    assert fanart.check(config, Secrets(sources={"fanart": {"api_key": API_KEY}})) is None

    keys = fanart.secrets_of(Secrets(sources={"fanart": {"api_key": "a", "other": 1}}))
    assert (keys.api_key, keys.client_key) == ("a", "")
    assert fanart.settings_of(config) == fanart.Settings()
