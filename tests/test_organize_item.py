from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.db.models import Item, ItemExternalId, ItemSourceRecord, MediaFile, Task
from mortis.settings import Settings
from mortis.tasks import repo
from tests.test_scrape_item import DATA_TYPE, SOURCE, FakeSites, _write

LOCAL = "http://127.0.0.1"


@pytest.fixture
def sites() -> FakeSites:
    sites = FakeSites()
    sites.hits["beta"] = [{"external_id": "b9", "title": "星之梦", "original_title": "ほしのゆめ"}]
    sites.items["alpha/item/a1"] = {"values": {"title": "星之梦（Alpha）"}}
    sites.items["beta/item/b9"] = {"values": {"title": "星之梦（Beta）"}}
    return sites


@pytest.fixture
def games(tmp_path: Path) -> Path:
    root = tmp_path / "games"
    (root / "[KOMMIT] 星之梦 v1.02 汉化硬盘版" / "data").mkdir(parents=True)
    (root / "没人认识的游戏").mkdir()
    return root


@pytest.fixture
def app(tmp_path: Path, sites: FakeSites) -> FastAPI:
    plugins = tmp_path / "plugins"
    _write(plugins, "doujin", "data_type", DATA_TYPE)
    _write(plugins, "alpha", "source", SOURCE.format(id="alpha", name="Alpha"))
    _write(plugins, "beta", "source", SOURCE.format(id="beta", name="Beta"))
    return create_app(
        Settings(data_dir=tmp_path / "data", plugins_dir=plugins),
        http_transport=httpx.MockTransport(sites.handler),
        start_worker=False,
    )


@pytest.fixture
def client(app: FastAPI):
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _library(client: TestClient, games: Path, auto: bool = False) -> int:
    response = client.post(
        "/api/libraries",
        json={"name": "同人", "data_type_id": "doujin", "paths": [str(games)]},
    )
    assert response.status_code == 201, response.text
    library_id = response.json()["id"]
    client.patch(f"/api/libraries/{library_id}", json={"is_auto_scrape": auto})
    return library_id


def _organize(app: FastAPI, library_id: int) -> Task:
    with app.state.session_factory() as session:
        task = repo.enqueue(session, "organize", {"library_id": library_id})
        session.commit()
        task_id = task.id
    app.state.task_runner.run_until_idle()
    with app.state.session_factory() as session:
        task = session.get(Task, task_id)
        session.expunge(task)
        return task


def test_scan_identifies_by_source_order(
    app: FastAPI, client: TestClient, games: Path, sites: FakeSites
) -> None:
    library_id = _library(client, games)
    task = _organize(app, library_id)
    assert task.status == "succeeded", task.error
    with app.state.session_factory() as session:
        files = session.scalars(select(MediaFile).order_by(MediaFile.path)).all()
        assert [(Path(f.path).name, f.file_kind) for f in files] == [
            ("[KOMMIT] 星之梦 v1.02 汉化硬盘版", "game"),
            ("没人认识的游戏", "game"),
        ]
        item = session.get(Item, files[0].item_id)
        assert item is not None and item.title == "星之梦"
        ids = session.execute(
            select(
                ItemExternalId.provider, ItemExternalId.external_id, ItemExternalId.is_primary
            ).where(ItemExternalId.item_id == item.id)
        ).all()
        assert [tuple(row) for row in ids] == [("beta", "b9", 1)]
        assert files[1].item_id is None
    # alpha 先搜（没结果），再用 beta
    assert sites.count("https://alpha.test/search") >= 1


def test_rescan_does_not_duplicate_and_removes_gone(
    app: FastAPI, client: TestClient, games: Path
) -> None:
    library_id = _library(client, games)
    _organize(app, library_id)
    (games / "没人认识的游戏").rmdir()
    assert _organize(app, library_id).status == "succeeded"
    with app.state.session_factory() as session:
        assert session.scalar(select(Item.id).where(Item.kind == "work")) is not None
        assert len(session.scalars(select(Item)).all()) == 1
        assert len(session.scalars(select(MediaFile)).all()) == 1


def test_auto_scrape_after_scan(app: FastAPI, client: TestClient, games: Path) -> None:
    library_id = _library(client, games, auto=True)
    assert _organize(app, library_id).status == "succeeded"
    with app.state.session_factory() as session:
        item = session.scalars(select(Item)).one()
        assert item.title == "星之梦（Beta）"
        assert session.get(ItemSourceRecord, (item.id, "beta")) is not None


def test_unmatched_bind_and_rematch_by_source(
    app: FastAPI, client: TestClient, games: Path, sites: FakeSites
) -> None:
    library_id = _library(client, games)
    _organize(app, library_id)
    unmatched = client.get("/api/media-files/unmatched").json()
    assert [(row["guess_title"], row["data_type_id"]) for row in unmatched] == [
        ("没人认识的游戏", "doujin")
    ]

    sources = client.get("/api/sources", params={"data_type": "doujin"}).json()
    assert [s["id"] for s in sources] == ["alpha", "beta"]
    sites.hits["alpha"] = [{"external_id": "a1", "title": "Alpha 游戏", "year": 2001}]
    hits = client.get("/api/sources/alpha/search", params={"query": "游戏"}).json()
    assert hits[0]["external_id"] == "a1" and hits[0]["year"] == 2001

    bound = client.post(
        f"/api/media-files/{unmatched[0]['id']}/bind",
        json={"source": "alpha", "external_id": "a1", "title": "Alpha 游戏"},
    )
    assert bound.status_code == 202, bound.text
    item_id = bound.json()["item_id"]
    app.state.task_runner.run_until_idle()
    assert client.get(f"/api/items/{item_id}").json()["title"] == "星之梦（Alpha）"

    wrong = client.post(f"/api/items/{item_id}/rematch", json={"source": "beta"})
    assert wrong.status_code == 422
    conflict = client.post(
        f"/api/items/{item_id}/rematch", json={"source": "beta", "external_id": "b9"}
    )
    assert conflict.status_code == 409
    sites.items["beta/item/b3"] = {"values": {"title": "改过的"}}
    redo = client.post(
        f"/api/items/{item_id}/rematch", json={"source": "beta", "external_id": "b3"}
    )
    assert redo.status_code == 202
    app.state.task_runner.run_until_idle()
    with app.state.session_factory() as session:
        providers = session.execute(
            select(ItemExternalId.provider, ItemExternalId.external_id).where(
                ItemExternalId.item_id == item_id
            )
        ).all()
        assert ("beta", "b3") in [tuple(row) for row in providers]
        assert ("alpha", "a1") not in [tuple(row) for row in providers]
        assert session.get(Item, item_id).title == "改过的"


def test_search_unknown_source_is_404(client: TestClient) -> None:
    assert client.get("/api/sources/nope/search", params={"query": "x"}).status_code == 404


def test_bind_with_source_of_other_type_rejected(client: TestClient, games: Path) -> None:
    app = client.app
    library_id = _library(client, games)
    _organize(app, library_id)
    row = client.get("/api/media-files/unmatched").json()[0]
    response = client.post(
        f"/api/media-files/{row['id']}/bind",
        json={"source": "bangumi", "external_id": "1", "title": "x"},
    )
    assert response.status_code == 422
