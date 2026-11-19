"""浏览器里走一遍钩子设置：导入 tag_rules 加一条标题规则，保存、刷新、刮削后生效；坏正则不保存。"""

from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect

from tests import test_e2e_plugin_import as shared

pytestmark = pytest.mark.e2e
SLOW = shared.SLOW
HOOK = "example.tag_rules_tagger"

movies = shared.movies
data_dir = shared.data_dir
base_url = shared.base_url
page = shared.page


def hook_settings(base_url: str) -> dict[str, Any]:
    for plugin in shared.api(base_url, "GET", "/plugins"):
        for hook in plugin["hooks"]:
            if hook["id"] == HOOK:
                return hook["settings"]
    pytest.fail(f"插件列表里没有钩子 {HOOK}")


def install_tag_rules(page: Page, base_url: str, tmp_path: Path) -> None:
    out = tmp_path / "zips"
    out.mkdir()
    page.goto(base_url + "/settings?section=plugins")
    shared.install_through_page(page, shared.pack("examples/plugins/tag_rules", out))
    expect(page.get_by_text("已安装 示例：按规则补标签")).to_be_visible(timeout=SLOW)


def open_hook_settings(page: Page) -> Locator:
    page.get_by_role("button", name="设置", exact=True).click()
    # 钩子设置卡片嵌在「插件」卡片里，取最里面那层。
    card = (
        page.locator("section")
        .filter(has=page.get_by_role("heading", name=f"钩子 {HOOK} 的设置", exact=True))
        .last
    )
    expect(card.get_by_role("button", name="保存钩子设置")).to_be_visible(timeout=SLOW)
    return card


def add_rule(card: Locator, pattern: str, tag: str) -> None:
    card.get_by_role("button", name="+ 添加").click()
    card.get_by_role("textbox", name="标题正则").fill(pattern)
    card.get_by_role("textbox", name="标签", exact=True).fill(tag)


def test_hook_rule_saved_and_applied(
    page: Page, base_url: str, movies: Path, tmp_path: Path
) -> None:
    install_tag_rules(page, base_url, tmp_path)
    assert hook_settings(base_url)["rules"] == []

    card = open_hook_settings(page)
    add_rule(card, "Matrix", "黑客")
    card.get_by_role("button", name="保存钩子设置").click()
    expect(page.get_by_text(f"钩子 {HOOK} 的设置已保存")).to_be_visible(timeout=SLOW)
    assert hook_settings(base_url)["rules"] == [
        {"pattern": "Matrix", "tag": "黑客", "enabled": True}
    ]

    # 刷新后表单里还是这条规则。
    page.reload()
    card = open_hook_settings(page)
    expect(card.get_by_role("textbox", name="标题正则")).to_have_value("Matrix")
    expect(card.get_by_role("textbox", name="标签", exact=True)).to_have_value("黑客")

    # 刮削《黑客帝国》：原标题 The Matrix 命中规则，标签里有「黑客」，详情页也看得到。
    item_id = shared.scrape_movie(base_url, movies)
    shared.wait_for(lambda: shared.idle(base_url), "任务跑完")
    assert "黑客" in shared.keywords(base_url, item_id)
    shared.open_details_tab(page, base_url, item_id)
    expect(page.get_by_text("黑客", exact=True).first).to_be_visible(timeout=SLOW)


def test_invalid_rule_shows_error_and_keeps_config(
    page: Page, base_url: str, tmp_path: Path
) -> None:
    install_tag_rules(page, base_url, tmp_path)
    before = hook_settings(base_url)

    card = open_hook_settings(page)
    add_rule(card, "(", "坏规则")
    card.get_by_role("button", name="保存钩子设置").click()
    expect(card.get_by_text("保存失败")).to_be_visible(timeout=SLOW)
    expect(card.get_by_text("不是合法的正则表达式").first).to_be_visible()
    assert hook_settings(base_url) == before

    page.reload()
    card = open_hook_settings(page)
    expect(card.get_by_role("textbox", name="标题正则")).to_have_count(0)
