import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.config.store import ConfigStore
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve
from tests.fakes import FakeTmdb, hit, load_fixture

pytestmark = pytest.mark.e2e
SLOW = 20_000


@pytest.fixture
def movies(tmp_path: Path) -> Path:
    root = tmp_path / "影片"
    for rel in ["The Matrix (1999)/The.Matrix.1999.mkv", "热火 1995/heat.mkv"]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return root


@pytest.fixture
def base_url(tmp_path: Path) -> Iterator[str]:
    fake = FakeTmdb()
    fake.add_search(
        "The Matrix", [hit(603, "黑客帝国", "1999-03-30", "The Matrix", "/zh-poster.jpg")]
    )
    fake.add_search("Heat", [hit(949, "盗火线", "1995-12-15", "Heat")])
    fake.add("/3/movie/603", load_fixture("movie_603.json"))
    fake.add("/3/collection/2344", load_fixture("collection_2344.json"))
    fake.add("/3/collection/2344/images", load_fixture("collection_2344_images.json"))
    heat = {
        "id": 949,
        "title": "盗火线",
        "original_title": "Heat",
        "release_date": "1995-12-15",
        "overview": "洛杉矶的警匪对决。",
    }
    fake.add("/3/movie/949", heat)
    data_dir = tmp_path / "data"
    ConfigStore(data_dir).save_secrets(Secrets(sources={"tmdb": {"api_key": "e2e-key"}}))
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


def test_scan_scrape_edit_and_bind(page: Page, base_url: str, movies: Path) -> None:
    page.goto(base_url + "/libraries")
    page.get_by_role("button", name="新建媒体库").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("目录").fill(str(movies))
    dialog.get_by_role("button", name="保存").click()

    page.get_by_role("button", name="扫描").click()
    expect(page.get_by_text("「电影」扫描结束")).to_be_visible(timeout=SLOW)
    expect(page.get_by_text("1 部电影 · 2 个视频")).to_be_visible()

    page.get_by_role("link", name="任务").click()
    scan_row = page.get_by_role("button", name=re.compile(r"^扫描媒体库 "))
    expect(scan_row).to_be_visible(timeout=SLOW)
    scan_row.click()
    scrape_row = page.get_by_role("button", name=re.compile(r"^刮削 黑客帝国 "))
    expect(scrape_row).to_be_visible(timeout=SLOW)
    scrape_row.click()
    item_link = page.get_by_text("查看《黑客帝国》")
    expect(item_link).to_be_visible(timeout=SLOW)
    # 刮削子任务自己的日志，根任务的日志里没有这一句。
    expect(page.get_by_text("刮削完成").first).to_be_visible(timeout=SLOW)
    item_link.click()
    expect(page.get_by_role("heading", name="黑客帝国")).to_be_visible(timeout=SLOW)

    page.get_by_role("link", name="媒体库").click()
    page.get_by_role("link", name="打开").click()
    page.get_by_role("link", name="黑客帝国").first.click()
    expect(page.get_by_role("heading", name="黑客帝国")).to_be_visible()
    expect(page.get_by_text("欢迎来到真实的世界")).to_be_visible()
    expect(page.get_by_text("基努·里维斯", exact=True)).to_be_visible()
    expect(page.get_by_text("系列：黑客帝国（系列）")).to_be_visible()

    page.get_by_role("button", name="编辑片名").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("片名").fill("骇客任务")
    dialog.get_by_role("button", name="保存").click()
    expect(page.get_by_role("heading", name="骇客任务")).to_be_visible()
    expect(page.get_by_role("button", name="解锁片名")).to_be_visible()

    page.get_by_role("button", name="刮削", exact=True).click()
    expect(page.get_by_text("刮削完成")).to_be_visible(timeout=SLOW)
    expect(page.get_by_role("heading", name="骇客任务")).to_be_visible()

    page.get_by_role("link", name="未识别").click()
    page.get_by_role("button", name="匹配heat.mkv").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("片名").fill("Heat")
    dialog.get_by_role("button", name="搜索").click()
    dialog.get_by_role("button", name="选这部").click()
    expect(page.get_by_text("没有未识别的文件。")).to_be_visible(timeout=SLOW)

    page.get_by_role("link", name="媒体库").click()
    page.get_by_role("link", name="打开").click()
    expect(page.get_by_role("link", name="盗火线").first).to_be_visible(timeout=SLOW)


def test_arrange_writes_files_and_renames(
    page: Page, base_url: str, movies: Path, tmp_path: Path
) -> None:
    page.goto(base_url + "/libraries")
    page.get_by_role("button", name="新建媒体库").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("目录").fill(str(movies))
    dialog.get_by_role("button", name="保存").click()
    page.get_by_role("button", name="扫描").click()
    expect(page.get_by_text("「电影」扫描结束")).to_be_visible(timeout=SLOW)
    old = movies / "The Matrix (1999)"
    page.get_by_role("link", name="打开").click()
    page.get_by_role("link", name="黑客帝国").first.click()
    expect(page.get_by_role("heading", name="黑客帝国")).to_be_visible()
    expect(page.get_by_text(str(old / "The.Matrix.1999.mkv"))).to_be_visible(timeout=SLOW)
    for _ in range(40):
        if (old / "movie.nfo").is_file():
            break
        page.wait_for_timeout(500)
    assert (old / "movie.nfo").is_file()
    expect(page.get_by_text("movie.nfo")).to_have_count(0)
    expect(page.get_by_text(".actors")).to_have_count(0)

    store = ConfigStore(tmp_path / "data")
    movie = store.load_config().types.movie
    movie.output.organize_rename = True
    store.save_section("types.movie", movie)

    page.get_by_role("button", name="整理", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_text("按模板重命名目录和文件")).to_be_visible(timeout=SLOW)
    expect(dialog.get_by_text("黑客帝国 (1999)")).to_be_visible()
    expect(dialog.get_by_text("黑客帝国.mkv")).to_be_visible()
    dialog.get_by_role("button", name="确认整理").click()
    expect(page.get_by_text("整理完成")).to_be_visible(timeout=SLOW)
    folder = movies / "黑客帝国 (1999)"
    expect(page.get_by_text(str(folder / "黑客帝国.mkv"))).to_be_visible()
    assert (folder / "黑客帝国.nfo").is_file() and (folder / "黑客帝国-poster.jpg").is_file()
    assert not old.exists()
