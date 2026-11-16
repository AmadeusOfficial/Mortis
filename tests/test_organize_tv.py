import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.db.models import Item, MediaFile, Task, utc_now
from mortis.libraries import create_library
from mortis.organize.scan import scan_shows
from mortis.settings import Settings
from mortis.tasks import repo
from tests.fakes import FakeTmdb, tv_hit

EPISODE = "毒枭：墨西哥.Narcos.Mexico.S0{season}E0{episode}.中英字幕.BD.1080P-人人影视.mp4"


@pytest.fixture
def fake() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add_search("毒枭:墨西哥", [tv_hit(80968, "毒枭：墨西哥", "2018-11-16", "Narcos: Mexico")])
    return fake


@pytest.fixture
def app(tmp_path: Path, fake: FakeTmdb) -> FastAPI:
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(tmdb_api_key="k"))
    return app


@pytest.fixture
def shows(tmp_path: Path) -> Path:
    root = tmp_path / "电视剧"
    for season in (1, 2):
        for episode in (1, 2, 3):
            path = (
                root
                / "毒枭：墨西哥"
                / f"S0{season}"
                / EPISODE.format(season=season, episode=episode)
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
    (root / "毒枭：墨西哥" / "S01" / "sample.mp4").write_bytes(b"x")
    (root / "毒枭：墨西哥" / "S01" / EPISODE.format(season=1, episode=1)).with_suffix(
        ".chs.ass"
    ).write_bytes(b"x")
    (root / "散落的视频.mkv").write_bytes(b"x")
    (root / "空目录").mkdir()
    return root


def _library(app: FastAPI, root: Path, data_type_id: str = "tv") -> int:
    with app.state.session_factory() as session:
        library = create_library(
            session, name="电视剧", data_type_id=data_type_id, paths=[str(root)]
        )
        library.is_auto_scrape = 0
        session.commit()
        return library.id


def _organize(app: FastAPI, library_id: int) -> dict:
    with app.state.session_factory() as session:
        task = repo.enqueue(session, "organize", {"library_id": library_id})
        session.commit()
        task_id = task.id
    app.state.task_runner.run_once()
    with app.state.session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None and task.status == "succeeded", task and task.error
        return json.loads(task.result or "{}")


def test_scan_shows_groups_by_show_folder(shows: Path) -> None:
    result = scan_shows(
        [str(shows)],
        video_extensions=[".mp4", ".mkv"],
        subtitle_extensions=[".ass"],
        skip_folders=[],
    )
    assert [(m.name, m.parsed.title, len(m.videos), len(m.subtitles)) for m in result.movies] == [
        ("毒枭：墨西哥", "毒枭:墨西哥", 6, 1)
    ]
    assert result.errors == [f"库目录 {shows} 下有 1 个视频没放在剧目录里，跳过"]


def test_organize_identifies_show_and_binds_videos(app: FastAPI, shows: Path) -> None:
    library_id = _library(app, shows)
    result = _organize(app, library_id)
    assert (result["found"], result["matched"], result["added"]) == (1, 1, 7)
    with app.state.session_factory() as session:
        items = session.scalars(select(Item)).all()
        assert [(i.kind, i.data_type_id, i.title, i.release_date) for i in items] == [
            ("work", "tv", "毒枭：墨西哥", "2018-11-16")
        ]
        bound = session.scalars(select(MediaFile).where(MediaFile.item_id == items[0].id)).all()
        assert len(bound) == 7


def test_rescan_keeps_episode_binding_and_puts_new_files_on_show(app: FastAPI, shows: Path) -> None:
    library_id = _library(app, shows)
    _organize(app, library_id)
    with app.state.session_factory() as session:
        show = session.scalars(select(Item)).one()
        now = utc_now()
        season = Item(
            library_id=library_id,
            data_type_id="tv",
            parent_id=show.id,
            kind="season",
            index_number=1,
            title="第 1 季",
            created_at=now,
            updated_at=now,
        )
        session.add(season)
        session.flush()
        episode = Item(
            library_id=library_id,
            data_type_id="tv",
            parent_id=season.id,
            kind="episode",
            index_number=1,
            title="卡美洛",
            created_at=now,
            updated_at=now,
        )
        session.add(episode)
        session.flush()
        first = session.scalars(select(MediaFile).where(MediaFile.path.like("%S01E01%.mp4"))).one()
        first.item_id = episode.id
        show_id, episode_id = show.id, episode.id
        session.commit()
    new = shows / "毒枭：墨西哥" / "S02" / EPISODE.format(season=2, episode=4)
    new.write_bytes(b"x")

    assert _organize(app, library_id)["matched"] == 0
    with app.state.session_factory() as session:
        assert session.scalars(select(Item).where(Item.kind == "work")).all()[0].id == show_id
        rows = {Path(r.path).name: r.item_id for r in session.scalars(select(MediaFile))}
        assert rows[EPISODE.format(season=1, episode=1)] == episode_id
        assert rows[new.name] == show_id


def test_movie_library_does_not_search_tv(app: FastAPI, shows: Path, fake: FakeTmdb) -> None:
    library_id = _library(app, shows, data_type_id="movie")
    _organize(app, library_id)
    assert "/3/search/tv" not in fake.paths()
    with app.state.session_factory() as session:
        assert session.scalars(select(Item)).all() == []
