from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.config.models import MovieScrapeConfig, Secrets, TvScrapeConfig
from mortis.settings import Settings
from tests.fakes import FakeTmdb, hit, tv_hit

LOCAL = "http://127.0.0.1"


@pytest.fixture
def fake() -> FakeTmdb:
    return FakeTmdb()


@pytest.fixture
def client(tmp_path: Path, fake: FakeTmdb) -> Iterator[TestClient]:
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _set_key(client: TestClient) -> None:
    client.app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))


def test_search_needs_key(client: TestClient) -> None:
    response = client.get("/api/tmdb/search/movie", params={"query": "x"})
    assert (response.status_code, response.json()["detail"]) == (400, "还没有填 TMDB API Key")


def test_search_returns_hits_with_proxy_poster(client: TestClient, fake: FakeTmdb) -> None:
    _set_key(client)
    fake.add_search("Heat", [hit(949, "盗火线", "1995-12-15", "Heat", poster="/p.jpg")])
    body = client.get("/api/tmdb/search/movie", params={"query": " Heat "}).json()
    assert body == [
        {
            "tmdb_id": 949,
            "title": "盗火线",
            "original_title": "Heat",
            "release_date": "1995-12-15",
            "year": 1995,
            "overview": "",
            "poster_url": "/api/tmdb/image/w185/p.jpg",
        }
    ]
    assert fake.requests[-1].url.params["language"] == "zh-CN"
    assert fake.requests[-1].url.params["include_adult"] == "true"


def test_blank_query_rejected(client: TestClient) -> None:
    _set_key(client)
    assert client.get("/api/tmdb/search/movie", params={"query": "  "}).status_code == 422


def test_tv_search_uses_tv_settings(client: TestClient, fake: FakeTmdb) -> None:
    _set_key(client)
    store = client.app.state.config_store
    store.save_section("types.tv", TvScrapeConfig(language="ja-JP", include_adult=False))
    fake.add_search("カイジ", [tv_hit(42951, "赌博默示录", "2007-10-02")])
    assert client.get("/api/tmdb/search/tv", params={"query": "カイジ"}).status_code == 200
    assert fake.requests[-1].url.params["language"] == "ja-JP"
    assert fake.requests[-1].url.params["include_adult"] == "false"


def test_movie_search_uses_movie_settings(client: TestClient, fake: FakeTmdb) -> None:
    _set_key(client)
    store = client.app.state.config_store
    store.save_section("types.tv", TvScrapeConfig(language="ja-JP"))
    store.save_section("types.movie", MovieScrapeConfig(language="en-US"))
    fake.add_search("Heat", [hit(949, "Heat", "1995-12-15")])
    assert client.get("/api/tmdb/search/movie", params={"query": "Heat"}).status_code == 200
    assert fake.requests[-1].url.params["language"] == "en-US"


def test_image_proxy(client: TestClient, fake: FakeTmdb) -> None:
    response = client.get("/api/tmdb/image/w185/p.jpg")
    assert response.status_code == 200
    assert response.content == b"img:/t/p/w185/p.jpg"
    assert response.headers["content-type"].startswith("image/")
    assert client.get("/api/tmdb/image/w9999/p.jpg").status_code == 404
    assert client.get("/api/tmdb/image/w185/..%2Fx.jpg").status_code == 404
    fake.image_status = 404
    assert client.get("/api/tmdb/image/w185/q.jpg").status_code == 502
