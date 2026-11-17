import copy
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.config.models import Secrets, TvOutputConfig, TvScrapeConfig
from mortis.config.store import ConfigStore
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve
from tests.fakes import FakeTmdb, load_fixture, tv_hit

pytestmark = pytest.mark.e2e
SLOW = 20_000
EPISODE = "毒枭：墨西哥.Narcos.Mexico.S0{season}E0{episode}.中英字幕.BD.1080P-人人影视.mp4"


@pytest.fixture
def shows(tmp_path: Path) -> Path:
    root = tmp_path / "剧集"
    for season in (1, 2):
        for episode in (1, 2, 3):
            path = (
                root
                / "毒枭：墨西哥"
                / f"0{season}"
                / EPISODE.format(season=season, episode=episode)
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
    return root


@pytest.fixture
def base_url(tmp_path: Path) -> Iterator[str]:
    fake = FakeTmdb()
    fake.add_search("毒枭:墨西哥", [tv_hit(80968, "毒枭：墨西哥", "2018-11-16", "Narcos: Mexico")])
    fake.add("/3/tv/80968", load_fixture("tv_80968.json"))
    fake.add("/3/tv/80968/season/1", load_fixture("tv_80968_season_1.json"))
    season_two = copy.deepcopy(load_fixture("tv_80968_season_1.json"))
    season_two["season_number"], season_two["name"] = 2, "第 2 季"
    for episode in season_two["episodes"]:
        episode["name"] = f"二季{episode['episode_number']}"
    fake.add("/3/tv/80968/season/2", season_two)
    fake.add("/3/tv/80968/season/3", load_fixture("tv_80968_season_3.json"))
    data_dir = tmp_path / "data"
    store = ConfigStore(data_dir)
    store.save_secrets(Secrets(sources={"tmdb": {"api_key": "e2e-key"}}))
    store.save_section(
        "types.tv", TvScrapeConfig(output=TvOutputConfig(organize_after_scrape=False))
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


def test_tv_scan_scrape_and_arrange(page: Page, base_url: str, shows: Path) -> None:
    page.goto(base_url + "/libraries")
    page.get_by_role("button", name="新建媒体库", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("数据类型").click()
    page.get_by_role("option", name="电视剧").click()
    expect(dialog.get_by_label("库名")).to_have_value("电视剧")
    dialog.get_by_label("目录").fill(str(shows))
    dialog.get_by_role("button", name="保存").click()

    page.get_by_role("button", name="扫描").click()
    expect(page.get_by_text("「电视剧」扫描结束")).to_be_visible(timeout=SLOW)
    expect(page.get_by_text("1 部剧", exact=True)).to_be_visible()

    page.get_by_role("link", name=re.compile(r"^进入媒体库")).first.click()
    page.get_by_role("link", name="毒枭：墨西哥").first.click()
    expect(page.get_by_role("heading", name="毒枭：墨西哥")).to_be_visible()
    season = page.get_by_role("region", name="第 1 季", exact=True)
    expect(season.get_by_text("10 集 · 有文件 3")).to_be_visible(timeout=SLOW)
    third = page.get_by_role("region", name="第 3 季", exact=True)
    expect(third.get_by_text("10 集 · 有文件 0")).to_be_visible()
    expect(season.get_by_role("link", name=re.compile("卡美洛"))).to_be_attached()
    expect(page.get_by_text("缺", exact=True).first).to_be_visible()

    page.get_by_role("button", name="整理", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_text("毒枭：墨西哥 (2018)").first).to_be_visible(timeout=SLOW)
    expect(dialog.get_by_text("毒枭：墨西哥.S01E01.卡美洛.mp4").first).to_be_visible()
    dialog.get_by_role("button", name="确认整理").click()
    expect(page.get_by_text("整理完成")).to_be_visible(timeout=SLOW)
    folder = shows / "毒枭：墨西哥 (2018)"
    assert (folder / "Season 1" / "毒枭：墨西哥.S01E01.卡美洛.mp4").is_file()
    assert (folder / "tvshow.nfo").is_file()
    assert not (shows / "毒枭：墨西哥").exists()

    page.get_by_text("卡美洛").click()
    expect(page.get_by_role("heading", name="卡美洛")).to_be_visible()
    expect(page.get_by_text("毒枭：墨西哥 · 第 1 季 第 1 集")).to_be_visible()
