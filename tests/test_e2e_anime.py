import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.config.models import Secrets, TvOutputConfig, TvScrapeConfig
from mortis.config.store import ConfigStore
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve
from tests.fakes import FakeTmdb, tv_hit

pytestmark = pytest.mark.e2e
SLOW = 20_000
FOLDER = "赌博默示录：破戒录篇 (2011)"
EPISODE = "[DBD-Raws][赌博默示录：破戒录篇][{:02d}][1080P][BDRip][HEVC-10bit][FLAC].mkv"


def _season(number: int, air_date: str, name: str, prefix: str) -> dict[str, Any]:
    return {
        "season_number": number,
        "name": name,
        "air_date": air_date,
        "episodes": [
            {
                "id": number * 1000 + n,
                "season_number": number,
                "episode_number": n,
                "name": f"{prefix}{n}",
                "air_date": air_date,
            }
            for n in range(1, 27)
        ],
    }


SHOW = {
    "id": 42951,
    "name": "赌博默示录",
    "original_name": "逆境無頼カイジ",
    "original_language": "ja",
    "first_air_date": "2007-10-03",
    "seasons": [
        {"season_number": 1, "name": "赌博默示录", "air_date": "2007-10-03", "episode_count": 26},
        {"season_number": 2, "name": "赌博破戒录", "air_date": "2011-04-06", "episode_count": 26},
    ],
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "动漫"
    folder = root / FOLDER
    folder.mkdir(parents=True)
    for number in (1, 2, 3):
        (folder / EPISODE.format(number)).write_bytes(b"x")
    (folder / "[DBD-Raws][赌博默示录：破戒录篇][NCOP][1080P].mkv").write_bytes(b"x")
    return root


@pytest.fixture
def base_url(tmp_path: Path) -> Iterator[str]:
    fake = FakeTmdb()
    fake.add_search("赌博默示录", [tv_hit(42951, "赌博默示录", "2007-10-03", "逆境無頼カイジ")])
    fake.add("/3/tv/42951", SHOW)
    fake.add("/3/tv/42951/season/1", _season(1, "2007-10-03", "赌博默示录", "一季"))
    fake.add("/3/tv/42951/season/2", _season(2, "2011-04-06", "赌博破戒录", "二季"))
    data_dir = tmp_path / "data"
    store = ConfigStore(data_dir)
    store.save_secrets(Secrets(sources={"tmdb": {"api_key": "e2e-key"}}))
    store.save_section(
        "types.anime", TvScrapeConfig(output=TvOutputConfig(organize_after_scrape=False))
    )
    app = create_app(Settings(data_dir=data_dir, web_dist=WEB_DIST), http_transport=fake.transport)
    with serve(app) as url:
        yield url


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def test_anime_sequel_folder_lands_in_its_season(page: Page, base_url: str, root: Path) -> None:
    page.goto(base_url + "/libraries")
    page.get_by_role("button", name="新建媒体库", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("数据类型").click()
    page.get_by_role("option", name="动漫").click()
    expect(dialog.get_by_label("库名")).to_have_value("动漫")
    dialog.get_by_label("目录").fill(str(root))
    dialog.get_by_role("button", name="保存").click()

    page.get_by_role("button", name="扫描").click()
    expect(page.get_by_text("「动漫」扫描结束")).to_be_visible(timeout=SLOW)
    expect(page.get_by_text("1 部动漫", exact=True)).to_be_visible()

    page.get_by_role("link", name=re.compile(r"^进入媒体库")).first.click()
    page.get_by_role("link", name="赌博默示录").first.click()
    expect(page.get_by_role("heading", name="赌博默示录", level=1)).to_be_visible()
    second = page.get_by_role("region", name="赌博破戒录", exact=True)
    expect(second.get_by_text("26 集 · 有文件 3")).to_be_visible(timeout=SLOW)
    first = page.get_by_role("region", name="赌博默示录", exact=True)
    expect(first.get_by_text("26 集 · 有文件 0")).to_be_visible()

    page.get_by_role("button", name="整理", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_text("赌博默示录.S02E01.二季1.mkv").first).to_be_visible(timeout=SLOW)
    dialog.get_by_role("button", name="确认整理").click()
    expect(page.get_by_text("整理完成")).to_be_visible(timeout=SLOW)
    show = root / "赌博默示录 (2007)"
    assert (show / "Season 2" / "赌博默示录.S02E01.二季1.mkv").is_file()
    assert (show / "[DBD-Raws][赌博默示录：破戒录篇][NCOP][1080P].mkv").is_file()
    assert not (root / FOLDER).exists()
