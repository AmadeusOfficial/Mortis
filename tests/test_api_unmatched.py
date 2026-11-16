from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.db.models import MediaFile, utc_now
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


@pytest.fixture
def files(app: FastAPI, client: TestClient, tmp_path: Path) -> dict[str, int]:
    root = tmp_path / "电影"
    folder = root / "热火 1995"
    folder.mkdir(parents=True)
    library = client.post(
        "/api/libraries", json={"name": "电影", "data_type_id": "movie", "paths": [str(root)]}
    ).json()
    now = utc_now()
    paths = {
        "cd1": (folder / "heat.cd1.mkv", "video"),
        "cd2": (folder / "heat.cd2.mkv", "video"),
        "sub": (folder / "heat.srt", "subtitle"),
        "loose": (root / "Other.2001.mkv", "video"),
    }
    ids = {"library": library["id"]}
    with app.state.session_factory() as session:
        for key, (path, kind) in paths.items():
            path.write_bytes(b"x")
            row = MediaFile(
                library_id=library["id"],
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


def _names(rows: list[dict]) -> list[str]:
    return [Path(r["path"]).name for r in rows]


def test_unmatched_lists_videos_with_guesses(client: TestClient, files: dict[str, int]) -> None:
    rows = client.get("/api/media-files/unmatched").json()
    assert _names(rows) == ["Other.2001.mkv", "heat.cd1.mkv", "heat.cd2.mkv"]
    guesses = {Path(r["path"]).name: (r["guess_title"], r["guess_year"]) for r in rows}
    assert guesses["heat.cd1.mkv"] == ("热火", 1995)
    assert guesses["Other.2001.mkv"] == ("Other", 2001)
    assert rows[0]["library_name"] == "电影"
    only = client.get("/api/media-files/unmatched", params={"library_id": 999}).json()
    assert only == []


def test_bind_takes_parts_and_subtitles(
    client: TestClient, app: FastAPI, files: dict[str, int]
) -> None:
    body = {
        "tmdb_id": 949,
        "title": "盗火线",
        "original_title": "Heat",
        "release_date": "1995-12-15",
    }
    response = client.post(f"/api/media-files/{files['cd1']}/bind", json=body)
    assert response.status_code == 202
    item_id = response.json()["item_id"]
    with app.state.session_factory() as session:
        for key in ("cd1", "cd2", "sub"):
            assert session.get(MediaFile, files[key]).item_id == item_id
        assert session.get(MediaFile, files["loose"]).item_id is None
    rows = client.get("/api/media-files/unmatched").json()
    assert _names(rows) == ["Other.2001.mkv"]
    again = client.post(
        f"/api/media-files/{files['cd2']}/bind", json={"tmdb_id": 949, "title": "x"}
    )
    assert again.status_code == 409
    app.state.task_runner.run_until_idle()
    assert client.get(f"/api/items/{item_id}").json()["title"] == "盗火线"


def test_bind_existing_movie_reuses_item(client: TestClient, files: dict[str, int]) -> None:
    body = {"tmdb_id": 949, "title": "盗火线"}
    first = client.post(f"/api/media-files/{files['cd1']}/bind", json=body).json()
    second = client.post(f"/api/media-files/{files['loose']}/bind", json=body).json()
    assert first["item_id"] == second["item_id"]


def test_bind_rejects_subtitles(client: TestClient, files: dict[str, int]) -> None:
    response = client.post(
        f"/api/media-files/{files['sub']}/bind", json={"tmdb_id": 949, "title": "x"}
    )
    assert response.status_code == 422
