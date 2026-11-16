from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mortis.api.app import create_app
from mortis.config.models import Secrets, TvOutputConfig, TvScrapeConfig
from mortis.db.models import Item, MediaFile
from mortis.libraries import create_library
from mortis.settings import Settings
from mortis.tasks import repo
from tests.fakes import FakeTmdb, load_fixture, tv_hit

EPISODE = "Narcos.Mexico.S0{season}E0{episode}.1080p.mkv"


@pytest.fixture
def fake() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add("/3/tv/80968", load_fixture("tv_80968.json"))
    fake.add("/3/tv/80968/season/1", load_fixture("tv_80968_season_1.json"))
    fake.add("/3/tv/80968/season/2", load_fixture("tv_80968_season_1.json") | {"episodes": []})
    fake.add("/3/tv/80968/season/3", load_fixture("tv_80968_season_3.json"))
    fake.add("/3/tv/999", {"id": 999, "name": "别的剧", "first_air_date": "2001-01-01"})
    fake.add_search("毒枭", [tv_hit(80968, "毒枭：墨西哥", "2018-11-16", "Narcos: Mexico")])
    return fake


@pytest.fixture
def app(tmp_path: Path, fake: FakeTmdb) -> FastAPI:
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(tmdb_api_key="k"))
    app.state.config_store.save_section(
        "tv", TvScrapeConfig(output=TvOutputConfig(organize_after_scrape=False))
    )
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def library_id(app: FastAPI, tmp_path: Path) -> int:
    root = tmp_path / "剧"
    for season, episode in ((1, 1), (1, 2), (2, 1)):
        path = (
            root
            / "毒枭 第三部"
            / f"Season {season}"
            / EPISODE.format(season=season, episode=episode)
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"xx")
    (root / "毒枭 第三部" / "Season 1" / "Narcos.Mexico.S01E01.1080p.zh.srt").write_bytes(b"x")
    (root / "另一部" / "a.mkv").parent.mkdir(parents=True)
    (root / "另一部" / "a.mkv").write_bytes(b"x")
    with app.state.session_factory() as session:
        library = create_library(session, name="剧", data_type_id="tv", paths=[str(root)])
        library.is_auto_scrape = 0
        session.commit()
        library_id = library.id
    with app.state.session_factory() as session:
        repo.enqueue(session, "organize", {"library_id": library_id})
        session.commit()
    app.state.task_runner.run_until_idle()
    return library_id


def _owners(app: FastAPI) -> dict[str, str | None]:
    with app.state.session_factory() as session:
        return {
            Path(row.path).name: kind
            for row, kind in session.execute(
                select(MediaFile, Item.kind).outerjoin(Item, Item.id == MediaFile.item_id)
            )
        }


def test_unmatched_groups_by_show_folder(client: TestClient, library_id: int) -> None:
    rows = client.get("/api/media-files/unmatched", params={"library_id": library_id}).json()
    assert [(Path(r["path"]).name, r["guess_title"], r["video_count"]) for r in rows] == [
        ("另一部", "另一部", 1),
        ("毒枭 第三部", "毒枭 第三部", 3),
    ]
    assert rows[1]["size"] == 6


def test_bind_show_then_rematch(
    app: FastAPI, client: TestClient, library_id: int, fake: FakeTmdb
) -> None:
    row = client.get("/api/media-files/unmatched", params={"library_id": library_id}).json()[1]
    hits = client.get("/api/tmdb/search/tv", params={"query": "毒枭"}).json()
    assert [(h["tmdb_id"], h["title"], h["year"]) for h in hits] == [(80968, "毒枭：墨西哥", 2018)]
    bound = client.post(
        f"/api/media-files/{row['id']}/bind",
        json={"tmdb_id": 80968, "title": "毒枭：墨西哥", "release_date": "2018-11-16"},
    )
    assert bound.status_code == 202
    show_id = bound.json()["item_id"]
    owners = _owners(app)
    assert owners["Narcos.Mexico.S01E02.1080p.mkv"] == "work"
    assert owners["Narcos.Mexico.S01E01.1080p.zh.srt"] == "work"
    assert owners["a.mkv"] is None

    app.state.task_runner.run_until_idle()
    owners = _owners(app)
    assert owners["Narcos.Mexico.S01E02.1080p.mkv"] == "episode"
    assert owners["Narcos.Mexico.S01E01.1080p.zh.srt"] == "episode"
    assert owners["Narcos.Mexico.S02E01.1080p.mkv"] == "work"

    assert client.post(f"/api/items/{show_id}/rematch", json={"tmdb_id": 999}).status_code == 202
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).where(Item.parent_id == show_id)) == 0
    assert set(_owners(app).values()) == {"work", None}
    app.state.task_runner.run_until_idle()
    with app.state.session_factory() as session:
        show = session.get(Item, show_id)
        assert show is not None and show.title == "别的剧"


def test_season_cannot_be_scraped_or_rematched(
    app: FastAPI, client: TestClient, library_id: int
) -> None:
    row = client.get("/api/media-files/unmatched", params={"library_id": library_id}).json()[1]
    show_id = client.post(
        f"/api/media-files/{row['id']}/bind", json={"tmdb_id": 80968, "title": "毒枭：墨西哥"}
    ).json()["item_id"]
    app.state.task_runner.run_until_idle()
    season_id = client.get(f"/api/items/{show_id}").json()["seasons"][0]["id"]
    scrape = client.post(f"/api/items/{season_id}/scrape")
    assert (scrape.status_code, scrape.json()["detail"]) == (422, "季和集跟着剧一起刮削")
    rematch = client.post(f"/api/items/{season_id}/rematch", json={"tmdb_id": 1})
    assert rematch.status_code == 422
