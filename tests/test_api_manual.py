from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.db.models import MediaFile, Task, utc_now
from mortis.settings import Settings
from tests.fakes import FakeTmdb

LOCAL = "http://127.0.0.1"


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    fake = FakeTmdb()
    heat = {"id": 949, "title": "盗火线", "original_title": "Heat", "release_date": "1995-12-15"}
    fake.add("/3/movie/949", heat)
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _library(client: TestClient, root: Path, data_type: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    body = {"name": root.name, "data_type_id": data_type, "paths": [str(root)]}
    response = client.post("/api/libraries", json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _add_files(app: FastAPI, library_id: int, paths: dict[str, tuple[Path, str]]) -> dict[str, int]:
    now = utc_now()
    ids: dict[str, int] = {}
    with app.state.session_factory() as session:
        for key, (path, kind) in paths.items():
            if kind == "game":
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.write_bytes(b"x")
            row = MediaFile(
                library_id=library_id,
                path=str(path),
                file_kind=kind,
                size=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            ids[key] = row.id
        session.commit()
    return ids


@pytest.fixture
def files(app: FastAPI, client: TestClient, tmp_path: Path) -> dict[str, int]:
    root = tmp_path / "电影"
    folder = root / "B站下载 2024"
    folder.mkdir(parents=True)
    library = _library(client, root, "movie")
    ids = _add_files(
        app,
        library,
        {
            "cd1": (folder / "video.cd1.mp4", "video"),
            "cd2": (folder / "video.cd2.mp4", "video"),
            "sub": (folder / "video.srt", "subtitle"),
            "loose": (root / "Other.2001.mkv", "video"),
        },
    )
    return {"library": library, **ids}


def _names(rows: list[dict]) -> list[str]:
    return [Path(r["path"]).name for r in rows]


def _field(detail: dict[str, Any], key: str) -> dict[str, Any]:
    for section in detail["sections"]:
        for field in section["fields"]:
            if field["key"] == key:
                return field
    raise AssertionError(f"没有字段 {key}")


def _wall(client: TestClient, library_id: int) -> dict[int, dict[str, Any]]:
    rows = client.get(f"/api/libraries/{library_id}/items").json()
    return {row["id"]: row for row in rows}


def test_manual_movie_takes_parts_and_locks_filled_fields(
    client: TestClient, app: FastAPI, files: dict[str, int]
) -> None:
    body = {"title": "  某个 B 站视频 ", "overview": "自己写的简介"}
    response = client.post(f"/api/media-files/{files['cd1']}/manual", json=body)
    assert response.status_code == 200, response.text
    item_id = response.json()["item_id"]

    with app.state.session_factory() as session:
        for key in ("cd1", "cd2", "sub"):
            assert session.get(MediaFile, files[key]).item_id == item_id
        assert session.get(MediaFile, files["loose"]).item_id is None
    assert _names(client.get("/api/media-files/unmatched").json()) == ["Other.2001.mkv"]

    detail = client.get(f"/api/items/{item_id}").json()
    assert detail["is_manual"] is True
    assert detail["title"] == "某个 B 站视频"
    assert detail["external_ids"] == {}
    for key in ("title", "overview"):
        field = _field(detail, key)
        assert (field["source"], field["locked"]) == ("manual", True), key
    assert _field(detail, "overview")["value"] == "自己写的简介"
    release = _field(detail, "release_date")
    assert (release["value"], release["source"], release["locked"]) == (None, None, False)
    with app.state.session_factory() as session:
        assert session.scalars(select(Task).where(Task.task_type == "scrape")).all() == []


def test_wall_marks_manual_items(client: TestClient, files: dict[str, int]) -> None:
    manual = client.post(
        f"/api/media-files/{files['cd1']}/manual",
        json={"title": "手动片", "release_date": "2024-05-01"},
    ).json()["item_id"]
    bound = client.post(
        f"/api/media-files/{files['loose']}/bind", json={"tmdb_id": 949, "title": "盗火线"}
    ).json()["item_id"]
    wall = _wall(client, files["library"])
    assert (wall[manual]["is_manual"], wall[manual]["is_scraped"]) == (True, False)
    assert wall[manual]["year"] == 2024
    assert wall[bound]["is_manual"] is False
    assert client.get(f"/api/items/{bound}").json()["is_manual"] is False


def test_manual_item_refuses_scrape_until_rematched(
    client: TestClient, files: dict[str, int]
) -> None:
    item_id = client.post(
        f"/api/media-files/{files['cd1']}/manual", json={"title": "手动片"}
    ).json()["item_id"]
    response = client.post(f"/api/items/{item_id}/scrape")
    assert response.status_code == 422
    assert response.json()["detail"] == "手动条目没有来源编号，要刮削请先重新匹配"

    rematched = client.post(f"/api/items/{item_id}/rematch", json={"tmdb_id": 949})
    assert rematched.status_code == 202, rematched.text
    assert client.get(f"/api/items/{item_id}").json()["is_manual"] is False
    assert _wall(client, files["library"])[item_id]["is_manual"] is False


def test_manual_rejects_bound_file_and_blank_title(
    client: TestClient, files: dict[str, int]
) -> None:
    subtitle = client.post(f"/api/media-files/{files['sub']}/manual", json={"title": "x"})
    assert subtitle.status_code == 422
    assert subtitle.json()["detail"] == "只能给视频文件或游戏目录建条目"
    first = client.post(f"/api/media-files/{files['cd1']}/manual", json={"title": "手动片"})
    assert first.status_code == 200
    again = client.post(f"/api/media-files/{files['cd2']}/manual", json={"title": "又一个"})
    assert again.status_code == 409
    blank = client.post(f"/api/media-files/{files['loose']}/manual", json={"title": "  "})
    assert blank.status_code == 422
    assert blank.json()["detail"] == "片名不能为空"
    missing = client.post("/api/media-files/99999/manual", json={"title": "x"})
    assert missing.status_code == 404


def test_manual_galgame_binds_only_the_folder(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "游戏"
    library = _library(client, root, "galgame")
    ids = _add_files(
        app,
        library,
        {"game": (root / "同人小游戏", "game"), "other": (root / "另一个游戏", "game")},
    )
    response = client.post(
        f"/api/media-files/{ids['game']}/manual",
        json={"title": "同人小游戏", "original_title": "Doujin", "overview": "简介"},
    )
    assert response.status_code == 200, response.text
    item_id = response.json()["item_id"]
    detail = client.get(f"/api/items/{item_id}").json()
    assert detail["is_manual"] is True
    assert detail["data_type_id"] == "galgame"
    assert _field(detail, "original_title")["source"] == "manual"
    assert _names(client.get("/api/media-files/unmatched").json()) == ["另一个游戏"]
    with app.state.session_factory() as session:
        assert session.get(MediaFile, ids["other"]).item_id is None
    scrape = client.post(f"/api/items/{item_id}/scrape")
    assert scrape.status_code == 422
    assert scrape.json()["detail"] == "手动条目没有来源编号，要刮削请先重新匹配"
