import gzip
from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.config.store import ConfigStore
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve
from tests.fakes import FakeTmdb

pytestmark = pytest.mark.e2e
SLOW = 20_000

RATINGS = "tconst\taverageRating\tnumVotes\ntt0133093\t8.7\t2100000\ntt0113277\t8.3\t700000\n"


class FakeServices(FakeTmdb):
    """TMDB 照旧回放；IMDb 数据集回一份两条的小样本，其余外站一律 404，不连外网。"""

    def __init__(self) -> None:
        super().__init__()
        self.dataset_requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "datasets.imdbws.com":
            self.dataset_requests += 1
            return httpx.Response(200, content=gzip.compress(RATINGS.encode("utf-8")))
        if request.url.host not in ("api.themoviedb.org", "image.tmdb.org"):
            return httpx.Response(404)
        return super().__call__(request)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def fake() -> FakeServices:
    return FakeServices()


@pytest.fixture
def base_url(data_dir: Path, fake: FakeServices) -> Iterator[str]:
    ConfigStore(data_dir).save_secrets(Secrets(sources={"tmdb": {"api_key": "e2e-key"}}))
    app = create_app(Settings(data_dir=data_dir, web_dist=WEB_DIST), http_transport=fake.transport)
    with serve(app) as url:
        yield url


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield browser.new_page(viewport={"width": 1400, "height": 1000})
        finally:
            browser.close()


def test_fanart_key_makes_source_available(page: Page, base_url: str, data_dir: Path) -> None:
    page.goto(base_url + "/settings?section=source.fanart")
    expect(page.get_by_role("heading", name="Fanart.tv")).to_be_visible()
    expect(page.get_by_text("不可用：Key 未填")).to_be_visible()

    page.get_by_label("项目 Key", exact=True).fill("fanart-e2e-key")
    page.get_by_role("button", name="保存项目 Key").click()
    expect(page.get_by_text("项目 Key已保存")).to_be_visible()
    expect(page.get_by_text("可用", exact=True)).to_be_visible()
    expect(page.get_by_text("不可用：Key 未填")).to_have_count(0)

    # 电影只差 Fanart.tv 要 Key；填好以后类型页的来源列表和来源顺序里都不再有「Key 未填」。
    page.get_by_role("link", name="电影", exact=True).click()
    expect(page.get_by_role("heading", name="电影 来源顺序")).to_be_visible()
    expect(page.get_by_text("评分数据还没下载").first).to_be_visible()
    expect(page.get_by_text("Key 未填")).to_have_count(0)
    secrets = ConfigStore(data_dir).load_secrets().sources["fanart"]
    assert secrets["api_key"] == "fanart-e2e-key"
    config = data_dir / "config.toml"
    assert not config.exists() or "fanart-e2e-key" not in config.read_text(encoding="utf-8")


def test_imdb_refresh_shows_count(page: Page, base_url: str, fake: FakeServices) -> None:
    page.goto(base_url + "/settings?section=source.imdb")
    expect(page.get_by_text("不可用：评分数据还没下载")).to_be_visible()
    expect(page.get_by_text("还没下载", exact=True)).to_be_visible()

    page.get_by_role("button", name="立即更新").click()
    expect(page.get_by_label("IMDb 评分条数")).to_have_text("2", timeout=SLOW)
    expect(page.get_by_text("最新", exact=True)).to_be_visible()
    expect(page.get_by_text("可用", exact=True)).to_be_visible(timeout=SLOW)
    assert fake.dataset_requests == 1

    page.get_by_label("更新间隔").fill("14")
    page.get_by_role("button", name="保存 IMDb 设置").click()
    expect(page.get_by_text("IMDb 设置已保存")).to_be_visible()
    page.reload()
    expect(page.get_by_label("更新间隔")).to_have_value("14 天")


def test_builtin_source_orders_listed(page: Page, base_url: str) -> None:
    page.goto(base_url + "/settings?section=type.movie")
    expect(page.get_by_role("heading", name="电影 来源顺序")).to_be_visible()
    for name in ("TMDB", "Fanart.tv", "IMDb", "烂番茄"):
        expect(page.get_by_role("button", name=f"上移{name}", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="上移TVDB")).to_have_count(0)
    expect(page.get_by_text("不可用：Key 未填").first).to_be_visible()
    expect(page.get_by_text("不可用：评分数据还没下载").first).to_be_visible()

    page.goto(base_url + "/settings?section=type.tv")
    expect(page.get_by_role("heading", name="电视剧 来源顺序")).to_be_visible()
    for name in ("TMDB", "TVDB", "Fanart.tv", "IMDb", "烂番茄"):
        expect(page.get_by_role("button", name=f"上移{name}", exact=True).first).to_be_visible()
