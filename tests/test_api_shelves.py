from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.db.models import ItemTag, Library, Tag
from mortis.items.identity import MovieRef, find_or_create_movie
from mortis.settings import Settings

LOCAL = "http://127.0.0.1"


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(Settings(data_dir=tmp_path / "data"), start_worker=False)


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
        crime = Tag(tag_kind="genre", name="犯罪")
        scifi = Tag(tag_kind="genre", name="科幻")
        keyword = Tag(tag_kind="keyword", name="黑客")
        session.add_all([crime, scifi, keyword])
        session.flush()
        session.add_all(
            [
                ItemTag(item_id=matrix.id, tag_id=crime.id, position=0),
                ItemTag(item_id=matrix.id, tag_id=scifi.id, position=0),
                ItemTag(item_id=heat.id, tag_id=crime.id, position=0),
                ItemTag(item_id=matrix.id, tag_id=keyword.id, position=0),
            ]
        )
        session.commit()
        return {
            "library": lib.id,
            "matrix": matrix.id,
            "heat": heat.id,
            "crime": crime.id,
            "scifi": scifi.id,
            "keyword": keyword.id,
        }


def test_library_genres_count_works(client: TestClient, ids: dict[str, int]) -> None:
    rows = client.get(f"/api/libraries/{ids['library']}/genres").json()
    assert sorted((row["name"], row["count"]) for row in rows) == [("犯罪", 2), ("科幻", 1)]
    assert client.get("/api/libraries/9999/genres").status_code == 404


def test_wall_genre_filter_and_shelves(client: TestClient, ids: dict[str, int]) -> None:
    wall = f"/api/libraries/{ids['library']}/items"
    both = client.get(wall, params={"tag": [ids["crime"], ids["scifi"]]}).json()
    assert [row["id"] for row in both] == [ids["matrix"]]
    assert client.get(wall, params={"tag": ids["keyword"]}).status_code == 400
    assert client.get(wall, params={"tag": 9999}).status_code == 400

    shelves = client.get(
        f"/api/libraries/{ids['library']}/shelves",
        params={"tags": f"{ids['scifi']},{ids['crime']}", "sort": "title"},
    ).json()
    assert [(s["name"], s["count"]) for s in shelves] == [("科幻", 1), ("犯罪", 2)]
    assert [row["title"] for row in shelves[1]["items"]] == ["Heat", "The Matrix"]
    bad = client.get(f"/api/libraries/{ids['library']}/shelves", params={"tags": "x"})
    assert bad.status_code == 400
