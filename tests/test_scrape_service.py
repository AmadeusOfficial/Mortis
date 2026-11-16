import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import MovieImages, MovieScrapeConfig, Secrets
from mortis.db.models import (
    Item,
    ItemExternalId,
    ItemImage,
    ItemSourceRecord,
    Library,
    Person,
    Task,
    utc_now,
)
from mortis.items.identity import MovieRef, find_or_create_movie
from mortis.scrape.images import ImageStore
from mortis.settings import Settings
from mortis.tasks import repo
from tests.fakes import FakeTmdb, load_fixture

LOCAL = "http://127.0.0.1"


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
    app.state.config_store.save_secrets(Secrets(tmdb_api_key="k"))
    return app


@pytest.fixture
def item_id(app: FastAPI) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(data_type_id="movie", name="电影", created_at=now, updated_at=now)
        session.add(library)
        session.flush()
        item, _ = find_or_create_movie(session, library, MovieRef(603, "The Matrix"))
        session.commit()
        return item.id


def _scrape(app: FastAPI, item_id: int, force: bool = False) -> Task:
    with app.state.session_factory() as session:
        task = repo.enqueue(session, "scrape", {"item_id": item_id, "force": force})
        session.commit()
        task_id = task.id
    app.state.task_runner.run_until_idle()
    with app.state.session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        session.expunge(task)
        return task


def test_scrape_records_each_source(app: FastAPI, item_id: int) -> None:
    first = _scrape(app, item_id)
    assert first.status == "succeeded", first.error
    assert json.loads(first.result or "{}")["sources"] == [
        {"id": "tmdb", "name": "TMDB 电影", "outcome": "ok", "detail": None},
        {"id": "tmdb_collection", "name": "TMDB 系列", "outcome": "ok", "detail": None},
    ]
    second = _scrape(app, item_id)
    assert [(s["id"], s["outcome"]) for s in json.loads(second.result or "{}")["sources"]] == [
        ("tmdb", "cached"),
        ("tmdb_collection", "cached"),
    ]


def test_scrape_writes_movie_collection_and_images(app: FastAPI, item_id: int) -> None:
    task = _scrape(app, item_id)
    assert task.status == "succeeded", task.error
    store = ImageStore(app.state.settings.data_dir)
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None
        assert (item.title, item.original_title, item.release_date) == (
            "黑客帝国",
            "The Matrix",
            "1999-03-30",
        )
        assert store.resolve(item.poster_path) is not None
        assert json.loads(item.fields)["certification"] == "R"
        ids = {
            provider: value
            for provider, value in session.execute(
                select(ItemExternalId.provider, ItemExternalId.external_id).where(
                    ItemExternalId.item_id == item_id
                )
            )
        }
        assert ids == {"tmdb": "603", "imdb": "tt0133093"}
        collection = session.get(Item, item.parent_id)
        assert collection is not None
        assert (collection.kind, collection.title) == ("collection", "黑客帝国（系列）")
        assert collection.subtitle == "The Matrix Collection"
        assert store.resolve(collection.poster_path) is not None
        kinds = set(
            session.scalars(select(ItemImage.image_kind).where(ItemImage.item_id == item_id))
        )
        assert kinds == {"fanart", "clearlogo", "keyart"}
        keanu = session.scalar(select(Person).where(Person.original_name == "Keanu Reeves"))
        assert keanu is not None and store.resolve(keanu.photo_path) is not None
        assert session.get(ItemSourceRecord, (item_id, "tmdb")) is not None
        assert session.get(ItemSourceRecord, (collection.id, "tmdb")) is not None


def test_snapshot_is_reused_unless_forced(app: FastAPI, item_id: int, fake: FakeTmdb) -> None:
    _scrape(app, item_id)
    count = fake.paths().count("/3/movie/603")
    _scrape(app, item_id)
    assert fake.paths().count("/3/movie/603") == count
    _scrape(app, item_id, force=True)
    assert fake.paths().count("/3/movie/603") == count + 1


def test_locked_fields_survive(app: FastAPI, item_id: int) -> None:
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None
        item.title = "我的片名"
        item.locked_fields = json.dumps(["title"])
        item.field_sources = json.dumps({"title": "manual"})
        session.commit()
    _scrape(app, item_id, force=True)
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None
        assert item.title == "我的片名"
        assert json.loads(item.field_sources)["title"] == "manual"


def test_disabled_image_kinds_are_not_downloaded(
    app: FastAPI, item_id: int, fake: FakeTmdb
) -> None:
    app.state.config_store.save_section(
        "movie", MovieScrapeConfig(images=MovieImages(clearlogo=False, actor_images=False))
    )
    _scrape(app, item_id)
    assert not any("logo" in path for path in fake.paths())
    assert not any("keanu" in path for path in fake.paths())


def test_leaving_collection_drops_empty_collection(
    app: FastAPI, item_id: int, fake: FakeTmdb
) -> None:
    _scrape(app, item_id)
    raw = load_fixture("movie_603.json")
    raw["belongs_to_collection"] = None
    fake.add("/3/movie/603", raw)
    _scrape(app, item_id, force=True)
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None and item.parent_id is None
        assert session.scalars(select(Item).where(Item.kind == "collection")).all() == []


def test_image_failure_does_not_fail_scrape(app: FastAPI, item_id: int, fake: FakeTmdb) -> None:
    fake.image_status = 500
    task = _scrape(app, item_id)
    assert task.status == "succeeded"
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None and item.poster_path is None


def test_item_without_tmdb_id_fails(app: FastAPI, item_id: int) -> None:
    with app.state.session_factory() as session:
        session.query(ItemExternalId).delete()
        session.commit()
    task = _scrape(app, item_id)
    assert task.status == "failed" and "没有 TMDB 编号" in (task.error or "")


def test_scrape_library_queues_followups(app: FastAPI, item_id: int) -> None:
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None
        task = repo.enqueue(
            session, "scrape_library", {"library_id": item.library_id, "force": False}
        )
        session.commit()
        root = task.id
    app.state.task_runner.run_until_idle()
    with app.state.session_factory() as session:
        children = session.scalars(select(Task).where(Task.parent_task_id == root)).all()
        assert [(c.task_type, c.status) for c in children] == [("scrape", "succeeded")]


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def test_scrape_endpoints(client: TestClient, item_id: int) -> None:
    first = client.post(f"/api/items/{item_id}/scrape", json={"force": True})
    assert first.status_code == 202
    assert client.post(f"/api/items/{item_id}/scrape").json() == first.json()
    assert client.post("/api/items/999/scrape").status_code == 404
    with client.app.state.session_factory() as session:
        library_id = session.get(Item, item_id).library_id
    response = client.post(f"/api/libraries/{library_id}/scrape")
    assert response.status_code == 202
    task = client.get(f"/api/tasks/{response.json()['task_id']}").json()
    assert (task["task_type"], task["title"], task["payload"]) == (
        "scrape_library",
        "电影",
        {"library_id": library_id, "force": False},
    )


def test_forced_rescrape_downloads_only_missing_images(
    app: FastAPI, item_id: int, fake: FakeTmdb
) -> None:
    assert _scrape(app, item_id).status == "succeeded"
    before = len(fake.paths())
    assert _scrape(app, item_id, force=True).status == "succeeded"
    assert [p for p in fake.paths()[before:] if "/t/p/" in p] == []

    store = ImageStore(app.state.settings.data_dir)
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None
        poster = store.resolve(item.poster_path)
    assert poster is not None
    poster.unlink()
    before = len(fake.paths())
    assert _scrape(app, item_id, force=True).status == "succeeded"
    assert len([p for p in fake.paths()[before:] if "/t/p/" in p]) == 1
    assert store.resolve(item.poster_path) is not None
