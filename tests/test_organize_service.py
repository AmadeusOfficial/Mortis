import json
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.db.models import Item, ItemExternalId, ItemSourceRecord, MediaFile, Task, utc_now
from mortis.libraries import create_library
from mortis.settings import Settings
from mortis.tasks import repo
from tests.fakes import FakeTmdb, hit


@pytest.fixture
def fake() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add_search("The Matrix", [hit(603, "黑客帝国", "1999-03-30", "The Matrix")])
    fake.add_search("Heat", [hit(949, "盗火线", "1995-12-15", "Heat")])
    return fake


@pytest.fixture
def app(tmp_path: Path, fake: FakeTmdb) -> FastAPI:
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    return app


@pytest.fixture
def movies(tmp_path: Path) -> Path:
    root = tmp_path / "电影"
    for rel in [
        "The Matrix (1999)/The.Matrix.1999.mkv",
        "The Matrix (1999)/The.Matrix.1999.zh.srt",
        "Heat (1995)/Heat.cd1.mkv",
        "Heat (1995)/Heat.cd2.mkv",
        "Nothing Like This (2031)/x.mkv",
    ]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"12345")
    return root


def _library(app: FastAPI, movies: Path, auto: bool = True) -> int:
    with app.state.session_factory() as session:
        library = create_library(session, name="电影", data_type_id="movie", paths=[str(movies)])
        library.is_auto_scrape = int(auto)
        session.commit()
        return library.id


def _organize(app: FastAPI, library_id: int, scrape: bool | None = None) -> int:
    with app.state.session_factory() as session:
        task = repo.enqueue(session, "organize", {"library_id": library_id, "scrape": scrape})
        session.commit()
        task_id = task.id
    app.state.task_runner.run_once()
    return task_id


def _task(app: FastAPI, task_id: int) -> Task:
    with app.state.session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        session.expunge(task)
        return task


def _result(app: FastAPI, task_id: int) -> dict:
    task = _task(app, task_id)
    assert task.status == "succeeded", task.error
    return json.loads(task.result or "{}")


def test_first_scan_identifies_and_queues_scrapes(app: FastAPI, movies: Path) -> None:
    library_id = _library(app, movies)
    task_id = _organize(app, library_id)
    assert _result(app, task_id) == {
        "found": 3,
        "added": 5,
        "removed": 0,
        "matched": 2,
        "unmatched": 1,
        "errors": 0,
        "identified": True,
        "scrape_queued": 2,
    }
    with app.state.session_factory() as session:
        items = session.scalars(select(Item).order_by(Item.title)).all()
        assert [(i.title, i.original_title, i.release_date, i.subtitle) for i in items] == [
            ("盗火线", "Heat", "1995-12-15", "Heat"),
            ("黑客帝国", "The Matrix", "1999-03-30", "The Matrix"),
        ]
        ids = [e.external_id for e in session.scalars(select(ItemExternalId))]
        assert sorted(ids) == ["603", "949"]
        heat = next(i for i in items if i.title == "盗火线")
        heat_files = session.scalars(
            select(MediaFile.path).where(MediaFile.item_id == heat.id)
        ).all()
        assert [Path(p).name for p in sorted(heat_files)] == ["Heat.cd1.mkv", "Heat.cd2.mkv"]
        unbound = session.scalars(select(MediaFile.path).where(MediaFile.item_id.is_(None))).all()
        assert [Path(p).name for p in unbound] == ["x.mkv"]
        children = session.scalars(select(Task).where(Task.parent_task_id == task_id)).all()
        assert {json.loads(c.payload or "{}")["item_id"] for c in children} == {i.id for i in items}
        assert all(c.task_type == "scrape" and c.priority == -1 for c in children)


def test_rescan_is_idempotent_and_removes_missing_files(app: FastAPI, movies: Path) -> None:
    library_id = _library(app, movies, auto=False)
    _organize(app, library_id)
    (movies / "The Matrix (1999)" / "The.Matrix.1999.zh.srt").unlink()
    (movies / "The Matrix (1999)" / "The.Matrix.1999.en.srt").write_bytes(b"1")
    result = _result(app, _organize(app, library_id))
    assert (result["added"], result["removed"], result["matched"]) == (1, 1, 0)
    assert result["scrape_queued"] == 0
    with app.state.session_factory() as session:
        assert len(session.scalars(select(Item)).all()) == 2
        new_sub = session.scalar(
            select(MediaFile).where(MediaFile.path.endswith("The.Matrix.1999.en.srt"))
        )
        assert new_sub is not None
        assert new_sub.item_id is not None and new_sub.file_kind == "subtitle"


def test_scrape_override_and_skip_already_scraped(app: FastAPI, movies: Path) -> None:
    library_id = _library(app, movies, auto=False)
    _organize(app, library_id)
    with app.state.session_factory() as session:
        matrix = session.scalar(select(Item).where(Item.title == "黑客帝国"))
        assert matrix is not None
        session.add(
            ItemSourceRecord(item_id=matrix.id, source="tmdb", fetched_at=utc_now(), raw="{}")
        )
        session.commit()
    result = _result(app, _organize(app, library_id, scrape=True))
    assert result["scrape_queued"] == 1


def test_no_key_only_scans(app: FastAPI, movies: Path, fake: FakeTmdb) -> None:
    app.state.config_store.save_secrets(Secrets())
    library_id = _library(app, movies)
    result = _result(app, _organize(app, library_id))
    assert (result["identified"], result["matched"], result["added"]) == (False, 0, 5)
    assert fake.requests == []


def test_rejected_key_fails_task(tmp_path: Path, movies: Path) -> None:
    reject = httpx.MockTransport(lambda request: httpx.Response(401))
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=reject, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    library_id = _library(app, movies)
    task = _task(app, _organize(app, library_id))
    assert (task.status, task.error) == ("failed", "TMDB 拒绝了这个 API Key")


def test_missing_library_fails(app: FastAPI) -> None:
    task = _task(app, _organize(app, 999))
    assert (task.status, task.error) == ("failed", "媒体库不存在，可能已被删除")
