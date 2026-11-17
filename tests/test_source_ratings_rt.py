from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx

from mortis.config.models import AppConfig, MovieScrapeConfig, Secrets, TvScrapeConfig
from mortis.sources import ratings_rt
from mortis.sources.builtin import FetchRequest
from mortis.version import get_version

KEY = "mdb-secret-123"
IMDB = "tt0111161"
TOMATOMETER = f"{ratings_rt.ENTITY_PREFIX}{ratings_rt.TOMATOMETER}"
AUDIENCE = f"{ratings_rt.ENTITY_PREFIX}Q108403540"

Handler = Callable[[httpx.Request], httpx.Response]


def _literal(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def _row(
    value: str, method: str | None = None, count: str | None = None, date: str | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "item": {"type": "uri", "value": f"{ratings_rt.ENTITY_PREFIX}Q172241"},
        "value": _literal(value),
    }
    if method is not None:
        row["method"] = {"type": "uri", "value": method}
    if count is not None:
        row["count"] = _literal(count)
    if date is not None:
        row["date"] = _literal(date)
    return row


def _sparql(*rows: dict[str, Any]) -> dict[str, Any]:
    return {
        "head": {"vars": ["item", "value", "method", "count", "date"]},
        "results": {"bindings": list(rows)},
    }


class Sites:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.wikidata: tuple[int, Any] = (200, _sparql())
        self.mdblist: tuple[int, Any] = (404, {"error": "not found"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "query.wikidata.org":
            status, body = self.wikidata
        elif request.url.host == "api.mdblist.com":
            status, body = self.mdblist
        else:
            status, body = 404, {}
        return httpx.Response(status, json=body)

    def hosts(self) -> list[str]:
        return [request.url.host for request in self.requests]


def _request(
    sites: Sites,
    *,
    key: str = "",
    ids: dict[str, str] | None = None,
    tv: bool = False,
    logs: list[str] | None = None,
) -> FetchRequest:
    secrets = Secrets(sources={"ratings_rt": {"mdblist_api_key": key}} if key else {})
    store = SimpleNamespace(load_secrets=lambda: secrets, load_config=AppConfig)
    sink = logs if logs is not None else []
    return FetchRequest(
        services=SimpleNamespace(config_store=store),  # type: ignore[arg-type]
        data_type_id="tv" if tv else "movie",
        config=TvScrapeConfig() if tv else MovieScrapeConfig(),
        ids={"imdb": IMDB, "tmdb": "278"} if ids is None else ids,
        http=httpx.Client(transport=httpx.MockTransport(sites)),
        log=sink.append,
    )


def _fetch(request: FetchRequest) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    raw = ratings_rt.FETCHER.fetch(request)
    values = ratings_rt.FETCHER.candidates(raw, request) if raw is not None else {}
    return raw, values


def test_wikidata_percentage_rating() -> None:
    sites = Sites()
    sites.wikidata = (
        200,
        _sparql(
            _row("8.1/10", count="80", date="2024-01-01T00:00:00Z"),
            _row("91%", method=TOMATOMETER, count="+120", date="2024-01-01T00:00:00Z"),
        ),
    )
    raw, values = _fetch(_request(sites))
    assert raw is not None and raw["provider"] == "wikidata"
    assert values == {
        "ratings": [
            {"source": "tomatometerallcritics", "rating": 91, "max_rating": 100, "vote_count": 120}
        ]
    }
    query = sites.requests[0].url.params["query"]
    assert f'wdt:P345 "{IMDB}"' in query
    assert "pq:P447 wd:Q105584" in query
    assert sites.requests[0].url.params["format"] == "json"


def test_wikidata_picks_latest_and_skips_other_methods() -> None:
    sites = Sites()
    sites.wikidata = (
        200,
        _sparql(
            _row("88%", method=TOMATOMETER, count="100", date="2020-05-01T00:00:00Z"),
            _row("94%", method=TOMATOMETER, date="2023-07-01T00:00:00Z"),
            _row("99%", method=AUDIENCE, count="5000", date="2025-01-01T00:00:00Z"),
            _row("70%", date="2026-01-01T00:00:00Z"),
        ),
    )
    _raw, values = _fetch(_request(sites))
    assert values["ratings"] == [
        {"source": "tomatometerallcritics", "rating": 94, "max_rating": 100, "vote_count": None}
    ]


def test_wikidata_untagged_percentage_used_when_no_method() -> None:
    sites = Sites()
    sites.wikidata = (
        200,
        _sparql(_row("75%", date="2019-01-01T00:00:00Z"), _row("80%", date="2021-01-01T00:00:00Z")),
    )
    _raw, values = _fetch(_request(sites))
    assert values["ratings"][0]["rating"] == 80


def test_wikidata_without_rotten_tomatoes_returns_none() -> None:
    sites = Sites()
    sites.wikidata = (200, _sparql(_row("8.1/10", method=TOMATOMETER)))
    logs: list[str] = []
    assert ratings_rt.FETCHER.fetch(_request(sites, logs=logs)) is None
    assert any("没有烂番茄" in line for line in logs)


def test_wikidata_empty_result_returns_none() -> None:
    sites = Sites()
    assert ratings_rt.FETCHER.fetch(_request(sites)) is None


def test_wikidata_sends_user_agent() -> None:
    sites = Sites()
    sites.wikidata = (200, _sparql(_row("90%")))
    _fetch(_request(sites))
    agent = sites.requests[0].headers["User-Agent"]
    assert agent.startswith(f"Mortis/{get_version()}")
    assert "local media library" in agent
    assert "sparql" not in sites.requests[0].headers.get("Accept", "")


def test_wikidata_error_raises_without_fallback() -> None:
    sites = Sites()
    sites.wikidata = (503, {})
    try:
        ratings_rt.FETCHER.fetch(_request(sites))
    except Exception as error:
        assert "HTTP 503" in str(error)
    else:
        raise AssertionError("应该报错")


def test_mdblist_used_when_key_set() -> None:
    sites = Sites()
    sites.mdblist = (
        200,
        {
            "title": "肖申克的救赎",
            "ratings": [
                {"source": "imdb", "value": 9.3, "score": 93, "votes": 3000000},
                {"source": "tomatoesaudience", "value": 98, "votes": 250000},
                {"source": "tomatoes", "value": 89, "score": 89, "votes": 150},
            ],
        },
    )
    sites.wikidata = (200, _sparql(_row("50%")))
    raw, values = _fetch(_request(sites, key=KEY))
    assert sites.hosts() == ["api.mdblist.com"]
    assert sites.requests[0].url.path == f"/imdb/movie/{IMDB}"
    assert sites.requests[0].url.params["apikey"] == KEY
    assert raw is not None and raw["provider"] == "mdblist"
    assert values["ratings"] == [
        {"source": "tomatometerallcritics", "rating": 89, "max_rating": 100, "vote_count": 150}
    ]


def test_mdblist_uses_show_path_for_series() -> None:
    sites = Sites()
    sites.mdblist = (200, {"ratings": [{"source": "tomatoes", "value": None, "score": "77"}]})
    _raw, values = _fetch(_request(sites, key=KEY, tv=True))
    assert sites.requests[0].url.path == f"/imdb/show/{IMDB}"
    assert values["ratings"][0]["rating"] == 77


def test_mdblist_401_falls_back_to_wikidata() -> None:
    sites = Sites()
    sites.mdblist = (401, {"error": "Invalid API key"})
    sites.wikidata = (200, _sparql(_row("93%", method=TOMATOMETER)))
    logs: list[str] = []
    raw, values = _fetch(_request(sites, key=KEY, logs=logs))
    assert sites.hosts() == ["api.mdblist.com", "query.wikidata.org"]
    assert raw is not None and raw["provider"] == "wikidata"
    assert values["ratings"][0]["rating"] == 93
    assert any("HTTP 401" in line and "Wikidata" in line for line in logs)


def test_mdblist_without_tomatoes_falls_back() -> None:
    sites = Sites()
    sites.mdblist = (200, {"ratings": [{"source": "imdb", "value": 9.3}]})
    sites.wikidata = (200, _sparql(_row("60%")))
    raw, _values = _fetch(_request(sites, key=KEY))
    assert raw is not None and raw["provider"] == "wikidata"


def test_mdblist_transport_error_falls_back_without_key_in_log() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.mdblist.com":
            raise httpx.ConnectError(f"boom {request.url}", request=request)
        return httpx.Response(200, json=_sparql(_row("90%")))

    logs: list[str] = []
    request = _request(Sites(), key=KEY, logs=logs)
    request = FetchRequest(
        services=request.services,
        data_type_id=request.data_type_id,
        config=request.config,
        ids=request.ids,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        log=request.log,
    )
    raw = ratings_rt.FETCHER.fetch(request)
    assert raw is not None and raw["provider"] == "wikidata"
    assert all(KEY not in line for line in logs)


def test_key_not_in_snapshot_or_log() -> None:
    sites = Sites()
    sites.mdblist = (200, {"ratings": [{"source": "tomatoes", "value": 89, "votes": 150}]})
    logs: list[str] = []
    raw, _values = _fetch(_request(sites, key=KEY, logs=logs))
    assert KEY not in repr(raw)
    assert all(KEY not in line for line in logs)

    sites = Sites()
    sites.mdblist = (401, {})
    sites.wikidata = (200, _sparql(_row("90%")))
    logs = []
    raw, _values = _fetch(_request(sites, key=KEY, logs=logs))
    assert KEY not in repr(raw)
    assert all(KEY not in line for line in logs)


def test_key_param_is_masked_by_host_log() -> None:
    from mortis.plugins.host import mask_url

    assert KEY not in mask_url(f"https://api.mdblist.com/imdb/movie/{IMDB}?apikey={KEY}")


def test_without_imdb_id_returns_none() -> None:
    sites = Sites()
    logs: list[str] = []
    assert ratings_rt.FETCHER.fetch(_request(sites, ids={"tmdb": "278"}, logs=logs)) is None
    assert sites.requests == []
    assert any("IMDb" in line for line in logs)


def test_malformed_imdb_id_not_sent() -> None:
    sites = Sites()
    assert ratings_rt.FETCHER.fetch(_request(sites, ids={"imdb": 'tt1" } #'})) is None
    assert sites.requests == []


def test_check_and_settings() -> None:
    assert ratings_rt.check(AppConfig(), Secrets()) is None
    off = AppConfig(sources={"ratings_rt": {"enabled": False}})
    assert ratings_rt.check(off, Secrets()) == "已关闭"
    assert ratings_rt.settings_of(AppConfig(sources={"ratings_rt": {"enabled": "x"}})).enabled
    assert ratings_rt.secrets_of(Secrets()).mdblist_api_key == ""


def test_provider_name() -> None:
    assert ratings_rt.provider_name(Secrets()) == "Wikidata"
    blank = Secrets(sources={"ratings_rt": {"mdblist_api_key": "  "}})
    assert ratings_rt.provider_name(blank) == "Wikidata"
    filled = Secrets(sources={"ratings_rt": {"mdblist_api_key": KEY}})
    assert ratings_rt.provider_name(filled) == "MDBList"
