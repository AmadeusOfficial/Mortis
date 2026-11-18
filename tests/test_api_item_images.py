from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.db.models import Item, Library, MediaFile, utc_now
from mortis.items.identity import MovieRef, find_or_create_movie
from mortis.settings import Settings
from tests.fakes import FakeTmdb, load_fixture

LOCAL = "http://127.0.0.1"
IMG = "https://image.tmdb.org/t/p"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def fake() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add("/3/movie/603", load_fixture("movie_603.json"))
    fake.add("/3/collection/2344", load_fixture("collection_2344.json"))
    fake.add("/3/collection/2344/images", load_fixture("collection_2344_images.json"))
    return fake


@pytest.fixture
def app(tmp_path: Path, fake: FakeTmdb) -> FastAPI:
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
def matrix(app: FastAPI, client: TestClient, tmp_path: Path) -> int:
    root = tmp_path / "电影"
    (root / "The Matrix (1999)").mkdir(parents=True)
    video = root / "The Matrix (1999)" / "m.mkv"
    video.write_bytes(b"x")
    library = client.post(
        "/api/libraries", json={"name": "电影", "data_type_id": "movie", "paths": [str(root)]}
    ).json()
    with app.state.session_factory() as session:
        lib = session.get(Library, library["id"])
        item, _ = find_or_create_movie(session, lib, MovieRef(603, "The Matrix"))
        now = utc_now()
        session.add(
            MediaFile(
                library_id=lib.id,
                item_id=item.id,
                path=str(video),
                file_kind="video",
                size=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        item_id = item.id
    _scrape(app, client, item_id)
    return item_id


def _scrape(app: FastAPI, client: TestClient, item_id: int) -> None:
    client.post(f"/api/items/{item_id}/scrape", json={"force": False})
    app.state.task_runner.run_until_idle()


def _field(detail: dict, key: str) -> dict:
    return next(f for s in detail["sections"] for f in s["fields"] if f["key"] == key)


def _choices(client: TestClient, item_id: int, key: str) -> list[dict]:
    response = client.get(f"/api/items/{item_id}/images/{key}/choices")
    assert response.status_code == 200, response.text
    return response.json()


def test_choices_follow_ranking_and_mark_current(client: TestClient, matrix: int) -> None:
    rows = _choices(client, matrix, "poster")
    assert [row["url"] for row in rows] == [
        f"{IMG}/w780/zh-poster.jpg",
        f"{IMG}/w780/en-poster.jpg",
        f"{IMG}/w780/textless-poster.jpg",
    ]
    assert [row["selected"] for row in rows] == [True, False, False]
    assert rows[0]["source"] == "tmdb" and rows[0]["source_name"] == "TMDB"
    assert rows[0]["preview_url"] == "/api/tmdb/image/w300/zh-poster.jpg"


def test_choose_candidate_records_source_locks_and_survives_rescrape(
    app: FastAPI, client: TestClient, matrix: int
) -> None:
    before = client.get(f"/api/items/{matrix}").json()["poster_url"]
    url = f"{IMG}/w780/en-poster.jpg"
    response = client.put(f"/api/items/{matrix}/images/poster", json={"url": url})
    assert response.status_code == 200, response.text
    detail = response.json()
    field = _field(detail, "poster")
    assert (field["source"], field["locked"]) == ("tmdb", True)
    assert detail["poster_url"] != before

    wall = client.get(f"/api/libraries/{detail['library_id']}/items").json()
    assert next(row for row in wall if row["id"] == matrix)["poster_url"] == detail["poster_url"]

    _scrape(app, client, matrix)
    assert client.get(f"/api/items/{matrix}").json()["poster_url"] == detail["poster_url"]
    assert [row["selected"] for row in _choices(client, matrix, "poster")] == [False, True, False]


def test_choose_image_kind_field(client: TestClient, matrix: int) -> None:
    url = f"{IMG}/original/logo-en.png"
    detail = client.put(f"/api/items/{matrix}/images/clearlogo", json={"url": url}).json()
    field = _field(detail, "clearlogo")
    assert (field["source"], field["locked"]) == ("tmdb", True)
    assert len(field["value"]) == 1
    assert field["value"][0]["language"] == "en"


def test_url_outside_choices_is_manual(app: FastAPI, client: TestClient, matrix: int) -> None:
    url = f"{IMG}/original/someone-else.jpg"
    detail = client.put(f"/api/items/{matrix}/images/fanart", json={"url": url}).json()
    assert (_field(detail, "fanart")["source"], _field(detail, "fanart")["locked"]) == (
        "manual",
        True,
    )


def test_bad_url_and_failed_download_change_nothing(
    client: TestClient, matrix: int, fake: FakeTmdb
) -> None:
    before = client.get(f"/api/items/{matrix}").json()
    assert (
        client.put(f"/api/items/{matrix}/images/poster", json={"url": "C:/x.jpg"}).status_code
        == 422
    )
    fake.image_status = 404
    response = client.put(
        f"/api/items/{matrix}/images/poster", json={"url": f"{IMG}/original/gone.jpg"}
    )
    assert response.status_code == 422
    assert "下载失败" in response.json()["detail"]
    after = client.get(f"/api/items/{matrix}").json()
    assert after["poster_url"] == before["poster_url"]
    assert _field(after, "poster")["locked"] is False


def test_not_image_or_multi_field_rejected(client: TestClient, matrix: int) -> None:
    assert client.get(f"/api/items/{matrix}/images/title/choices").status_code == 422
    assert client.get(f"/api/items/{matrix}/images/extrafanart/choices").status_code == 422
    assert client.get(f"/api/items/{matrix}/images/nope/choices").status_code == 422


def test_upload_png_is_manual_and_locked(client: TestClient, matrix: int) -> None:
    response = client.put(
        f"/api/items/{matrix}/images/clearlogo/file",
        content=PNG,
        headers={"content-type": "image/png"},
    )
    assert response.status_code == 200, response.text
    field = _field(response.json(), "clearlogo")
    assert (field["source"], field["locked"]) == ("manual", True)
    assert field["value"][0]["url"].endswith(".png")


def test_upload_rejects_non_image(client: TestClient, matrix: int) -> None:
    for content, kind in ((b"hello", "image/png"), (PNG, "text/plain")):
        response = client.put(
            f"/api/items/{matrix}/images/poster/file",
            content=content,
            headers={"content-type": kind},
        )
        assert response.status_code == 422


def test_preview_only_for_choices(client: TestClient, matrix: int) -> None:
    ok = client.get(
        f"/api/items/{matrix}/images/poster/preview", params={"url": f"{IMG}/w780/en-poster.jpg"}
    )
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("image/")
    missing = client.get(
        f"/api/items/{matrix}/images/poster/preview", params={"url": "http://127.0.0.1:1/x.png"}
    )
    assert missing.status_code == 404


def test_collection_poster_choices(app: FastAPI, client: TestClient, matrix: int) -> None:
    with app.state.session_factory() as session:
        collection_id = session.scalars(select(Item.id).where(Item.kind == "collection")).one()
    rows = _choices(client, collection_id, "poster")
    assert rows[0]["url"].endswith("/c-poster.jpg") and rows[0]["selected"]
    detail = client.put(
        f"/api/items/{collection_id}/images/poster", json={"url": rows[1]["url"]}
    ).json()
    assert _field(detail, "poster")["locked"] is True
