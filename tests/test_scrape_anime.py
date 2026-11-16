# ruff: noqa: F401, F811  # 共用 test_scrape_tv 的夹具和假 TMDB
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from mortis.config.models import Secrets, TvOutputConfig, TvScrapeConfig
from mortis.db.models import Item, MediaFile
from mortis.libraries import create_library
from mortis.settings import Settings
from tests.fakes import FakeTmdb
from tests.test_scrape_tv import (
    EPISODE,
    _owners,
    _run,
    _scrape,
    _season_two,
    _tree,
    app,
    fake,
    load_fixture,
    root,
    show_id,
    tv_hit,
)


@pytest.fixture
def anime_app(tmp_path: Path, fake: FakeTmdb) -> FastAPI:
    """和剧一样的数据和假 TMDB，只是媒体库是动漫。"""
    from mortis.api.app import create_app

    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    app.state.config_store.save_section("types.tv", TvScrapeConfig())
    app.state.config_store.save_section(
        "types.anime", TvScrapeConfig(output=TvOutputConfig(organize_after_scrape=False))
    )
    return app


@pytest.fixture
def anime_show_id(anime_app: FastAPI, root: Path) -> int:
    with anime_app.state.session_factory() as session:
        library = create_library(session, name="动漫", data_type_id="anime", paths=[str(root)])
        library.is_auto_scrape = 0
        session.commit()
        library_id = library.id
    assert _run(anime_app, "organize", {"library_id": library_id}).status == "succeeded"
    with anime_app.state.session_factory() as session:
        return session.scalars(
            select(Item.id).where(Item.kind == "work", Item.data_type_id == "anime")
        ).one()


def test_scan_and_scrape_anime_series(
    anime_app: FastAPI, anime_show_id: int, fake: FakeTmdb
) -> None:
    task = _scrape(anime_app, anime_show_id)
    import json

    assert json.loads(task.result or "{}")["episodes"] == 30
    tree = _tree(anime_app, anime_show_id)
    assert sorted(tree) == [1, 2, 3]
    assert tree[1][1] == "卡美洛"
    with anime_app.state.session_factory() as session:
        show = session.get(Item, anime_show_id)
        assert show is not None
        assert show.data_type_id == "anime"
        seasons = list(
            session.scalars(
                select(Item).where(Item.parent_id == anime_show_id, Item.kind == "season")
            )
        )
        assert all(season.data_type_id == "anime" for season in seasons)
        episodes = list(
            session.scalars(select(Item).where(Item.parent_id.in_([s.id for s in seasons])))
        )
        assert all(episode.data_type_id == "anime" for episode in episodes)
    assert "/3/search/tv" in fake.paths()
    assert "/3/search/movie" not in fake.paths()

    owners = _owners(anime_app)
    first = EPISODE.format(season=1, episode=1)
    assert owners[f"{first}.mp4"] == ("episode", 1)
    assert owners[f"{first}.chs.ass"] == ("episode", 1)
    assert owners["花絮.mp4"] == ("work", None)


def test_anime_uses_its_own_file_template(
    anime_app: FastAPI, anime_show_id: int, root: Path
) -> None:
    _scrape(anime_app, anime_show_id)
    anime_app.state.config_store.save_section(
        "types.anime",
        TvScrapeConfig(
            output=TvOutputConfig(
                organize_after_scrape=False,
                organize_rename=True,
                file_template="${showTitle} - ${seasonNr2}x${episodeNr2}",
            )
        ),
    )
    task = _run(anime_app, "arrange", {"item_id": anime_show_id})
    assert task.status == "succeeded", task.error

    folder = root / "毒枭：墨西哥 (2018)"
    names = {p.name for p in folder.rglob("*.mp4")}
    # 文件名里的空格按 anime 自己的 file_space="." 换成点
    assert "毒枭：墨西哥.01x01.mp4" in names
    assert "毒枭：墨西哥.02x03.mp4" in names
    # 电视剧的模板没被动漫设置影响
    tv = anime_app.state.config_store.load_config().types.tv
    assert tv.output.file_template == "${showTitle}.S${seasonNr2}E${episodeNr2}.${title}"
    with anime_app.state.session_factory() as session:
        paths = [path for path in session.scalars(select(MediaFile.path)) if path.endswith(".mp4")]
    assert any(Path(path).name == "毒枭：墨西哥.01x01.mp4" for path in paths)
