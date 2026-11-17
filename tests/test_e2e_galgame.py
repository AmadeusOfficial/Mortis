import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.config.store import ConfigStore
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve

pytestmark = pytest.mark.e2e
SLOW = 20_000
FIXTURES = Path(__file__).parent / "fixtures" / "vndb"
PNG = b"\x89PNG\r\n\x1a\nfake"
MATCHED = "Dies irae ~Acta est Fabula~"
UNMATCHED = "神座万象シリーズ 未分类"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeVndb:
    def __init__(self) -> None:
        self.search = _fixture("search.json")
        self.vn = _fixture("vn.json")
        self.release = _fixture("release.json")

    def _detail(self, vn_id: str) -> dict[str, Any]:
        if vn_id == "v548":
            return self.vn
        hit = next(row for row in self.search["results"] if row["id"] == vn_id)
        return {"results": [{"id": vn_id, "title": hit["title"], "released": hit["released"]}]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "t.vndb.org":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if request.url.host != "api.vndb.org":
            return httpx.Response(404)
        body = json.loads(request.content)
        if request.url.path.endswith("/release"):
            return httpx.Response(200, json=self.release)
        name, _, value = body["filters"]
        if name == "search":
            return httpx.Response(200, json=self.search)
        return httpx.Response(200, json=self._detail(value))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "Galgame"
    for name in (MATCHED, UNMATCHED):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "malie.exe").write_bytes(b"x")
    return root


@pytest.fixture
def base_url(tmp_path: Path) -> Iterator[str]:
    data_dir = tmp_path / "data"
    store = ConfigStore(data_dir)
    for plugin_id in ("bangumi", "ymgal", "cngal"):
        store.save_plugin(plugin_id, disabled=True)
    fake = FakeVndb()
    app = create_app(
        Settings(data_dir=data_dir, web_dist=WEB_DIST),
        http_transport=httpx.MockTransport(fake.handler),
    )
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


GALGAME_SOURCES = ("Bangumi", "VNDB", "月幕 Galgame", "CnGal")
GALGAME_CHILDREN = (*GALGAME_SOURCES, "Galgame 启动")


@pytest.fixture
def plugins_base_url(tmp_path: Path) -> Iterator[str]:
    # 单独起一个全部来源都启用的环境，用来验证插件管理页的分组和联动。
    data_dir = tmp_path / "data"
    ConfigStore(data_dir)
    fake = FakeVndb()
    app = create_app(
        Settings(data_dir=data_dir, web_dist=WEB_DIST),
        http_transport=httpx.MockTransport(fake.handler),
    )
    with serve(app) as url:
        yield url


def test_plugins_grouped_under_data_type(page: Page, plugins_base_url: str) -> None:
    page.goto(plugins_base_url + "/settings?section=plugins")
    expect(page.get_by_role("heading", name="插件", exact=True)).to_be_visible()

    # Galgame 分组下缩进列出四个来源和「Galgame 启动」，各自有启用开关。
    galgame = page.locator('[class*="listRowCol"]', has_text="Galgame").first
    galgame_switch = galgame.get_by_role("switch").first
    group = galgame.locator('[class*="childSources"]')
    switches = group.get_by_role("switch")
    expect(switches).to_have_count(len(GALGAME_CHILDREN))
    for name in GALGAME_CHILDREN:
        expect(group.get_by_text(name, exact=True)).to_be_visible()
    for index in range(len(GALGAME_CHILDREN)):
        expect(switches.nth(index)).to_be_enabled()
    # 按文本定位 CnGal 那一行的开关，不依赖来源在列表里的顺序。
    cngal_row = group.locator("xpath=./div").filter(has_text="CnGal")
    cngal_switch = cngal_row.get_by_role("switch")

    # 停用 Galgame：父开关和所有子开关都未选中且子开关禁用，并注明「随 Galgame 停用」。
    galgame_switch.click()
    expect(page.get_by_text("已停用 Galgame")).to_be_visible(timeout=SLOW)
    expect(galgame_switch).not_to_be_checked()
    for index in range(len(GALGAME_CHILDREN)):
        expect(switches.nth(index)).to_be_disabled()
        expect(switches.nth(index)).not_to_be_checked()
    expect(group.get_by_text("随 Galgame 停用")).to_have_count(len(GALGAME_CHILDREN))

    # 左侧「来源」导航里 Galgame 的来源随父停用变为不可用（状态点 data-ok="false"）。
    nav = page.get_by_role("navigation", name="设置分区")
    for name in GALGAME_SOURCES:
        dot = nav.get_by_role("link", name=name, exact=True).locator('[class*="navDot"]')
        expect(dot).to_have_attribute("data-ok", "false")

    # 启用 Galgame 后恢复可操作，单独停用 CnGal。
    galgame_switch.click()
    expect(page.get_by_text("已启用 Galgame")).to_be_visible(timeout=SLOW)
    expect(cngal_switch).to_be_enabled(timeout=SLOW)
    cngal_switch.click()
    expect(page.get_by_text("已停用 CnGal")).to_be_visible(timeout=SLOW)

    # 再停用又启用 Galgame，CnGal 仍保持自己的停用状态。
    galgame_switch.click()
    expect(page.get_by_text("已停用 Galgame").last).to_be_visible(timeout=SLOW)
    galgame_switch.click()
    expect(page.get_by_text("已启用 Galgame").last).to_be_visible(timeout=SLOW)
    # 等列表刷新到父已启用：CnGal 重新可操作，但仍是停用状态。
    expect(cngal_switch).to_be_enabled(timeout=SLOW)
    expect(cngal_switch).not_to_be_checked()

    # 恢复：把 CnGal 重新启用。
    cngal_switch.click()
    expect(page.get_by_text("已启用 CnGal")).to_be_visible(timeout=SLOW)
    expect(cngal_switch).to_be_checked()


def test_galgame_folders_scrape_from_vndb(page: Page, base_url: str, root: Path) -> None:
    page.goto(base_url + "/libraries")
    page.get_by_role("button", name="新建媒体库", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("数据类型").click()
    page.get_by_role("option", name="Galgame").click()
    expect(dialog.get_by_label("库名")).to_have_value("Galgame")
    dialog.get_by_label("目录").fill(str(root))
    dialog.get_by_role("button", name="保存").click()

    page.get_by_role("button", name="扫描").click()
    expect(page.get_by_text("「Galgame」扫描结束")).to_be_visible(timeout=SLOW)
    expect(page.get_by_text("1 款游戏", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="整理整库")).to_have_count(0)

    page.get_by_role("link", name="未识别").click()
    page.get_by_role("button", name=f"匹配{UNMATCHED}").click()
    panel = page.get_by_role("complementary", name="匹配面板")
    sources = panel.get_by_role("group", name="来源")
    expect(sources.get_by_role("button", name="VNDB")).to_have_attribute("aria-pressed", "true")
    panel.get_by_label("名称").fill("Paradise Lost")
    panel.get_by_role("button", name="搜索").click()
    panel.get_by_role("button", name="选Paradise Lost", exact=True).click()
    expect(page.get_by_text("已匹配为《Paradise Lost》")).to_be_visible(timeout=SLOW)
    expect(page.get_by_text("没有未识别的文件。")).to_be_visible()

    page.goto(base_url + "/libraries")
    expect(page.get_by_text("2 款游戏", exact=True)).to_be_visible(timeout=SLOW)
    page.get_by_role("link", name=re.compile(r"^进入媒体库")).first.click()
    page.get_by_role("link", name=MATCHED).first.click()
    expect(page.get_by_role("heading", name=MATCHED)).to_be_visible()
    expect(page.get_by_text("VNDB v548")).to_be_visible()
    expect(page.get_by_text("上次刮削")).to_be_visible(timeout=SLOW)
    expect(page.get_by_text("Greenwood Co., Ltd.").first).to_be_visible()
    page.get_by_role("tab", name=re.compile(r"^文件")).click()
    expect(page.get_by_text("游戏文件夹")).to_be_visible()
    expect(page.get_by_role("button", name="整理", exact=True)).to_have_count(0)
