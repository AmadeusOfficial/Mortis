from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.db.models import Library, LibraryPath, MediaFile, Task, utc_now
from mortis.items.identity import MovieRef, find_or_create_movie
from mortis.settings import Settings
from mortis.tasks import repo
from tests.fakes import FakeTmdb, load_fixture


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    fake = FakeTmdb()
    fake.add("/3/movie/603", load_fixture("movie_603.json"))
    fake.add("/3/collection/2344", load_fixture("collection_2344.json"))
    fake.add("/3/collection/2344/images", load_fixture("collection_2344_images.json"))
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "lib"
    (root / "matrix").mkdir(parents=True)
    (root / "matrix" / "matrix.mkv").write_bytes(b"video")
    return root


def _setup(app: FastAPI, root: Path) -> tuple[int, int]:
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(data_type_id="movie", name="电影", created_at=now, updated_at=now)
        session.add(library)
        session.flush()
        session.add(LibraryPath(library_id=library.id, path=str(root)))
        item, _ = find_or_create_movie(session, library, MovieRef(603, "The Matrix"))
        session.add(
            MediaFile(
                library_id=library.id,
                item_id=item.id,
                path=str(root / "matrix" / "matrix.mkv"),
                file_kind="video",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        return library.id, item.id


def _configure(app: FastAPI, **switches: bool) -> None:
    config = app.state.config_store.load_config().types.movie
    for key, value in switches.items():
        setattr(config.output, key, value)
    app.state.config_store.save_section("types.movie", config)


def _scrape(app: FastAPI, item_id: int) -> None:
    with app.state.session_factory() as session:
        repo.enqueue(session, "scrape", {"item_id": item_id, "force": False})
        session.commit()
    app.state.task_runner.run_until_idle()


def _statuses(app: FastAPI) -> dict[str, str]:
    with app.state.session_factory() as session:
        return {t.task_type: t.status for t in session.scalars(select(Task).order_by(Task.id))}


def test_scrape_arranges_by_default(app: FastAPI, root: Path) -> None:
    _, item_id = _setup(app, root)
    _scrape(app, item_id)
    assert _statuses(app) == {"scrape": "succeeded", "arrange": "succeeded"}
    assert (root / "matrix" / "movie.nfo").is_file()
    assert (root / "matrix" / "matrix-poster.jpg").is_file()


def test_scrape_only_when_auto_arrange_off(app: FastAPI, root: Path) -> None:
    _configure(app, organize_after_scrape=False)
    _, item_id = _setup(app, root)
    _scrape(app, item_id)
    assert _statuses(app) == {"scrape": "succeeded"}
    assert not (root / "matrix" / "movie.nfo").exists()


def test_scrape_then_rename_and_write(app: FastAPI, root: Path) -> None:
    _configure(app, organize_rename=True)
    _, item_id = _setup(app, root)
    _scrape(app, item_id)
    assert _statuses(app) == {"scrape": "succeeded", "arrange": "succeeded"}
    folder = root / "黑客帝国 (1999)"
    assert (folder / "黑客帝国.mkv").is_file()
    assert (folder / "黑客帝国.nfo").is_file() and (folder / "movie.nfo").is_file()
    assert not (root / "matrix").exists()


def test_rename_only_skips_files(app: FastAPI, root: Path) -> None:
    _configure(app, organize_rename=True, organize_write_files=False)
    _, item_id = _setup(app, root)
    _scrape(app, item_id)
    folder = root / "黑客帝国 (1999)"
    assert (folder / "黑客帝国.mkv").is_file()
    assert not (folder / "movie.nfo").exists()


def test_preview_and_arrange_through_api(app: FastAPI, client: TestClient, root: Path) -> None:
    _configure(app, organize_after_scrape=False, organize_rename=True)
    library_id, item_id = _setup(app, root)
    _scrape(app, item_id)
    preview = client.get(f"/api/items/{item_id}/arrange-preview").json()
    assert preview["rename"] and preview["write"]
    assert preview["changes"] and not preview["problems"]
    assert preview["folder"] == preview["folder_to"] == str(root / "黑客帝国 (1999)")
    assert {
        "source": str(root / "matrix" / "matrix.mkv"),
        "target": str(root / "黑客帝国 (1999)" / "黑客帝国.mkv"),
    } in preview["moves"]
    listed = client.get(f"/api/libraries/{library_id}/arrange-preview").json()
    assert listed["count"] == 1 and listed["rename"] and listed["write"]
    assert [row["item_id"] for row in listed["previews"]] == [item_id]

    response = client.post(f"/api/items/{item_id}/arrange")
    assert response.status_code == 202
    app.state.task_runner.run_until_idle()
    folder = root / "黑客帝国 (1999)"
    assert (folder / "黑客帝国.mkv").is_file()
    assert (folder / "黑客帝国-poster.jpg").is_file()
    assert client.get(f"/api/libraries/{library_id}/arrange-preview").json()["previews"] == []

    detail = client.get(f"/api/items/{item_id}").json()
    kinds = {Path(f["path"]).name: f["file_kind"] for f in detail["files"]}
    assert kinds["黑客帝国.mkv"] == "video" and kinds["movie.nfo"] == "nfo"


def test_preview_without_rename(app: FastAPI, client: TestClient, root: Path) -> None:
    _configure(app, organize_after_scrape=False)
    _, item_id = _setup(app, root)
    _scrape(app, item_id)
    preview = client.get(f"/api/items/{item_id}/arrange-preview").json()
    assert not preview["rename"] and preview["write"]
    assert preview["folder"] == str(root / "matrix")
    assert preview["moves"] == []


def test_rename_conflict_is_rejected(app: FastAPI, client: TestClient, root: Path) -> None:
    _configure(app, organize_after_scrape=False, organize_rename=True)
    _, item_id = _setup(app, root)
    _scrape(app, item_id)
    (root / "黑客帝国 (1999)").mkdir()
    response = client.post(f"/api/items/{item_id}/arrange")
    assert response.status_code == 422
    assert "目标目录已存在" in response.json()["detail"]


def test_conflict_still_writes_files(app: FastAPI, root: Path) -> None:
    _configure(app, organize_rename=True)
    _, item_id = _setup(app, root)
    (root / "黑客帝国 (1999)").mkdir()
    _scrape(app, item_id)
    assert _statuses(app)["arrange"] == "failed"
    assert (root / "matrix" / "movie.nfo").is_file()


def test_unscraped_item_cannot_be_arranged(app: FastAPI, client: TestClient, root: Path) -> None:
    _, item_id = _setup(app, root)
    response = client.post(f"/api/items/{item_id}/arrange")
    assert response.status_code == 422
    assert "还没刮削" in response.json()["detail"]


def test_library_arrange(app: FastAPI, client: TestClient, root: Path) -> None:
    _configure(app, organize_after_scrape=False)
    library_id, item_id = _setup(app, root)
    _scrape(app, item_id)
    assert client.post(f"/api/libraries/{library_id}/arrange").status_code == 202
    app.state.task_runner.run_until_idle()
    assert (root / "matrix" / "movie.nfo").is_file()
    assert _statuses(app)["arrange_library"] == "succeeded"


def test_edit_rewrites_arranged_nfo(app: FastAPI, client: TestClient, root: Path) -> None:
    _configure(app, organize_rename=True)
    _, item_id = _setup(app, root)
    _scrape(app, item_id)
    folder = root / "黑客帝国 (1999)"
    response = client.patch(f"/api/items/{item_id}/fields", json={"values": {"title": "母体"}})
    assert response.status_code == 200
    app.state.task_runner.run_until_idle()
    assert "<title>母体</title>" in (folder / "movie.nfo").read_text(encoding="utf-8")
    assert (folder / "黑客帝国.mkv").is_file()


def test_edit_does_not_write_unarranged(app: FastAPI, client: TestClient, root: Path) -> None:
    _configure(app, organize_after_scrape=False)
    _, item_id = _setup(app, root)
    _scrape(app, item_id)
    client.patch(f"/api/items/{item_id}/fields", json={"values": {"title": "母体"}})
    app.state.task_runner.run_until_idle()
    assert not (root / "matrix" / "movie.nfo").exists()


def test_collection_has_no_files(app: FastAPI, client: TestClient, root: Path) -> None:
    _, item_id = _setup(app, root)
    _scrape(app, item_id)
    detail = client.get(f"/api/items/{item_id}").json()
    collection_id = detail["collection"]["id"]
    assert client.post(f"/api/items/{collection_id}/arrange").status_code == 422
