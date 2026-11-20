"""Galgame 手动合集：多选收入、移出、排序、解散、封面和来源建议。"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.db.models import Item, ItemExternalId, ItemImage, ItemSourceRecord, MediaFile, utc_now
from mortis.items.covers import collage_cells
from mortis.settings import Settings

LOCAL = "http://127.0.0.1"


def test_collage_repeats_when_fewer_than_four() -> None:
    assert collage_cells([]) == [None, None, None, None]
    assert collage_cells(["a"]) == ["a", "a", "a", "a"]
    assert collage_cells(["a", "b"]) == ["a", "b", "a", "b"]
    assert collage_cells(["a", "b", "c"]) == ["a", "b", "c", "a"]
    assert collage_cells(["a", None, "b"]) == ["a", "b", "a", "b"]
    assert collage_cells(["a", "b", "c", "d", "e"]) == ["a", "b", "c", "d"]


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(Settings(data_dir=tmp_path / "data"), start_worker=False)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _library(client: TestClient, root: Path) -> int:
    root.mkdir()
    body = client.post(
        "/api/libraries",
        json={"name": "游戏", "data_type_id": "galgame", "paths": [str(root)]},
    )
    assert body.status_code == 201, body.text
    return body.json()["id"]


def _game(
    app: FastAPI,
    library_id: int,
    title: str,
    poster: str | None = None,
    release_date: str | None = None,
) -> int:
    now = utc_now()
    with app.state.session_factory() as session:
        item = Item(
            library_id=library_id,
            data_type_id="galgame",
            kind="work",
            title=title,
            poster_path=poster,
            release_date=release_date,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.commit()
        return item.id


def test_assign_moves_and_orders(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    library_id = _library(client, tmp_path / "库")
    first = _game(app, library_id, "第一作", "posters/1.jpg", "2011-01-01")
    second = _game(app, library_id, "第二作", "posters/2.jpg", "2012-01-01")
    third = _game(app, library_id, "第三作", "posters/3.jpg", "2013-01-01")
    now = utc_now()
    with app.state.session_factory() as session:
        session.add_all(
            [
                MediaFile(
                    library_id=library_id,
                    item_id=first,
                    path=r"H:\adv\1",
                    file_kind="game",
                    created_at=now,
                    updated_at=now,
                ),
                MediaFile(
                    library_id=library_id,
                    item_id=second,
                    path=r"H:\adv\2",
                    file_kind="game",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.commit()

    created = client.post(
        f"/api/libraries/{library_id}/collections",
        json={"work_ids": [second, first], "title": "万华镜"},
    )
    assert created.status_code == 200, created.text
    collection_id = created.json()["id"]
    assert created.json()["moved"] == 0

    flat = client.get(f"/api/libraries/{library_id}/items", params={"flat": True}).json()
    by_id = {row["id"]: row for row in flat}
    assert by_id[first]["collection_id"] == collection_id
    assert by_id[third]["collection_id"] is None

    detail = client.get(f"/api/items/{collection_id}").json()
    assert [part["id"] for part in detail["parts"]] == [first, second]
    assert detail["cover_mode"] == "auto"
    assert [row["path"] for row in detail["files"]] == [r"H:\adv\1", r"H:\adv\2"]
    assert detail["cover_urls"] == [
        "/api/images/posters/1.jpg",
        "/api/images/posters/2.jpg",
        "/api/images/posters/1.jpg",
        "/api/images/posters/2.jpg",
    ]
    assert client.get(f"/api/items/{first}").json()["poster_url"] == "/api/images/posters/1.jpg"

    moved = client.post(
        f"/api/libraries/{library_id}/collections",
        json={"work_ids": [first, third], "title": "另一个"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"] == 1
    assert client.get(f"/api/items/{collection_id}").json()["parts"][0]["id"] == second

    added = client.post(
        f"/api/items/{moved.json()['id']}/members", json={"work_ids": [second]}
    )
    assert added.status_code == 200, added.text
    assert [part["id"] for part in added.json()["parts"]] == [first, second, third]
    assert client.get(f"/api/items/{collection_id}").status_code == 404


def test_reorder_remove_and_dissolve(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    library_id = _library(client, tmp_path / "库")
    games = [
        _game(app, library_id, name, release_date=date)
        for name, date in (("甲", "2011-01-01"), ("乙", "2012-01-01"), ("丙", "2013-01-01"))
    ]
    collection_id = client.post(
        f"/api/libraries/{library_id}/collections",
        json={"work_ids": list(reversed(games)), "title": "系列"},
    ).json()["id"]
    assert [
        part["id"]
        for part in client.get(f"/api/items/{collection_id}").json()["parts"]
    ] == games

    renamed = client.patch(f"/api/items/{collection_id}/title", json={"title": "新名字"})
    assert renamed.json()["title"] == "新名字"

    left = client.post(
        f"/api/items/{collection_id}/members/remove", json={"work_ids": [games[0], games[2]]}
    )
    assert left.status_code == 200
    assert [part["id"] for part in left.json()["parts"]] == [games[1]]
    assert client.get(f"/api/items/{games[0]}").json()["collection"] is None

    gone = client.post(f"/api/items/{collection_id}/dissolve")
    assert gone.json() == {"library_id": library_id}
    assert client.get(f"/api/items/{collection_id}").status_code == 404
    assert client.get(f"/api/items/{games[1]}").json()["collection"] is None


def test_cover_mode_keeps_uploads(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    library_id = _library(client, tmp_path / "库")
    game = _game(app, library_id, "独作", "posters/only.jpg")
    collection_id = client.post(
        f"/api/libraries/{library_id}/collections",
        json={"work_ids": [game], "title": "独作合集"},
    ).json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    uploaded = client.put(
        f"/api/items/{collection_id}/images/cover_1/file",
        content=png,
        headers={"Content-Type": "image/png"},
    )
    assert uploaded.status_code == 200, uploaded.text
    switched = client.put(f"/api/items/{collection_id}/cover", json={"mode": "single"})
    assert switched.json()["cover_mode"] == "single"
    assert switched.json()["cover_urls"] == []
    back = client.put(f"/api/items/{collection_id}/cover", json={"mode": "grid"})
    assert back.json()["cover_mode"] == "grid"
    assert back.json()["cover_urls"][0].startswith("/api/images/")
    assert back.json()["cover_urls"][1:] == ["/api/images/posters/only.jpg"] * 3
    with app.state.session_factory() as session:
        saved = session.scalar(select(ItemImage).where(ItemImage.item_id == collection_id))
        assert saved is not None and saved.file_path


def test_suggestions_use_saved_relations(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    library_id = _library(client, tmp_path / "库")
    current = _game(app, library_id, "第一作")
    sequel = _game(app, library_id, "第二作")
    outsider = _game(app, library_id, "别的")
    now = utc_now()
    with app.state.session_factory() as session:
        session.add_all(
            [
                ItemExternalId(item_id=current, provider="vndb", external_id="v1", is_primary=1),
                ItemExternalId(item_id=sequel, provider="vndb", external_id="v2", is_primary=1),
                ItemExternalId(item_id=outsider, provider="vndb", external_id="v9", is_primary=1),
                ItemSourceRecord(
                    item_id=current,
                    source="vndb",
                    fetched_at=now,
                    raw=json.dumps(
                        {
                            "vn": {
                                "relations": [
                                    {"id": "v2", "relation": "seq"},
                                    {"id": "v8", "relation": "ser"},
                                ]
                            }
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        session.commit()
    rows = client.get(
        f"/api/libraries/{library_id}/collection-suggestions", params={"work_id": current}
    ).json()
    assert rows == [{"id": sequel, "title": "第二作", "relation": "续作"}]


def test_movie_library_rejects_manual_collection(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "电影"
    root.mkdir()
    library_id = client.post(
        "/api/libraries", json={"name": "电影", "data_type_id": "movie", "paths": [str(root)]}
    ).json()["id"]
    response = client.post(
        f"/api/libraries/{library_id}/collections", json={"work_ids": [1], "title": "不行"}
    )
    assert response.status_code == 422


def test_flat_wall_lists_members(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    library_id = _library(client, tmp_path / "库")
    games = [_game(app, library_id, name) for name in ("甲", "乙")]
    client.post(
        f"/api/libraries/{library_id}/collections",
        json={"work_ids": games, "title": "系列"},
    )
    folded = client.get(f"/api/libraries/{library_id}/items").json()
    assert [row["kind"] for row in folded] == ["collection"]
    opened = client.get(f"/api/libraries/{library_id}/items", params={"flat": True}).json()
    assert {row["id"] for row in opened} == set(games)
