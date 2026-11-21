"""排除识别：目录写进类型的跳过目录，库里的记录去掉，磁盘文件不动。"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.types import type_config
from mortis.db.models import Item, MediaFile, utc_now
from mortis.organize.scan import scan_folders, scan_paths
from mortis.settings import Settings
from mortis.tasks import repo

LOCAL = "http://127.0.0.1"


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(Settings(data_dir=tmp_path / "data"), start_worker=False)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _library(client: TestClient, data_type_id: str, root: Path) -> int:
    root.mkdir(exist_ok=True)
    body = client.post(
        "/api/libraries",
        json={"name": data_type_id, "data_type_id": data_type_id, "paths": [str(root)]},
    )
    assert body.status_code == 201, body.text
    return body.json()["id"]


def _item(
    app: FastAPI,
    library_id: int,
    data_type_id: str,
    title: str,
    kind: str = "work",
    parent: int | None = None,
) -> int:
    now = utc_now()
    with app.state.session_factory() as session:
        item = Item(
            library_id=library_id,
            data_type_id=data_type_id,
            kind=kind,
            title=title,
            parent_id=parent,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.commit()
        return item.id


def _file(app: FastAPI, library_id: int, item_id: int | None, path: Path, kind: str) -> int:
    if kind == "game":
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    now = utc_now()
    with app.state.session_factory() as session:
        row = MediaFile(
            library_id=library_id,
            item_id=item_id,
            path=str(path),
            file_kind=kind,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


def _skip(app: FastAPI, data_type_id: str) -> list[str]:
    return type_config(app.state.config_store.load_config(), data_type_id).skip_folders


def _paths(app: FastAPI) -> list[str]:
    with app.state.session_factory() as session:
        return sorted(session.scalars(select(MediaFile.path)))


def _titles(app: FastAPI) -> list[str]:
    with app.state.session_factory() as session:
        return sorted(session.scalars(select(Item.title)))


def _exclude(client: TestClient, url: str) -> dict:
    preview = client.get(url)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    done = client.post(url, json={"entries": body["entries"]})
    assert done.status_code == 200, done.text
    assert done.json()["excluded"] is True
    return body


def test_unmatched_game_folder(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "游戏"
    library_id = _library(client, "galgame", root)
    game = root / "没认出的游戏"
    file_id = _file(app, library_id, None, game, "game")
    kept = _file(app, library_id, None, root / "别的游戏", "game")
    assert kept

    body = _exclude(client, f"/api/media-files/{file_id}/exclude")
    assert body["entries"] == [str(game)]
    assert body["file_count"] == 1
    assert body["type_name"]
    assert _skip(app, "galgame") == [str(game)]
    assert _paths(app) == [str(root / "别的游戏")]
    assert game.is_dir()
    rescanned = scan_folders([str(root)], skip_folders=_skip(app, "galgame"))
    assert [movie.name for movie in rescanned.movies] == ["别的游戏"]


def test_movie_in_own_folder(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "电影"
    library_id = _library(client, "movie", root)
    folder = root / "黑客帝国 (1999)"
    movie = _item(app, library_id, "movie", "黑客帝国")
    _file(app, library_id, movie, folder / "The.Matrix.mkv", "video")
    _file(app, library_id, movie, folder / "The.Matrix.chs.srt", "subtitle")

    body = _exclude(client, f"/api/items/{movie}/exclude")
    assert body["entries"] == [str(folder)]
    assert body["title"] == "黑客帝国"
    assert body["file_count"] == 2
    assert _skip(app, "movie") == ["MAKEMKV", str(folder)]
    assert _titles(app) == []
    assert _paths(app) == []
    assert (folder / "The.Matrix.mkv").is_file()
    assert (folder / "The.Matrix.chs.srt").is_file()


def test_movie_in_shared_folder_excludes_its_files(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "电影"
    library_id = _library(client, "movie", root)
    loose = _item(app, library_id, "movie", "盗梦空间")
    _file(app, library_id, loose, root / "Inception.2010.mkv", "video")
    other = _item(app, library_id, "movie", "异形")
    _file(app, library_id, other, root / "合集" / "Alien.1979.mkv", "video")
    unmatched = _file(app, library_id, None, root / "合集" / "Aliens.1986.mkv", "video")

    body = _exclude(client, f"/api/items/{loose}/exclude")
    assert body["entries"] == [str(root / "Inception.2010.mkv")]
    body = _exclude(client, f"/api/media-files/{unmatched}/exclude")
    assert body["entries"] == [str(root / "合集" / "Aliens.1986.mkv")]
    assert _titles(app) == ["异形"]
    assert _paths(app) == [str(root / "合集" / "Alien.1979.mkv")]

    config = app.state.config_store.load_config()
    rescanned = scan_paths(
        [str(root)],
        video_extensions=config.files.video_extensions,
        subtitle_extensions=config.files.subtitle_extensions,
        skip_folders=_skip(app, "movie"),
    )
    assert [(movie.name, movie.videos) for movie in rescanned.movies] == [
        ("合集", [str(root / "合集" / "Alien.1979.mkv")])
    ]


def test_show_with_seasons(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "剧"
    library_id = _library(client, "tv", root)
    folder = root / "某剧 (2020)"
    show = _item(app, library_id, "tv", "某剧")
    season = _item(app, library_id, "tv", "第 1 季", kind="season", parent=show)
    episode = _item(app, library_id, "tv", "第 1 集", kind="episode", parent=season)
    _file(app, library_id, episode, folder / "Season 1" / "S01E01.mkv", "video")
    _file(app, library_id, show, folder / "S00E01.mkv", "video")

    body = _exclude(client, f"/api/items/{show}/exclude")
    assert body["entries"] == [str(folder)]
    assert body["file_count"] == 2
    assert _titles(app) == []
    assert _paths(app) == []
    assert (folder / "Season 1" / "S01E01.mkv").is_file()


def test_unmatched_show_folder(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "剧"
    library_id = _library(client, "tv", root)
    folder = root / "没认出的剧"
    first = _file(app, library_id, None, folder / "01.mkv", "video")
    _file(app, library_id, None, folder / "02.mkv", "video")
    body = _exclude(client, f"/api/media-files/{first}/exclude")
    assert body["entries"] == [str(folder)]
    assert body["file_count"] == 2
    assert _paths(app) == []


def test_refusals(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "剧"
    library_id = _library(client, "tv", root)
    collection = _item(app, library_id, "tv", "系列", kind="collection")
    show = _item(app, library_id, "tv", "某剧")
    season = _item(app, library_id, "tv", "第 1 季", kind="season", parent=show)
    empty = _item(app, library_id, "tv", "没有文件")
    folder = root / "某剧"
    _file(app, library_id, show, folder / "S01E01.mkv", "video")
    stray = _file(app, library_id, None, folder / "多出来的.mkv", "video")
    matched = _file(app, library_id, show, folder / "S01E02.mkv", "video")

    for url in (
        f"/api/items/{collection}/exclude",
        f"/api/items/{season}/exclude",
        f"/api/items/{empty}/exclude",
        f"/api/items/{show}/exclude",
        f"/api/media-files/{stray}/exclude",
        f"/api/media-files/{matched}/exclude",
    ):
        response = client.get(url)
        assert response.status_code >= 400, url
        assert response.json()["detail"], url
    assert _skip(app, "tv") == ["MAKEMKV"]


def test_changed_entries_or_busy_library_are_refused(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "游戏"
    library_id = _library(client, "galgame", root)
    file_id = _file(app, library_id, None, root / "某游戏", "game")
    url = f"/api/media-files/{file_id}/exclude"

    changed = client.post(url, json={"entries": [str(root / "别的")]})
    assert changed.status_code == 409

    with app.state.session_factory() as session:
        repo.enqueue(session, "organize", {"library_id": library_id, "scrape": None})
        session.commit()
    busy = client.post(url, json={"entries": [str(root / "某游戏")]})
    assert busy.status_code == 409
    assert _skip(app, "galgame") == []
    assert _paths(app) == [str(root / "某游戏")]
