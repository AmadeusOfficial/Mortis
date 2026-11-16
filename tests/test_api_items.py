import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.db.models import Item, Library, MediaFile, Task, utc_now
from mortis.items.identity import MovieRef, find_or_create_movie
from mortis.settings import Settings
from tests.fakes import FakeTmdb, load_fixture

LOCAL = "http://127.0.0.1"


@pytest.fixture
def fake() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add("/3/movie/603", load_fixture("movie_603.json"))
    fake.add("/3/collection/2344", load_fixture("collection_2344.json"))
    fake.add("/3/collection/2344/images", load_fixture("collection_2344_images.json"))
    heat = {"id": 949, "title": "盗火线", "original_title": "Heat", "release_date": "1995-12-15"}
    fake.add("/3/movie/949", heat)
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
def scraped(app: FastAPI, client: TestClient, tmp_path: Path) -> dict[str, int]:
    root = tmp_path / "电影"
    (root / "The Matrix (1999)").mkdir(parents=True)
    video = root / "The Matrix (1999)" / "m.mkv"
    video.write_bytes(b"x")
    library = client.post(
        "/api/libraries", json={"name": "电影", "data_type_id": "movie", "paths": [str(root)]}
    ).json()
    with app.state.session_factory() as session:
        lib = session.get(Library, library["id"])
        matrix, _ = find_or_create_movie(session, lib, MovieRef(603, "The Matrix"))
        heat, _ = find_or_create_movie(session, lib, MovieRef(949, "Heat", "Heat", "1995-12-15"))
        now = utc_now()
        session.add(
            MediaFile(
                library_id=lib.id,
                item_id=matrix.id,
                path=str(video),
                file_kind="video",
                size=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        ids = {"library": lib.id, "matrix": matrix.id, "heat": heat.id}
    client.post(f"/api/items/{ids['matrix']}/scrape")
    app.state.task_runner.run_until_idle()
    return ids


def _field(detail: dict, key: str) -> dict:
    return next(f for s in detail["sections"] for f in s["fields"] if f["key"] == key)


def test_wall_lists_works_with_posters(client: TestClient, scraped: dict[str, int]) -> None:
    wall = client.get(f"/api/libraries/{scraped['library']}/items").json()
    assert [row["title"] for row in wall] == ["Heat", "黑客帝国"]
    matrix = wall[1]
    assert matrix["subtitle"] == "The Matrix" and matrix["year"] == 1999
    assert matrix["poster_url"].startswith("/api/images/") and matrix["is_scraped"] is True
    assert wall[0]["poster_url"] is None and wall[0]["is_scraped"] is False
    assert [row["issue"] for row in wall] == [None, None]
    poster = client.get(matrix["poster_url"])
    assert poster.status_code == 200 and poster.content.startswith(b"img:")
    by_date = client.get(
        f"/api/libraries/{scraped['library']}/items",
        params={"sort": "release_date", "order": "desc"},
    ).json()
    assert [row["title"] for row in by_date] == ["黑客帝国", "Heat"]
    found = client.get(f"/api/libraries/{scraped['library']}/items", params={"q": "matrix"}).json()
    assert [row["title"] for row in found] == ["黑客帝国"]
    assert client.get("/api/libraries/999/items").status_code == 404


def test_wall_issue_comes_from_latest_failed_scrape(
    client: TestClient, app: FastAPI, scraped: dict[str, int]
) -> None:
    long_error = "刮削失败：连接 TMDB 时请求超时，请检查网络或代理设置后重试一次，日志里有详细原因"
    with app.state.session_factory() as session:
        now = utc_now()
        session.add(
            Task(
                task_type="scrape",
                status="failed",
                payload=json.dumps({"item_id": scraped["heat"], "force": False}),
                error=long_error + "\nTraceback (most recent call last): ...",
                created_at=now,
            )
        )
        session.commit()
    wall = client.get(f"/api/libraries/{scraped['library']}/items").json()
    assert [row["issue"] for row in wall] == [long_error[:40] + "…", None]
    collection_id = client.get(f"/api/items/{scraped['matrix']}").json()["collection"]["id"]
    parts = client.get(f"/api/items/{collection_id}").json()["parts"]
    assert [(part["title"], part["issue"]) for part in parts] == [("黑客帝国", None)]


def test_detail_sections_sources_and_collection(
    client: TestClient, scraped: dict[str, int]
) -> None:
    detail = client.get(f"/api/items/{scraped['matrix']}").json()
    assert detail["external_ids"] == {"tmdb": "603", "imdb": "tt0133093"}
    assert [s["name"] for s in detail["sections"]] == ["基本信息", "演职员", "图片"]
    title = _field(detail, "title")
    assert (title["value"], title["source"], title["locked"], title["editable"]) == (
        "黑客帝国",
        "tmdb",
        False,
        True,
    )
    assert _field(detail, "genres")["value"] == ["动作", "科幻"]
    actors = _field(detail, "actors")
    assert actors["value"][0]["character"] == "Neo / Thomas Anderson"
    assert actors["value"][0]["photo_url"].startswith("/api/images/")
    assert actors["editable"] is False
    assert _field(detail, "ratings")["value"][0]["rating"] == 8.2
    assert _field(detail, "fanart")["value"][0]["url"].startswith("/api/images/")
    assert detail["fanart_url"] == _field(detail, "fanart")["value"][0]["url"]
    assert detail["collection"]["title"] == "黑客帝国（系列）"
    videos = [f for f in detail["files"] if f["file_kind"] == "video"]
    assert [f["path"].endswith("m.mkv") for f in videos] == [True]
    assert detail["files"][0] == videos[0]
    assert {f["file_kind"] for f in detail["files"]} == {"video", "nfo", "image"}
    assert detail["scraped_at"] is not None
    collection = client.get(f"/api/items/{detail['collection']['id']}").json()
    assert collection["kind"] == "collection"
    assert [p["title"] for p in collection["parts"]] == ["黑客帝国"]
    assert _field(collection, "title")["label"] == "系列名"


def test_edit_locks_and_survives_rescrape(
    client: TestClient, app: FastAPI, scraped: dict[str, int]
) -> None:
    item_id = scraped["matrix"]
    values = {
        "title": "  骇客任务 ",
        "runtime": 140,
        "genres": ["科幻", " 赛博朋克 ", ""],
        "release_date": "1999-06-11",
    }
    detail = client.patch(f"/api/items/{item_id}/fields", json={"values": values}).json()
    title = _field(detail, "title")
    assert (title["value"], title["source"], title["locked"]) == ("骇客任务", "manual", True)
    assert _field(detail, "runtime")["value"] == 140
    assert _field(detail, "genres")["value"] == ["科幻", "赛博朋克"]
    client.post(f"/api/items/{item_id}/scrape", json={"force": True})
    app.state.task_runner.run_until_idle()
    detail = client.get(f"/api/items/{item_id}").json()
    assert _field(detail, "title")["value"] == "骇客任务"
    assert _field(detail, "genres")["value"] == ["科幻", "赛博朋克"]
    assert _field(detail, "overview")["source"] == "tmdb"
    detail = client.put(f"/api/items/{item_id}/locks", json={"locked": ["runtime"]}).json()
    assert _field(detail, "title")["locked"] is False
    assert _field(detail, "runtime")["locked"] is True


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"title": "  "}, "片名不能为空"),
        ({"release_date": "1999/06/11"}, "日期要写成"),
        ({"runtime": "很长"}, "要填数字"),
        ({"actors": []}, "不能手动修改"),
        ({"poster": []}, "不能手动修改"),
        ({"nope": 1}, "没有这个字段"),
    ],
)
def test_edit_rejects_bad_values(
    client: TestClient, scraped: dict[str, int], values: dict, message: str
) -> None:
    response = client.patch(f"/api/items/{scraped['matrix']}/fields", json={"values": values})
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_locks_reject_unknown_keys(client: TestClient, scraped: dict[str, int]) -> None:
    response = client.put(f"/api/items/{scraped['matrix']}/locks", json={"locked": ["nope"]})
    assert response.status_code == 422


def test_rematch(client: TestClient, app: FastAPI, scraped: dict[str, int]) -> None:
    conflict = client.post(f"/api/items/{scraped['matrix']}/rematch", json={"tmdb_id": 949})
    assert conflict.status_code == 409 and "Heat" in conflict.json()["detail"]
    with app.state.session_factory() as session:
        session.delete(session.get(Item, scraped["heat"]))
        session.commit()
    response = client.post(f"/api/items/{scraped['matrix']}/rematch", json={"tmdb_id": 949})
    assert response.status_code == 202
    app.state.task_runner.run_until_idle()
    detail = client.get(f"/api/items/{scraped['matrix']}").json()
    assert detail["title"] == "盗火线"
    assert detail["external_ids"] == {"tmdb": "949"}
    assert detail["collection"] is None
    assert _field(detail, "tagline")["value"] is None


def test_image_route_rejects_bad_paths(client: TestClient) -> None:
    assert client.get("/api/images/../mortis.db").status_code == 404
    assert client.get("/api/images/ab/" + "0" * 64 + ".jpg").status_code == 404
