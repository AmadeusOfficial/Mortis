import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mortis.api.app import create_app
from mortis.config.models import Secrets, TvImages, TvOutputConfig, TvScrapeConfig
from mortis.db.models import (
    Item,
    ItemExternalId,
    ItemImage,
    ItemSourceRecord,
    Library,
    MediaFile,
    Task,
)
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
    app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    app.state.config_store.save_section("types.tv", _config())
    return app


def _config(**values: object) -> TvScrapeConfig:
    """测试里刮削后不自动整理，文件名保持原样。"""
    return TvScrapeConfig(output=TvOutputConfig(organize_after_scrape=False), **values)


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


def test_result_records_tmdb_and_seasons(app: FastAPI, show_id: int) -> None:
    first = _scrape(app, show_id)
    first_sources = json.loads(first.result or "{}")["sources"]
    assert [s for s in first_sources if s["id"].startswith("tmdb")] == [
        {"id": "tmdb", "name": "TMDB 剧集", "outcome": "ok", "detail": None},
        {"id": "tmdb_season_1", "name": "TMDB 第 1 季", "outcome": "ok", "detail": None},
        {"id": "tmdb_season_2", "name": "TMDB 第 2 季", "outcome": "ok", "detail": None},
        {"id": "tmdb_season_3", "name": "TMDB 第 3 季", "outcome": "ok", "detail": None},
    ]
    assert [(s["id"], s["outcome"]) for s in first_sources[1:5]] == [
        ("tvdb", "skipped"),
        ("fanart", "skipped"),
        ("imdb", "skipped"),
        ("ratings_rt", "failed"),
    ]
    second = _scrape(app, show_id)
    second_sources = json.loads(second.result or "{}")["sources"]
    assert [(s["id"], s["outcome"]) for s in second_sources if s["id"].startswith("tmdb")] == [
        ("tmdb", "cached"),
        ("tmdb_season_1", "cached"),
        ("tmdb_season_2", "cached"),
        ("tmdb_season_3", "cached"),
    ]


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
    assert len([p for p in fake.paths() if p.startswith("/3/tv/")]) == count * 2


def test_detail_lists_seasons_and_missing_episodes(app: FastAPI, show_id: int) -> None:
    _scrape(app, show_id)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        detail = client.get(f"/api/items/{show_id}").json()
        assert [(s["number"], s["episode_count"], s["file_count"]) for s in detail["seasons"]] == [
            (1, 10, 3),
            (2, 10, 3),
            (3, 10, 0),
        ]
        first = detail["seasons"][0]["episodes"][0]
        assert (first["number"], first["title"], first["has_file"]) == (1, "卡美洛", True)
        assert first["still_url"].startswith("/api/images/")
        assert detail["seasons"][0]["episodes"][3]["has_file"] is False
        assert [Path(f["path"]).name for f in detail["files"]] == ["花絮.mp4"]
        assert (detail["show"], detail["position"]) == (None, None)

        season = client.get(f"/api/items/{detail['seasons'][0]['id']}").json()
        assert (season["kind"], season["position"], season["show"]["id"]) == (
            "season",
            "第 1 季",
            show_id,
        )
        assert season["collection"] is None and [
            s["number"] for s in season["seasons"]
        ] == [1, 2, 3]
        episode = client.get(f"/api/items/{first['id']}").json()
        assert (episode["title"], episode["position"]) == ("卡美洛", "第 1 季 第 1 集")
        assert episode["season"]["id"] == season["id"]
        assert episode["show"]["title"] == "毒枭：墨西哥"
        assert [f["file_kind"] for f in episode["files"]] == ["video", "subtitle"]
        assert {s["name"] for s in episode["sections"]} >= {"基本信息", "演职员"}
        assert episode["poster_url"] == first["still_url"]
        assert episode["scraped_at"] is not None and season["scraped_at"] is not None
        basic = next(s for s in episode["sections"] if s["name"] == "基本信息")
        original = next(f for f in basic["fields"] if f["key"] == "original_title")
        assert original["value"] == "卡美洛"


def test_without_missing_episodes_only_local_ones(app: FastAPI, show_id: int) -> None:
    app.state.config_store.save_section("types.tv", _config(show_missing_episodes=False))
    _scrape(app, show_id)
    assert {n: sorted(e) for n, e in _tree(app, show_id).items()} == {1: [1, 2, 3], 2: [1, 2, 3]}


def test_library_switch_hides_missing_and_show_wins(app: FastAPI, show_id: int) -> None:
    _scrape(app, show_id)
    with app.state.session_factory() as session:
        show = session.get(Item, show_id)
        assert show is not None
        library = session.get(Library, show.library_id)
        assert library is not None
        library.show_missing_episodes = 0
        session.commit()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        detail = client.get(f"/api/items/{show_id}").json()
        assert detail["show_missing_episodes"] is None
        assert detail["show_missing_effective"] is False
        assert detail["seasons"]
        assert all(
            episode["has_file"] for season in detail["seasons"] for episode in season["episodes"]
        )
        saved = client.put(
            f"/api/items/{show_id}/missing-episodes", json={"show_missing_episodes": True}
        )
        assert saved.status_code == 200, saved.text
        shown = saved.json()
        assert shown["show_missing_episodes"] is True
        assert shown["show_missing_effective"] is True
        assert any(
            not episode["has_file"] for season in shown["seasons"] for episode in season["episodes"]
        )


def test_show_override_keeps_missing_when_library_hides(app: FastAPI, show_id: int) -> None:
    with app.state.session_factory() as session:
        show = session.get(Item, show_id)
        assert show is not None
        library = session.get(Library, show.library_id)
        assert library is not None
        library.show_missing_episodes = 0
        show.show_missing_episodes = 1
        session.commit()
    _scrape(app, show_id)
    tree = _tree(app, show_id)
    assert len(tree[1]) == 10
    assert 3 in tree


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
    app.state.config_store.save_section("types.tv", _config(images=TvImages(episode_thumb=False)))
    before = len(fake.paths())
    _scrape(app, show_id, force=True)
    assert _tree(app, show_id)[1][1] == "我的集名"
    assert not any("/w300/" in path for path in fake.paths()[before:])


def test_forced_rescrape_reuses_downloaded_images(
    app: FastAPI, show_id: int, fake: FakeTmdb
) -> None:
    _scrape(app, show_id)
    assert any("/t/p/" in path for path in fake.paths())
    before = len(fake.paths())
    _scrape(app, show_id, force=True)
    assert [p for p in fake.paths()[before:] if "/t/p/" in p] == []
    with app.state.session_factory() as session:
        assert session.scalar(select(Item.poster_path).where(Item.id == show_id))


def test_season_and_show_image_choices(app: FastAPI, show_id: int) -> None:
    _scrape(app, show_id)
    with app.state.session_factory() as session:
        season_id = session.scalars(
            select(Item.id).where(Item.parent_id == show_id, Item.index_number == 1)
        ).one()
        episode_id = session.scalars(select(Item.id).where(Item.parent_id == season_id)).first()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        rows = client.get(f"/api/items/{season_id}/images/poster/choices").json()
        assert len(rows) > 1 and rows[0]["selected"] and rows[0]["source"] == "tmdb"
        detail = client.put(
            f"/api/items/{season_id}/images/poster", json={"url": rows[1]["url"]}
        ).json()
        assert detail["poster_url"] is not None
        logos = client.get(f"/api/items/{show_id}/images/clearlogo/choices").json()
        assert logos[0]["language"] == "zh" and logos[0]["region"] == "CN"
        thumbs = client.get(f"/api/items/{episode_id}/images/thumb/choices").json()
        assert thumbs and thumbs[0]["source"] == "tmdb"
    _scrape(app, show_id)
    with app.state.session_factory() as session:
        season = session.get(Item, season_id)
        assert season is not None and "poster" in json.loads(season.locked_fields)


def test_scan_scrapes_show_that_got_new_episodes(
    app: FastAPI, show_id: int, root: Path, fake: FakeTmdb
) -> None:
    _scrape(app, show_id)
    new = root / "毒枭：墨西哥" / "S03" / "Narcos.Mexico.S03E01.1080p.mp4"
    new.parent.mkdir()
    new.write_bytes(b"x")
    with app.state.session_factory() as session:
        library = session.scalars(select(Item.library_id).where(Item.id == show_id)).one()
        session.get(Library, library).is_auto_scrape = 1
        session.commit()
    before = len(fake.paths())
    assert _run(app, "organize", {"library_id": library}).status == "succeeded"
    assert _owners(app)["Narcos.Mexico.S03E01.1080p.mp4"] == ("episode", 1)
    assert not any(path.startswith("/3/tv/") for path in fake.paths()[before:])


def test_multi_episode_file_covers_every_episode(app: FastAPI, show_id: int, root: Path) -> None:
    (root / "毒枭：墨西哥" / "S01" / "Narcos.Mexico.S01E04E05.mp4").write_bytes(b"x")
    with app.state.session_factory() as session:
        library = session.scalars(select(Item.library_id).where(Item.id == show_id)).one()
    assert _run(app, "organize", {"library_id": library}).status == "succeeded"
    app.state.config_store.save_section("types.tv", _config(show_missing_episodes=False))
    _scrape(app, show_id)
    assert sorted(_tree(app, show_id)[1]) == [1, 2, 3, 4, 5]
    with TestClient(app, base_url="http://127.0.0.1") as client:
        season = client.get(f"/api/items/{show_id}").json()["seasons"][0]
        assert [e["has_file"] for e in season["episodes"]] == [True] * 5
        assert season["file_count"] == 5


def test_long_season_keeps_undated_episodes_that_have_files(
    app: FastAPI, show_id: int, root: Path, fake: FakeTmdb
) -> None:
    """长寿番后段在 TMDB 上没有播出日期：有文件的照样建集，没文件的不算已播出。"""
    raw = copy.deepcopy(load_fixture("tv_80968_season_1.json"))
    template = raw["episodes"][0]
    raw["episodes"] = [
        {
            **template,
            "id": 9_000_000 + n,
            "episode_number": n,
            "name": f"第{n}话",
            "air_date": "2018-01-01" if n <= 1473 else None,
            "still_path": None,
        }
        for n in range(1, 2491)
    ]
    fake.add("/3/tv/80968/season/1", raw)
    folder = root / "毒枭：墨西哥" / "S01"
    for n in range(4, 2485):
        (folder / f"Narcos.Mexico.S01E{n:02d}.mp4").write_bytes(b"x")
    with app.state.session_factory() as session:
        library = session.scalars(select(Item.library_id).where(Item.id == show_id)).one()
    assert _run(app, "organize", {"library_id": library}).status == "succeeded"

    _scrape(app, show_id)
    assert sorted(_tree(app, show_id)[1]) == list(range(1, 2485))
    owners = _owners(app)
    assert owners["Narcos.Mexico.S01E1000.mp4"] == ("episode", 1000)
    assert owners["Narcos.Mexico.S01E1474.mp4"] == ("episode", 1474)
    assert owners["Narcos.Mexico.S01E2484.mp4"] == ("episode", 2484)


def test_season_gone_from_tmdb_is_dropped(app: FastAPI, show_id: int, fake: FakeTmdb) -> None:
    _scrape(app, show_id)
    assert 3 in _tree(app, show_id)
    raw = copy.deepcopy(load_fixture("tv_80968.json"))
    raw["seasons"] = [s for s in raw["seasons"] if s["season_number"] != 3]
    fake.add("/3/tv/80968", raw)
    _scrape(app, show_id, force=True)
    assert sorted(_tree(app, show_id)) == [1, 2]
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Item)) == 1 + 2 + 20


def test_refresh_running_shows_refetches_only_tmdb(
    app: FastAPI, show_id: int, fake: FakeTmdb
) -> None:
    _scrape(app, show_id)
    with app.state.session_factory() as session:
        show = session.get(Item, show_id)
        assert show is not None
        library_id = show.library_id
        show.fields = json.dumps({**json.loads(show.fields), "status": "连载中"})
        ended = Item(
            library_id=library_id,
            data_type_id="tv",
            kind="work",
            title="完结的剧",
            fields=json.dumps({"status": "已完结"}),
            created_at=show.created_at,
            updated_at=show.updated_at,
        )
        session.add(ended)
        session.flush()
        session.add(ItemExternalId(item_id=ended.id, provider="tmdb", external_id="1"))
        session.commit()
    before = len([p for p in fake.paths() if p.startswith("/3/tv/")])

    task = _run(app, "refresh_running_shows", {"library_id": library_id})
    assert task.status == "succeeded", task.error
    assert json.loads(task.result or "{}") == {"scrape_queued": 1}
    with app.state.session_factory() as session:
        child = session.scalars(select(Task).where(Task.parent_task_id == task.id)).one()
        assert child.status == "succeeded", child.error
        assert json.loads(child.payload or "{}") == {"item_id": show_id, "refresh": ["tmdb"]}
        sources = {s["id"]: s["outcome"] for s in json.loads(child.result or "{}")["sources"]}
    assert sources["tmdb"] == "ok"
    assert sources["tmdb_season_1"] == "ok"
    assert len([p for p in fake.paths() if p.startswith("/3/tv/")]) == before * 2


def test_episode_runtime_comes_from_tmdb(app: FastAPI, show_id: int) -> None:
    _scrape(app, show_id)
    with app.state.session_factory() as session:
        season = session.scalars(
            select(Item).where(Item.parent_id == show_id, Item.index_number == 1)
        ).one()
        episode = session.scalars(
            select(Item).where(Item.parent_id == season.id, Item.index_number == 1)
        ).one()
        assert json.loads(episode.fields)["runtime"] > 0
