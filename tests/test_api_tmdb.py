from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.settings import Settings
from tests.fakes import FakeTmdb, hit

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
    client.app.state.config_store.save_secrets(Secrets(tmdb_api_key="k"))


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


def test_image_proxy(client: TestClient, fake: FakeTmdb) -> None:
    response = client.get("/api/tmdb/image/w185/p.jpg")
    assert response.status_code == 200
    assert response.content == b"img:/t/p/w185/p.jpg"
    assert response.headers["content-type"].startswith("image/")
    assert client.get("/api/tmdb/image/w9999/p.jpg").status_code == 404
    assert client.get("/api/tmdb/image/w185/..%2Fx.jpg").status_code == 404
    fake.image_status = 404
    assert client.get("/api/tmdb/image/w185/q.jpg").status_code == 502
