# ruff: noqa: F401, F811  # 共用 test_scrape_tv 的夹具
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from mortis.db.models import Item, ItemImage, MediaFile
from mortis.output.tv import write_show_files
from mortis.scrape.images import ImageStore
from tests.test_scrape_tv import EPISODE, _scrape, app, fake, root, show_id

FIRST = EPISODE.format(season=1, episode=1)


def _write(app: FastAPI, show_id: int):
    with app.state.session_factory() as session:
        show = session.get(Item, show_id)
        assert show is not None
        report = write_show_files(
            session,
            ImageStore(app.state.settings.data_dir),
            show,
            app.state.config_store.load_config().tv,
        )
        session.commit()
        return report


@pytest.fixture
def written(app: FastAPI, show_id: int, root: Path) -> Path:
    _scrape(app, show_id)
    _write(app, show_id)
    return root / "毒枭：墨西哥"


def _names(folder: Path) -> set[str]:
    return {str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*")}


def test_written_files(written: Path) -> None:
    names = _names(written)
    assert {
        "tvshow.nfo",
        "poster.jpg",
        "fanart.jpg",
        "keyart.jpg",
        "clearlogo.png",
        "season01-poster.jpg",
        "season02-poster.jpg",
        "season03-poster.jpg",
        "S01/season.nfo",
        "S02/season.nfo",
        f"S01/{FIRST}.nfo",
        f"S01/{FIRST}-thumb.jpg",
    } <= names
    assert not any(name.startswith("花絮") and name != "花絮.mp4" for name in names)
    assert len([n for n in names if n.endswith("-thumb.jpg")]) == 6
    assert len([n for n in names if n.endswith(".nfo")]) == 1 + 2 + 6


def test_nfo_contents(written: Path) -> None:
    show = ET.parse(written / "tvshow.nfo").getroot()
    assert show.findtext("title") == "毒枭：墨西哥"
    assert show.findtext("status") == "Ended"
    assert show.findtext("episodeguide") == '{"tmdb":"80968","imdb":"tt8714904","tvdb":"353232"}'
    assert [u.get("type") for u in show.findall("uniqueid")] == ["tmdb", "imdb", "tvdb"]
    assert [n.get("number") for n in show.findall("namedseason")] == ["3", "2", "1"]
    assert show.find("thumb[@aspect='poster']") is not None

    season = ET.parse(written / "S01" / "season.nfo").getroot()
    assert (season.findtext("seasonnumber"), season.findtext("title")) == ("1", "第 1 季")

    episode = ET.parse(written / "S01" / f"{FIRST}.nfo").getroot()
    assert episode.tag == "episodedetails"
    assert (episode.findtext("season"), episode.findtext("episode")) == ("1", "1")
    assert episode.findtext("title") == "卡美洛"
    assert episode.find("uniqueid[@type='tmdb']") is not None
    assert episode.findtext("director") == "Josef Kubota Wladyka"
    assert episode.findall("actor")


def test_rewrite_is_stable_and_drops_stale_thumb(
    app: FastAPI,
    show_id: int,
    written: Path,
) -> None:
    assert _write(app, show_id).written == []
    with app.state.session_factory() as session:
        thumb = session.scalars(
            select(MediaFile).where(MediaFile.path.like(f"%{FIRST}-thumb.jpg"))
        ).one()
        session.execute(
            delete(ItemImage).where(
                ItemImage.item_id == thumb.item_id, ItemImage.image_kind == "thumb"
            )
        )
        session.commit()
    report = _write(app, show_id)
    assert [Path(p).name for p in report.removed] == [f"{FIRST}-thumb.jpg"]
    assert not (written / "S01" / f"{FIRST}-thumb.jpg").exists()
    assert (written / "S01" / f"{FIRST}.nfo").exists()
