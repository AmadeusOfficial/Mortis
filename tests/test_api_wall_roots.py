"""海报墙的每张卡片带上它的文件所在的库目录，页面用来按目录筛选。"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.db.models import Item, MediaFile, utc_now
from mortis.settings import Settings

LOCAL = "http://127.0.0.1"


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(Settings(data_dir=tmp_path / "data"), start_worker=False)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _library(client: TestClient, *roots: Path) -> int:
    for root in roots:
        root.mkdir()
    body = client.post(
        "/api/libraries",
        json={"name": "游戏", "data_type_id": "galgame", "paths": [str(root) for root in roots]},
    )
    assert body.status_code == 201, body.text
    return body.json()["id"]


def _item(
    app: FastAPI, library_id: int, title: str, kind: str = "work", parent: int | None = None
) -> int:
    now = utc_now()
    with app.state.session_factory() as session:
        item = Item(
            library_id=library_id,
            data_type_id="galgame",
            kind=kind,
            title=title,
            parent_id=parent,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.commit()
        return item.id


def _file(app: FastAPI, library_id: int, item_id: int | None, path: Path, kind: str) -> None:
    now = utc_now()
    with app.state.session_factory() as session:
        session.add(
            MediaFile(
                library_id=library_id,
                item_id=item_id,
                path=str(path),
                file_kind=kind,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def _roots(client: TestClient, library_id: int, **params: str) -> dict[str, list[str]]:
    response = client.get(f"/api/libraries/{library_id}/items", params=params)
    assert response.status_code == 200, response.text
    return {row["title"]: row["roots"] for row in response.json()}


def test_wall_items_carry_their_roots(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    first, second = tmp_path / "库A", tmp_path / "库B"
    library_id = _library(client, first, second)
    in_first = _item(app, library_id, "在 A")
    _file(app, library_id, in_first, first / "在 A", "game")
    in_second = _item(app, library_id, "在 B")
    _file(app, library_id, in_second, second / "系列" / "在 B", "game")
    _item(app, library_id, "没有文件")

    collection = _item(app, library_id, "合集", kind="collection")
    part_a = _item(app, library_id, "合集里的 A", parent=collection)
    _file(app, library_id, part_a, first / "合集里的 A", "game")
    part_b = _item(app, library_id, "合集里的 B", parent=collection)
    _file(app, library_id, part_b, second / "合集里的 B", "game")
    _file(app, library_id, None, first / "没认出", "game")

    assert _roots(client, library_id) == {
        "在 A": [str(first)],
        "在 B": [str(second)],
        "没有文件": [],
        "合集": [str(first), str(second)],
    }
    flat = _roots(client, library_id, flat="true")
    assert flat["合集里的 A"] == [str(first)]
    assert flat["合集里的 B"] == [str(second)]


def test_roots_follow_nested_files(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    first, second = tmp_path / "剧A", tmp_path / "剧B"
    library_id = _library(client, first, second)
    show = _item(app, library_id, "剧")
    season = _item(app, library_id, "第 1 季", kind="season", parent=show)
    episode = _item(app, library_id, "第 1 集", kind="episode", parent=season)
    _file(app, library_id, episode, second / "剧" / "Season 1" / "S01E01.mkv", "video")
    assert _roots(client, library_id) == {"剧": [str(second)]}


def test_root_match_needs_a_folder_boundary(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    short, longer = tmp_path / "游戏", tmp_path / "游戏2"
    library_id = _library(client, short, longer)
    game = _item(app, library_id, "在游戏2")
    _file(app, library_id, game, Path(str(longer).upper()) / "某作", "game")
    assert _roots(client, library_id) == {"在游戏2": [str(longer)]}
