import httpx2 as httpx

from mortis.config.models import TvScrapeConfig
from mortis.sources.tmdb.client import TmdbClient
from mortis.sources.tmdb.tv import (
    episode_candidates,
    fetch_season,
    fetch_show,
    season_candidates,
    show_candidates,
    show_external_ids,
)
from tests.fakes import FakeTmdb, load_fixture

IMG = "https://image.tmdb.org/t/p"


def test_show_candidates() -> None:
    c = show_candidates(load_fixture("tv_80968.json"), TvScrapeConfig())
    assert c["title"] == "毒枭：墨西哥"
    assert c["original_title"] == "Narcos: Mexico"
    assert c["release_date"] == "2018-11-16"
    assert c["status"] == "已完结"
    assert c["runtime"] == 69
    assert c["certification"] == "TV-MA"
    assert c["ratings"][0]["vote_count"] == 1569
    assert c["genres"] == ["剧情", "犯罪"]
    assert c["countries"] == ["美国"]
    assert c["studios"][:2] == ["Netflix", "Gaumont"]
    assert c["trailer"].startswith("https://www.youtube.com/watch?v=")
    assert c["actors"][0] == {
        "tmdb_id": 8688,
        "name": "迭戈·卢纳",
        "original_name": "Diego Luna",
        "character": "Miguel Ángel Félix Gallardo",
        "photo_url": f"{IMG}/w185/qwbjcyDN37OHiln4a6P4LP31o7F.jpg",
    }
    assert c["poster"][0]["url"].startswith(f"{IMG}/w780/")
    assert c["clearlogo"][0]["language"] == "zh"
    assert all(image["language"] is None for image in c["keyart"])


def test_show_runtime_and_status_fallbacks() -> None:
    raw = {"episode_run_time": [], "last_episode_to_air": {"runtime": 52}, "status": "Unknown"}
    c = show_candidates(raw, TvScrapeConfig())
    assert (c["runtime"], c["status"]) == (52, "Unknown")
    assert show_candidates({"episode_run_time": [45]}, TvScrapeConfig())["runtime"] == 45


def test_season_and_episode_candidates() -> None:
    raw = load_fixture("tv_80968_season_1.json")
    season = season_candidates(raw, TvScrapeConfig())
    assert (season["title"], season["release_date"]) == ("第 1 季", "2018-11-16")
    assert season["poster"][0]["url"] == f"{IMG}/w780/nd6btFNw6kly3BuxojYAhnIwGg.jpg"

    episode = episode_candidates(raw["episodes"][0], TvScrapeConfig())
    assert (episode["title"], episode["release_date"]) == ("卡美洛", "2018-11-16")
    assert episode["thumb"][0]["url"] == f"{IMG}/w300/fBodZTlh8d3SfJtHzGtKQjfApdX.jpg"
    assert [p["name"] for p in episode["directors"]] == ["Josef Kubota Wladyka"]
    assert [p["name"] for p in episode["writers"]] == ["Eric Newman", "Clayton Trussell"]
    assert episode["actors"][0]["character"] == "Verdin"


def test_season_poster_falls_back_to_poster_path() -> None:
    season = season_candidates({"name": "第 2 季", "poster_path": "/s2.jpg"}, TvScrapeConfig())
    assert season["poster"][0]["url"] == f"{IMG}/w780/s2.jpg"


def test_still_size_and_missing_still() -> None:
    episode = {"name": "第 1 集", "still_path": "/still.jpg"}
    config = TvScrapeConfig(still_size="original")
    assert episode_candidates(episode, config)["thumb"][0]["url"] == f"{IMG}/original/still.jpg"
    assert "thumb" not in episode_candidates({"name": "第 1 集"}, config)


def test_show_external_ids() -> None:
    assert show_external_ids(load_fixture("tv_80968.json")) == {
        "imdb": "tt8714904",
        "tvdb": "353232",
    }
    assert show_external_ids({}) == {}


def test_fetch_params() -> None:
    fake = FakeTmdb()
    fake.add("/3/tv/80968", load_fixture("tv_80968.json"))
    fake.add("/3/tv/80968/season/1", load_fixture("tv_80968_season_1.json"))
    tmdb = TmdbClient(
        httpx.Client(transport=fake.transport), "k", language="zh-CN", include_adult=True
    )
    assert fetch_show(tmdb, 80968, TvScrapeConfig())["id"] == 80968
    params = fake.requests[-1].url.params
    assert params["append_to_response"] == (
        "aggregate_credits,images,content_ratings,keywords,videos,external_ids"
    )
    assert params["include_image_language"] == "zh,en,null"
    before = len(fake.requests)
    season = fetch_season(tmdb, 80968, 1, TvScrapeConfig(), "zh")
    assert len(season["episodes"]) == 10
    assert len(fake.requests) == before + 1
    assert fake.requests[-1].url.params["append_to_response"] == "images"
    assert season["original_names"]["1"] == season["episodes"][0]["name"]

    season = fetch_season(tmdb, 80968, 1, TvScrapeConfig(), "en")
    assert fake.requests[-2].url.params["language"] == "zh-CN"
    assert fake.requests[-1].url.params["language"] == "en"
    assert "append_to_response" not in fake.requests[-1].url.params
    assert set(season["original_names"]) == {str(n) for n in range(1, 11)}
    episode = episode_candidates(season["episodes"][0], TvScrapeConfig(), "Camelot")
    assert episode["original_title"] == "Camelot"
