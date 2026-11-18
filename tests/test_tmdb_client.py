from collections.abc import Callable
from pathlib import Path

import httpx2 as httpx
import pytest

from mortis.config.models import Secrets
from mortis.config.store import ConfigStore
from mortis.organize.names import parse_name
from mortis.services import Services
from mortis.sources.tmdb.client import (
    TmdbAuthError,
    TmdbClient,
    TmdbError,
    TmdbNoKey,
    TmdbNotFound,
    open_tmdb,
)
from mortis.sources.tmdb.search import MovieHit, find_best, score, similarity, title_score
from tests.fakes import FakeTmdb, hit, tv_hit

Handler = Callable[[httpx.Request], httpx.Response]


def _client(
    handler: Handler,
    key: str = "k-secret",
    sleep: Callable[[float], None] = lambda _s: None,
) -> TmdbClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return TmdbClient(http, key, language="zh-CN", include_adult=True, sleep=sleep)


def test_v3_key_goes_in_query_and_v4_in_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    _client(handler).get("/movie/1", {"language": "zh-CN"})
    assert seen[0].url.params["api_key"] == "k-secret"
    _client(handler, key="eyJtoken").get("/movie/1")
    assert seen[1].headers["Authorization"] == "Bearer eyJtoken"
    assert "api_key" not in seen[1].url.params


def test_errors_are_mapped_and_key_is_redacted() -> None:
    with pytest.raises(TmdbAuthError):
        _client(lambda r: httpx.Response(401)).get("/x")
    with pytest.raises(TmdbNotFound):
        _client(lambda r: httpx.Response(404)).get("/x")
    with pytest.raises(TmdbError, match="HTTP 500"):
        _client(lambda r: httpx.Response(500)).get("/x")

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}")

    with pytest.raises(TmdbError) as caught:
        _client(broken).get("/x")
    assert "k-secret" not in caught.value.message
    assert "连不上 TMDB" in caught.value.message


def test_429_is_retried_with_retry_after() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": 1})

    assert _client(handler, sleep=sleeps.append).get("/x") == {"ok": 1}
    assert sleeps == [2.0, 2.0]


def test_open_tmdb_needs_a_key(tmp_path: Path) -> None:
    services = Services(tmp_path, ConfigStore(tmp_path))
    with pytest.raises(TmdbNoKey):
        open_tmdb(services)
    services.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    with open_tmdb(services) as tmdb:
        assert tmdb.image_url("/a.jpg", "w500") == "https://image.tmdb.org/t/p/w500/a.jpg"


def test_similarity_and_score() -> None:
    assert similarity("The Matrix", "matrix") == 1.0
    assert similarity("黑客帝国", "黑客帝国") == 1.0
    assert similarity("Alien", "Aliens") > 0.8
    movie = MovieHit(603, "黑客帝国", "The Matrix", "1999-03-30", 1999, "", None, 1.0)
    assert score(movie, "The Matrix", 1999) == 1.0
    assert score(movie, "The Matrix", 2003) == pytest.approx(0.85)
    assert score(movie, "The Matrix", None) == 1.0


def test_title_score_unifies_numbers_and_penalizes_missing_sequel_number() -> None:
    assert title_score("Royal Tramp II", "Royal Tramp 2") == 1.0
    assert title_score("A Chinese Odyssey Part 2", "A Chinese Odyssey Part Two") == 1.0
    assert title_score("Infernal Affairs II", "Infernal Affairs III") < 0.75
    assert title_score("Fight Back to School 3", "Fight Back to School") < 0.75
    assert title_score("Fight Back to School", "Fight Back to School 3") > 0.9


def test_hit_without_year_loses_a_tie() -> None:
    dated = MovieHit(1, "Shaolin Soccer", "少林足球", "2001-07-12", 2001, "", None, 15.0)
    undated = MovieHit(2, "Shaolin Soccer", "Shaolin Soccer", None, None, "", None, 0.1)
    assert score(dated, "Shaolin Soccer", None) > score(undated, "Shaolin Soccer", None)


def _bilingual(
    local: dict[str, list[dict]], english: dict[str, list[dict]], languages: list[str]
) -> httpx.MockTransport:
    """中文和英文搜索回不同的结果，记下每次搜索用的语言。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/3/search/movie":
            return httpx.Response(404, json={})
        language = request.url.params["language"]
        languages.append(language)
        table = english if language == "en-US" else local
        return httpx.Response(200, json={"results": table.get(request.url.params["query"], [])})

    return httpx.MockTransport(handler)


def test_english_file_name_matches_by_english_title() -> None:
    transport = _bilingual(
        {"Kung Fu Hustle": [hit(9470, "功夫", "2004-02-10")]},
        {"Kung Fu Hustle": [hit(9470, "Kung Fu Hustle", "2004-02-10", "功夫")]},
        [],
    )
    tmdb = TmdbClient(httpx.Client(transport=transport), "k", language="zh-CN", include_adult=True)
    match = find_best(tmdb, parse_name("Kung.Fu.Hustle"))
    assert match is not None and match.hit.tmdb_id == 9470 and match.score == 1.0
    assert match.hit.title == "功夫"


def test_english_search_is_skipped_for_chinese_queries_and_english_libraries() -> None:
    languages: list[str] = []
    transport = _bilingual({"功夫": [hit(9470, "功夫", "2004-02-10")]}, {}, languages)
    zh = TmdbClient(httpx.Client(transport=transport), "k", language="zh-CN", include_adult=True)
    find_best(zh, parse_name("功夫"))
    en = TmdbClient(httpx.Client(transport=transport), "k", language="en-US", include_adult=True)
    find_best(en, parse_name("Heat"))
    assert languages == ["zh-CN", "en-US"]


def test_same_name_films_without_year_are_left_for_manual_pick() -> None:
    fake = FakeTmdb()
    fake.add_search(
        "Flow",
        [
            hit(823219, "Flow", "2024-08-29"),
            hit(11, "Flow", "2010-05-01"),
            hit(12, "Flow", ""),
            hit(13, "Flowers", "2015-01-01"),
        ],
    )
    tmdb = TmdbClient(
        httpx.Client(transport=fake.transport), "k", language="en-US", include_adult=True
    )
    match = find_best(tmdb, parse_name("Flow"))
    assert match is not None and [r.tmdb_id for r in match.rivals] == [11]
    match = find_best(tmdb, parse_name("Flow.2024"))
    assert match is not None and match.hit.tmdb_id == 823219 and match.rivals == ()


def test_find_best_tries_variants_and_falls_back_without_year() -> None:
    fake = FakeTmdb()
    fake.add_search("黑客帝国 The Matrix", [])
    fake.add_search("黑客帝国", [hit(603, "黑客帝国", "1999-03-30", "The Matrix")])
    tmdb = TmdbClient(
        httpx.Client(transport=fake.transport), "k", language="zh-CN", include_adult=True
    )
    match = find_best(tmdb, parse_name("黑客帝国.The.Matrix.1999"))
    assert match is not None and match.hit.tmdb_id == 603 and match.score == 1.0

    fake.add_search("Heat", [hit(949, "Heat", "1995-12-15")])
    match = find_best(tmdb, parse_name("Heat.1996"))
    assert match is not None and match.hit.tmdb_id == 949
    assert any(r.url.params.get("year") == "1996" for r in fake.requests)
    assert fake.requests[-1].url.params.get("year") is None


def _kaiji(fake: FakeTmdb) -> TmdbClient:
    fake.add_search("赌博默示录", [tv_hit(42951, "赌博默示录", "2007-10-02", "逆境無頼カイジ")])
    return TmdbClient(
        httpx.Client(transport=fake.transport), "k", language="zh-CN", include_adult=True
    )


def test_tv_shortens_title_before_season_marker() -> None:
    fake = FakeTmdb()
    tmdb = _kaiji(fake)
    match = find_best(tmdb, parse_name("赌博默示录：破戒录篇 (2011)"), kind="tv")
    assert match is not None and match.hit.tmdb_id == 42951
    assert match.score >= 0.75


def test_movie_does_not_search_shortened_title() -> None:
    fake = FakeTmdb()
    tmdb = _kaiji(fake)
    match = find_best(tmdb, parse_name("赌博默示录：破戒录篇 (2011)"), kind="movie")
    assert match is None


def test_tv_folder_year_before_show_year_is_penalized() -> None:
    fake = FakeTmdb()
    tmdb = _kaiji(fake)
    match = find_best(tmdb, parse_name("赌博默示录：破戒录篇 (2001)"), kind="tv")
    assert match is not None and match.score < 0.75
