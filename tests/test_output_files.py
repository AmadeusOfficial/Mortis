import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import MovieScrapeConfig, Secrets
from mortis.db.models import Item, Library, LibraryPath, MediaFile, Task, utc_now
from mortis.items.identity import MovieRef, find_or_create_movie
from mortis.output.nfo import build_movie_nfo, kodi_trailer
from mortis.output.writer import actor_file_name, write_item_files
from mortis.scrape.images import ImageStore
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
    app.state.config_store.save_secrets(Secrets(tmdb_api_key="k"))
    config = app.state.config_store.load_config().movie
    config.output.write_after_scrape = False
    app.state.config_store.save_section("movie", config)
    return app


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "lib" / "黑客帝国" / "The.Matrix.1999.mkv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")
    return path


def _add_movie(app: FastAPI, video: Path, extra_video: Path | None = None) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(data_type_id="movie", name="电影", created_at=now, updated_at=now)
        session.add(library)
        session.flush()
        item, _ = find_or_create_movie(session, library, MovieRef(603, "The Matrix"))
        for path, owner in ((video, item.id), (extra_video, None)):
            if path is not None:
                session.add(
                    MediaFile(
                        library_id=library.id,
                        item_id=owner,
                        path=str(path),
                        file_kind="video",
                        created_at=now,
                        updated_at=now,
                    )
                )
        session.commit()
        item_id = item.id
        task = repo.enqueue(session, "scrape", {"item_id": item_id, "force": False})
        session.commit()
        task_id = task.id
    app.state.task_runner.run_until_idle()
    with app.state.session_factory() as session:
        assert session.get(Task, task_id).status == "succeeded"
    return item_id


def _config(app: FastAPI) -> MovieScrapeConfig:
    return app.state.config_store.load_config().movie


def _write(app: FastAPI, item_id: int, config: MovieScrapeConfig | None = None):
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        report = write_item_files(
            session, ImageStore(app.state.settings.data_dir), item, config or _config(app)
        )
        session.commit()
        return report


def test_nfo_follows_emby_layout(app: FastAPI, video: Path) -> None:
    item_id = _add_movie(app, video)
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        raw = build_movie_nfo(session, item, certification_country="US")
    text = raw.decode("utf-8")
    assert text.startswith(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<!--created on'
    )
    assert "by Mortis for EMBY-->" in text
    assert "<sorttitle/>" in text and " />" not in text
    root = ET.fromstring(raw)
    tags = [child.tag for child in root]
    order = [
        "title",
        "originaltitle",
        "sorttitle",
        "year",
        "ratings",
        "set",
        "plot",
        "runtime",
        "mpaa",
        "id",
        "tmdbid",
        "uniqueid",
        "premiered",
        "genre",
        "director",
        "actor",
        "trailer",
        "dateadded",
        "fileinfo",
    ]
    positions = [tags.index(tag) for tag in order]
    assert positions == sorted(positions)
    assert root.findtext("title") == "黑客帝国"
    assert root.findtext("year") == "1999"
    assert root.findtext("mpaa") == "US:R"
    assert root.findtext("tmdbid") == "603"
    assert root.find("set").get("tmdbcolid") == "2344"
    uniques = {u.get("type"): (u.text, u.get("default")) for u in root.findall("uniqueid")}
    assert uniques["tmdb"] == ("603", "false")
    assert uniques["imdb"][1] == "true"
    assert uniques["tmdbSet"] == ("2344", "false")
    rating = root.find("ratings/rating")
    assert (rating.get("name"), rating.get("default"), rating.get("max")) == (
        "themoviedb",
        "true",
        "10",
    )
    actor = root.find("actor")
    assert actor.findtext("name") and actor.findtext("tmdbid")
    assert root.findtext("trailer", "").startswith("plugin://plugin.video.youtube/")
    assert root.find("lockdata") is None
    assert tags.index("writer") > tags.index("fileinfo")
    assert build_movie_nfo(session, item, certification_country="US") == raw


def test_writer_writes_nfo_images_and_actors(app: FastAPI, video: Path) -> None:
    item_id = _add_movie(app, video)
    report = _write(app, item_id)
    folder = video.parent
    names = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    assert {
        "movie.nfo",
        "The.Matrix.1999.nfo",
        "The.Matrix.1999-poster.jpg",
        "The.Matrix.1999-fanart.jpg",
        "movieset-poster.jpg",
    } <= names
    assert any(name.startswith(".actors/") for name in names)
    assert not any(name.endswith(".tmp") for name in names)
    assert report.written and not report.unchanged
    again = _write(app, item_id)
    assert not again.written and sorted(again.unchanged) == sorted(report.written)
    with app.state.session_factory() as session:
        kinds = {
            row.path: row.file_kind
            for row in session.scalars(select(MediaFile).where(MediaFile.item_id == item_id))
        }
    assert kinds[str(folder / "movie.nfo")] == "nfo"
    assert kinds[str(folder / "The.Matrix.1999-poster.jpg")] == "image"


def test_writer_removes_only_its_own_stale_files(app: FastAPI, video: Path) -> None:
    item_id = _add_movie(app, video)
    foreign = video.parent / "fanart.jpg"
    foreign.write_bytes(b"theirs")
    _write(app, item_id)
    config = _config(app)
    config.output.nfo_names = ["filename"]
    config.images.actor_images = False
    report = _write(app, item_id, config)
    assert not (video.parent / "movie.nfo").exists()
    assert not (video.parent / ".actors").exists()
    assert str(video.parent / "movie.nfo") in report.removed
    assert foreign.read_bytes() == b"theirs"


def test_shared_folder_skips_names_without_file_stem(app: FastAPI, video: Path) -> None:
    other = video.parent / "Other.Movie.2001.mkv"
    other.write_bytes(b"other")
    item_id = _add_movie(app, video, extra_video=other)
    config = _config(app)
    config.output.image_style = "plain"
    _write(app, item_id, config)
    assert not (video.parent / "movie.nfo").exists()
    assert not (video.parent / "poster.jpg").exists()
    assert not (video.parent / "movieset-poster.jpg").exists()
    assert (video.parent / "The.Matrix.1999.nfo").exists()
    assert (video.parent / "The.Matrix.1999-poster.jpg").exists()


def test_library_root_gets_only_file_named_outputs(app: FastAPI, tmp_path: Path) -> None:
    video = tmp_path / "lib" / "matrix.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    item_id = _add_movie(app, video)
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        session.add(LibraryPath(library_id=item.library_id, path=str(video.parent)))
        session.commit()
    _write(app, item_id)
    assert (video.parent / "matrix.nfo").is_file()
    assert not (video.parent / "movie.nfo").exists()
    assert not (video.parent / "movieset-poster.jpg").exists()


def test_plain_image_style_when_alone(app: FastAPI, video: Path) -> None:
    item_id = _add_movie(app, video)
    config = _config(app)
    config.output.image_style = "plain"
    _write(app, item_id, config)
    assert (video.parent / "poster.jpg").exists()
    assert not (video.parent / "The.Matrix.1999-poster.jpg").exists()


def test_helpers() -> None:
    assert kodi_trailer("https://www.youtube.com/watch?v=abc") == (
        "plugin://plugin.video.youtube/play/?video_id=abc"
    )
    assert kodi_trailer("https://example.com/t.mp4") == "https://example.com/t.mp4"
    assert actor_file_name('Keanu Reeves: "Neo"') == "Keanu_Reeves_Neo"
