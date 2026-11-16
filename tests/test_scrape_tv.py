import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import Secrets, TvImages, TvScrapeConfig
from mortis.db.models import Item, ItemExternalId, ItemImage, ItemSourceRecord, MediaFile, Task
from mortis.libraries import create_library
from mortis.settings import Settings
from mortis.tasks import repo
from tests.fakes import FakeTmdb, load_fixture, tv_hit

EPISODE = "毒枭：墨西哥.Narcos.Mexico.S0{season}E0{episode}.中英字幕.BD.1080P-人人影视"


def _season_two() -> dict:
    raw = copy.deepcopy(load_fixture("tv_80968_season_1.json"))
    raw["season_number"], raw["name"], raw["air_date"] = 2, "第 2 季", "2020-02-13"
    for episode in raw["episodes"]:
        episode["season_number"] = 2
        episode["name"] = f"二季{episode['episode_number']}"
    return raw


@pytest.fixture
def fake() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add_search("毒枭:墨西哥", [tv_hit(80968, "毒枭：墨西哥", "2018-11-16", "Narcos: Mexico")])
    fake.add("/3/tv/80968", load_fixture("tv_80968.json"))
    fake.add("/3/tv/80968/season/1", load_fixture("tv_80968_season_1.json"))
    fake.add("/3/tv/80968/season/2", _season_two())
    fake.add("/3/tv/80968/season/3", load_fixture("tv_80968_season_3.json"))
    return fake


@pytest.fixture
def app(tmp_path: Path, fake: FakeTmdb) -> FastAPI:
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(tmdb_api_key="k"))
    return app


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "电视剧"
    for season in (1, 2):
        for episode in (1, 2, 3):
            path = (
                root
                / "毒枭：墨西哥"
                / f"S0{season}"
                / f"{EPISODE.format(season=season, episode=episode)}.mp4"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
    (root / "毒枭：墨西哥" / "S01" / f"{EPISODE.format(season=1, episode=1)}.chs.ass").write_bytes(
        b"x"
    )
    (root / "毒枭：墨西哥" / "花絮.mp4").write_bytes(b"x")
    return root


def _run(app: FastAPI, type_: str, payload: dict) -> Task:
    with app.state.session_factory() as session:
        task = repo.enqueue(session, type_, payload)
        session.commit()
        task_id = task.id
    app.state.task_runner.run_until_idle()
    with app.state.session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        session.expunge(task)
        return task


@pytest.fixture
def show_id(app: FastAPI, root: Path) -> int:
    with app.state.session_factory() as session:
        library = create_library(session, name="电视剧", data_type_id="tv", paths=[str(root)])
        library.is_auto_scrape = 0
        session.commit()
        library_id = library.id
    assert _run(app, "organize", {"library_id": library_id}).status == "succeeded"
    with app.state.session_factory() as session:
        return session.scalars(select(Item.id).where(Item.kind == "work")).one()


def _scrape(app: FastAPI, show_id: int, force: bool = False) -> Task:
    task = _run(app, "scrape", {"item_id": show_id, "force": force})
    assert task.status == "succeeded", task.error
    return task


def _tree(app: FastAPI, show_id: int) -> dict[int, dict[int, str]]:
    with app.state.session_factory() as session:
        tree: dict[int, dict[int, str]] = {}
        for season in session.scalars(
            select(Item).where(Item.parent_id == show_id, Item.kind == "season")
        ):
            tree[season.index_number] = {
                episode.index_number: episode.title
                for episode in session.scalars(select(Item).where(Item.parent_id == season.id))
            }
        return tree


def _owners(app: FastAPI) -> dict[str, tuple[str, int | None]]:
    """文件名 → (挂的条目类型, 集号)。"""
    with app.state.session_factory() as session:
        return {
            Path(row.path).name: (item.kind, item.index_number)
            for row, item in session.execute(
                select(MediaFile, Item).join(Item, Item.id == MediaFile.item_id)
            )
        }


def test_scrape_builds_seasons_episodes_and_binds_files(
    app: FastAPI, show_id: int, fake: FakeTmdb
) -> None:
    task = _scrape(app, show_id)
    assert json.loads(task.result or "{}")["episodes"] == 30
    tree = _tree(app, show_id)
    assert sorted(tree) == [1, 2, 3]
    assert [len(tree[n]) for n in (1, 2, 3)] == [10, 10, 10]
    assert tree[1][1] == "卡美洛"
    assert tree[3][1] == "12 步"

    owners = _owners(app)
    first = EPISODE.format(season=1, episode=1)
    assert owners[f"{first}.mp4"] == ("episode", 1)
    assert owners[f"{first}.chs.ass"] == ("episode", 1)
    assert owners["花絮.mp4"] == ("work", None)

    with app.state.session_factory() as session:
        show = session.get(Item, show_id)
        assert show is not None
        assert (show.title, show.release_date) == ("毒枭：墨西哥", "2018-11-16")
        assert json.loads(show.fields)["status"] == "已完结"
        ids = dict(
            session.execute(
                select(ItemExternalId.provider, ItemExternalId.external_id).where(
                    ItemExternalId.item_id == show_id
                )
            ).all()
        )
        assert ids == {"tmdb": "80968", "imdb": "tt8714904", "tvdb": "353232"}
        season = session.scalars(
            select(Item).where(Item.parent_id == show_id, Item.index_number == 1)
        ).one()
        assert season.poster_path is not None
        assert session.get(ItemSourceRecord, (season.id, "tmdb")) is not None
        episode = session.scalars(
            select(Item).where(Item.parent_id == season.id, Item.index_number == 1)
        ).one()
        assert session.scalars(
            select(ItemImage.image_kind).where(ItemImage.item_id == episode.id)
        ).all() == ["thumb"]


def test_snapshots_are_reused_and_rescan_keeps_bindings(
    app: FastAPI, show_id: int, fake: FakeTmdb
) -> None:
    _scrape(app, show_id)
    count = len([p for p in fake.paths() if p.startswith("/3/tv/")])
    _scrape(app, show_id)
    assert len([p for p in fake.paths() if p.startswith("/3/tv/")]) == count

    with app.state.session_factory() as session:
        library_id = session.get(Item, show_id).library_id  # type: ignore[union-attr]
    assert _run(app, "organize", {"library_id": library_id}).status == "succeeded"
    assert _owners(app)[f"{EPISODE.format(season=2, episode=3)}.mp4"] == ("episode", 3)

    _scrape(app, show_id, force=True)
    assert len([p for p in fake.paths() if p.startswith("/3/tv/")]) == count + 4


def test_without_missing_episodes_only_local_ones(app: FastAPI, show_id: int) -> None:
    app.state.config_store.save_section("tv", TvScrapeConfig(show_missing_episodes=False))
    _scrape(app, show_id)
    assert {n: sorted(e) for n, e in _tree(app, show_id).items()} == {1: [1, 2, 3], 2: [1, 2, 3]}


def test_locked_episode_title_and_disabled_thumbs(
    app: FastAPI, show_id: int, fake: FakeTmdb
) -> None:
    _scrape(app, show_id)
    with app.state.session_factory() as session:
        season = session.scalars(
            select(Item).where(Item.parent_id == show_id, Item.index_number == 1)
        ).one()
        episode = session.scalars(
            select(Item).where(Item.parent_id == season.id, Item.index_number == 1)
        ).one()
        episode.title = "我的集名"
        episode.locked_fields = json.dumps(["title"])
        session.commit()
    app.state.config_store.save_section("tv", TvScrapeConfig(images=TvImages(episode_thumb=False)))
    before = len(fake.paths())
    _scrape(app, show_id, force=True)
    assert _tree(app, show_id)[1][1] == "我的集名"
    assert not any("/w300/" in path for path in fake.paths()[before:])
