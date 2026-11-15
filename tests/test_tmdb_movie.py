import httpx2 as httpx

from mortis.config.models import MovieScrapeConfig
from mortis.sources.tmdb.client import TmdbClient
from mortis.sources.tmdb.movie import (
    collection_candidates,
    fetch_collection,
    fetch_movie,
    movie_candidates,
    movie_external_ids,
)
from tests.fakes import FakeTmdb, load_fixture

IMG = "https://image.tmdb.org/t/p"


def _image(size: str, path: str, language: str | None, width: int, height: int) -> dict:
    return {"url": f"{IMG}/{size}{path}", "language": language, "width": width, "height": height}


def test_movie_candidates() -> None:
    c = movie_candidates(load_fixture("movie_603.json"), MovieScrapeConfig())
    assert c["title"] == "黑客帝国"
    assert c["original_title"] == "The Matrix"
    assert c["tagline"] == "欢迎来到真实的世界"
    assert c["release_date"] == "1999-03-30"
    assert c["runtime"] == 136
    assert c["certification"] == "R"
    assert c["ratings"] == [
        {"source": "tmdb", "rating": 8.2, "max_rating": 10.0, "vote_count": 26000}
    ]
    assert c["genres"] == ["动作", "科幻"]
    assert c["countries"] == ["United States of America"]
    assert c["studios"] == ["Village Roadshow Pictures", "Groucho II Film Partnership"]
    assert c["keywords"] == ["simulated reality", "artificial intelligence"]
    assert c["spoken_languages"] == ["English"]
    assert c["trailer"] == "https://www.youtube.com/watch?v=zh-trailer"
    assert [p["name"] for p in c["directors"]] == ["Lana Wachowski", "Lilly Wachowski"]
    assert [p["name"] for p in c["writers"]] == ["Lana Wachowski", "Lilly Wachowski"]
    assert [p["name"] for p in c["producers"]] == ["Joel Silver", "Lana Wachowski"]
    assert c["actors"] == [
        {
            "tmdb_id": 6384,
            "name": "基努·里维斯",
            "original_name": "Keanu Reeves",
            "character": "Neo / Thomas Anderson",
            "photo_url": f"{IMG}/w185/keanu.jpg",
        },
        {
            "tmdb_id": 2975,
            "name": "劳伦斯·菲什伯恩",
            "original_name": "Laurence Fishburne",
            "character": "Morpheus",
            "photo_url": None,
        },
    ]
    assert c["poster"][0] == _image("w780", "/zh-poster.jpg", "zh", 1000, 1500)
    assert [i["language"] for i in c["poster"]] == ["zh", "en", None]
    assert c["fanart"][0] == _image("original", "/backdrop-none.jpg", None, 1920, 1080)
    assert c["clearlogo"][0]["url"] == f"{IMG}/original/logo-zh.png"
    assert c["keyart"] == [_image("w780", "/textless-poster.jpg", None, 2000, 3000)]
    assert "top250" not in c and "sort_title" not in c


def test_fanart_without_textless_preference() -> None:
    config = MovieScrapeConfig(prefer_textless_fanart=False, fanart_size="w1280")
    c = movie_candidates(load_fixture("movie_603.json"), config)
    assert c["fanart"][0]["url"] == f"{IMG}/w1280/backdrop-en.jpg"


def test_empty_values_are_dropped() -> None:
    raw = {
        "id": 1,
        "title": "片",
        "overview": "",
        "tagline": None,
        "vote_count": 0,
        "genres": [],
        "credits": {"cast": [], "crew": []},
    }
    assert movie_candidates(raw, MovieScrapeConfig()) == {"title": "片"}


def test_other_certification_country() -> None:
    c = movie_candidates(
        load_fixture("movie_603.json"), MovieScrapeConfig(certification_country="DE")
    )
    assert c["certification"] == "16"


def test_external_ids() -> None:
    assert movie_external_ids(load_fixture("movie_603.json")) == {"imdb": "tt0133093"}
    assert movie_external_ids({"id": 1}) == {}


def test_collection_candidates() -> None:
    raw = load_fixture("collection_2344.json")
    raw["images"] = load_fixture("collection_2344_images.json")
    c = collection_candidates(raw, MovieScrapeConfig())
    assert c["title"] == "黑客帝国（系列）"
    assert c["original_title"] == "The Matrix Collection"
    assert c["overview"] == "黑客帝国系列电影。"
    assert c["poster"][0]["url"] == f"{IMG}/w780/c-poster.jpg"
    assert c["fanart"][0]["url"] == f"{IMG}/original/c-backdrop.jpg"


def test_fetch_params() -> None:
    fake = FakeTmdb()
    fake.add("/3/movie/603", load_fixture("movie_603.json"))
    fake.add("/3/collection/2344", load_fixture("collection_2344.json"))
    fake.add("/3/collection/2344/images", load_fixture("collection_2344_images.json"))
    tmdb = TmdbClient(
        httpx.Client(transport=fake.transport), "k", language="zh-CN", include_adult=True
    )
    raw = fetch_movie(tmdb, 603, MovieScrapeConfig())
    assert raw["id"] == 603
    params = fake.requests[-1].url.params
    assert params["language"] == "zh-CN"
    assert params["append_to_response"] == (
        "credits,images,release_dates,keywords,videos,external_ids"
    )
    assert params["include_image_language"] == "zh,en,null"
    collection = fetch_collection(tmdb, 2344, MovieScrapeConfig())
    assert collection["images"]["posters"][0]["file_path"] == "/c-poster-en.jpg"
