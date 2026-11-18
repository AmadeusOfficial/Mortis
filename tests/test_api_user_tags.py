from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.db.models import Item, Library
from mortis.items.identity import MovieRef, find_or_create_movie
from mortis.output.nfo import build_movie_nfo
from mortis.settings import Settings
from tests.fakes import FakeTmdb, load_fixture

LOCAL = "http://127.0.0.1"


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    fake = FakeTmdb()
    fake.add("/3/movie/603", load_fixture("movie_603.json"))
    fake.add("/3/collection/2344", load_fixture("collection_2344.json"))
    fake.add("/3/collection/2344/images", load_fixture("collection_2344_images.json"))
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


@pytest.fixture
def ids(app: FastAPI, client: TestClient, tmp_path: Path) -> dict[str, int]:
    root = tmp_path / "电影"
    root.mkdir()
    library = client.post(
        "/api/libraries", json={"name": "电影", "data_type_id": "movie", "paths": [str(root)]}
    ).json()
    with app.state.session_factory() as session:
        lib = session.get(Library, library["id"])
        matrix, _ = find_or_create_movie(session, lib, MovieRef(603, "The Matrix"))
        heat, _ = find_or_create_movie(session, lib, MovieRef(949, "Heat"))
        session.commit()
        return {"library": lib.id, "matrix": matrix.id, "heat": heat.id}


def _put(client: TestClient, item_id: int, names: list[str]) -> list[str]:
    response = client.put(f"/api/items/{item_id}/user-tags", json={"names": names})
    assert response.status_code == 200, response.text
    return response.json()["names"]


def _tags(client: TestClient, data_type: str = "movie") -> dict[str, int]:
    rows = client.get(f"/api/data-types/{data_type}/user-tags").json()
    return {row["name"]: row["count"] for row in rows}


def test_tags_are_cleaned_deduped_and_kept_per_type(
    client: TestClient, ids: dict[str, int]
) -> None:
    assert _put(client, ids["matrix"], [" 收藏 ", "Sci-Fi", "sci-fi", "收藏"]) == ["收藏", "Sci-Fi"]
    assert _put(client, ids["heat"], ["SCI-FI", "重看"]) == ["Sci-Fi", "重看"]
    assert _tags(client) == {"收藏": 1, "Sci-Fi": 2, "重看": 1}
    assert _tags(client, "tv") == {}
    detail = client.get(f"/api/items/{ids['matrix']}").json()
    assert detail["user_tags"] == ["收藏", "Sci-Fi"]
    wall = client.get(f"/api/libraries/{ids['library']}/items").json()
    assert all("user_tags" not in row for row in wall)
    too_long = client.put(f"/api/items/{ids['matrix']}/user-tags", json={"names": ["长" * 41]})
    assert too_long.status_code == 422
    blank = client.put(f"/api/items/{ids['matrix']}/user-tags", json={"names": ["  "]})
    assert blank.status_code == 422
    assert _put(client, ids["matrix"], ["收藏"]) == ["收藏"]


def test_rescrape_keeps_user_tags(app: FastAPI, client: TestClient, ids: dict[str, int]) -> None:
    _put(client, ids["matrix"], ["收藏"])
    client.post(f"/api/items/{ids['matrix']}/scrape")
    app.state.task_runner.run_until_idle()
    detail = client.get(f"/api/items/{ids['matrix']}").json()
    assert detail["title"] == "黑客帝国"
    assert detail["user_tags"] == ["收藏"]
    with app.state.session_factory() as session:
        item = session.get(Item, ids["matrix"])
        config = app.state.config_store.load_config().types.movie
        nfo = build_movie_nfo(session, item, config).decode()
    assert "<tag>收藏</tag>" in nfo
    _put(client, ids["matrix"], [])
    with app.state.session_factory() as session:
        nfo = build_movie_nfo(session, session.get(Item, ids["matrix"]), config).decode()
    assert "收藏" not in nfo


def test_rename_merge_and_delete(client: TestClient, ids: dict[str, int]) -> None:
    _put(client, ids["matrix"], ["收藏", "珍藏"])
    _put(client, ids["heat"], ["珍藏", "重看"])
    by_name = {
        row["name"]: row["id"] for row in client.get("/api/data-types/movie/user-tags").json()
    }
    conflict = client.patch(f"/api/user-tags/{by_name['珍藏']}", json={"name": "收藏"})
    assert conflict.status_code == 409
    renamed = client.patch(f"/api/user-tags/{by_name['重看']}", json={"name": "二刷"})
    assert renamed.json() == {"id": by_name["重看"], "name": "二刷", "count": 1}

    params = {"target_id": by_name["收藏"], "source_ids": [by_name["珍藏"]]}
    assert client.get("/api/user-tags/merge/impact", params=params).json() == {"count": 2}
    body = {"target_id": by_name["收藏"], "source_ids": [by_name["珍藏"]], "expected_count": 1}
    assert client.post("/api/user-tags/merge", json=body).status_code == 409
    merged = client.post("/api/user-tags/merge", json={**body, "expected_count": 2})
    assert merged.json() == {"id": by_name["收藏"], "name": "收藏", "count": 2}
    assert client.get(f"/api/items/{ids['matrix']}").json()["user_tags"] == ["收藏"]
    assert client.get(f"/api/items/{ids['heat']}").json()["user_tags"] == ["收藏", "二刷"]

    tag = by_name["收藏"]
    assert client.get(f"/api/user-tags/{tag}/impact").json() == {"count": 2}
    wrong = client.request("DELETE", f"/api/user-tags/{tag}", json={"expected_count": 1})
    assert wrong.status_code == 409
    done = client.request("DELETE", f"/api/user-tags/{tag}", json={"expected_count": 2})
    assert done.status_code == 204
    assert _tags(client) == {"二刷": 1}
    assert client.get(f"/api/items/{ids['heat']}").json()["user_tags"] == ["二刷"]
