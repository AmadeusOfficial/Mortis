"""浏览器里走一遍插件导入：非莫提斯 zip、安全弹窗、覆盖、卸载、钩子加标签、熔断后重新启用。"""

import json
import re
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.config.store import ConfigStore
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve
from tests.fakes import FakeTmdb, hit, load_fixture

pytestmark = pytest.mark.e2e
SLOW = 30_000
ROOT = Path(__file__).resolve().parents[1]
TAG_RULES = "example.tag_rules"


def pack(folder: str, out: Path) -> Path:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "pack_plugin.py"),
            str(ROOT / folder),
            "-o",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return next(out.glob("*.zip"))


def fake_tmdb() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add_search("The Matrix", [hit(603, "黑客帝国", "1999-03-30", "The Matrix")])
    movie = {**load_fixture("movie_603.json"), "runtime": 170}  # 超过 150 分钟：示例规则给「长片」
    fake.add("/3/movie/603", movie)
    fake.add("/3/collection/2344", load_fixture("collection_2344.json"))
    fake.add("/3/collection/2344/images", load_fixture("collection_2344_images.json"))
    return fake


@pytest.fixture
def movies(tmp_path: Path) -> Path:
    path = tmp_path / "影片" / "The Matrix (1999)" / "The.Matrix.1999.mkv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    return path.parent.parent


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    ConfigStore(path).save_secrets(Secrets(sources={"tmdb": {"api_key": "e2e-key"}}))
    return path


@pytest.fixture
def base_url(data_dir: Path) -> Iterator[str]:
    app = create_app(
        Settings(data_dir=data_dir, web_dist=WEB_DIST), http_transport=fake_tmdb().transport
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


def api(base_url: str, method: str, path: str, **kwargs: Any) -> Any:
    response = httpx.request(method, base_url + "/api" + path, timeout=20, **kwargs)
    assert response.status_code < 300, response.text
    return response.json() if response.content else None


def keywords(base_url: str, item_id: int) -> list[str]:
    response = httpx.get(f"{base_url}/api/items/{item_id}", timeout=20)
    if response.status_code == 404:  # 整理任务还没建出条目
        return []
    assert response.status_code < 300, response.text
    detail = response.json()
    for section in detail["sections"]:
        for field in section["fields"]:
            if field["key"] == "keywords":
                return list(field["value"] or [])
    return []


def wait_for(check: Any, what: str) -> Any:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.3)
    pytest.fail(f"等不到：{what}")


def scrape_movie(base_url: str, movies: Path) -> int:
    library = api(
        base_url,
        "POST",
        "/libraries",
        json={"name": "电影", "data_type_id": "movie", "paths": [str(movies)]},
    )
    api(base_url, "POST", f"/libraries/{library['id']}/organize", json={"scrape": True})
    wait_for(lambda: "长片" in keywords(base_url, 1), "钩子给条目加上「长片」")
    return 1


def idle(base_url: str) -> bool:
    rows = api(base_url, "GET", "/tasks")
    rows = rows["items"] if isinstance(rows, dict) else rows
    return all(row["status"] not in ("queued", "running") for row in rows)


def install_through_page(page: Page, zip_path: Path) -> None:
    page.set_input_files("input[type=file]", str(zip_path))
    dialog = page.get_by_role("dialog", name="导入插件")
    expect(dialog).to_be_visible(timeout=SLOW)
    confirm = dialog.get_by_role("button", name=re.compile("^确认(导入|覆盖)$"))
    expect(confirm).to_be_disabled()  # 没勾选「我信任这个插件」不能确认
    dialog.get_by_label("我信任这个插件").check()
    expect(confirm).to_be_enabled()
    confirm.click()


def test_import_hook_tags_override_and_uninstall(
    page: Page, base_url: str, movies: Path, tmp_path: Path, data_dir: Path
) -> None:
    out = tmp_path / "zips"
    out.mkdir()
    rules_zip = pack("examples/plugins/tag_rules", out)
    foreign = tmp_path / "foreign.zip"
    with zipfile.ZipFile(foreign, "w") as archive:
        archive.writestr("plugin.toml", 'id = "foreign.thing"\nname = "外来"\nversion = "1"\n')
        archive.writestr("__init__.py", "PLUGIN = None\n")

    page.goto(base_url + "/settings?section=plugins")
    # 非莫提斯 zip：被拒绝，提示清楚，不弹安装窗。
    page.set_input_files("input[type=file]", str(foreign))
    expect(page.get_by_text("这个压缩包不能导入")).to_be_visible(timeout=SLOW)
    expect(page.get_by_text("这不是莫提斯插件包").first).to_be_visible()
    expect(page.get_by_role("dialog", name="导入插件")).to_have_count(0)

    # 导入 tag_rules：预览里有权限声明，勾选信任才能装。
    page.set_input_files("input[type=file]", str(rules_zip))
    dialog = page.get_by_role("dialog", name="导入插件")
    expect(dialog).to_be_visible(timeout=SLOW)
    expect(dialog.get_by_text("权限声明（可写）：tags:keyword")).to_be_visible()
    expect(dialog.get_by_text("订阅事件：scrape.finished")).to_be_visible()
    expect(dialog.get_by_text("导入的插件是在本机运行的 Python 代码")).to_be_visible()
    confirm = dialog.get_by_role("button", name="确认导入")
    expect(confirm).to_be_disabled()
    dialog.get_by_label("我信任这个插件").check()
    confirm.click()
    expect(page.get_by_text("已安装 示例：按规则补标签")).to_be_visible(timeout=SLOW)
    expect(page.get_by_text("钩子 example.tag_rules_tagger：订阅 scrape.finished")).to_be_visible()

    # 重复导入：覆盖对比、需要重启。
    page.set_input_files("input[type=file]", str(rules_zip))
    dialog = page.get_by_role("dialog", name="导入插件")
    expect(dialog.get_by_text("已装有这个插件，将覆盖")).to_be_visible(timeout=SLOW)
    expect(dialog.get_by_text("内容完全相同")).to_be_visible()
    expect(dialog.get_by_text("覆盖升级要重启莫提斯才完全生效。").first).to_be_visible()
    dialog.get_by_label("我信任这个插件").check()
    dialog.get_by_role("button", name="确认覆盖").click()
    expect(page.get_by_text("已覆盖 示例：按规则补标签")).to_be_visible(timeout=SLOW)
    expect(page.get_by_text("这个插件被覆盖升级过")).to_be_visible()

    # 刮削：钩子加「长片」，详情、NFO 都有；重新刮削后还在。
    item_id = scrape_movie(base_url, movies)
    wait_for(lambda: idle(base_url), "任务跑完")
    assert "长片" in keywords(base_url, item_id)
    nfo = movies / "The Matrix (1999)" / "movie.nfo"
    wait_for(
        lambda: nfo.is_file() and "<tag>长片</tag>" in nfo.read_text(encoding="utf-8"), "NFO 有标签"
    )
    page.goto(f"{base_url}/items/{item_id}")
    expect(page.get_by_text("长片", exact=True).first).to_be_visible(timeout=SLOW)

    api(base_url, "POST", f"/items/{item_id}/scrape", json={"force": True})
    wait_for(lambda: idle(base_url), "重新刮削跑完")
    assert "长片" in keywords(base_url, item_id)

    # 用户在详情页手动删掉「长片」：再刮削不会被加回。
    page.reload()
    page.get_by_role("button", name="编辑标签").click()
    edit = page.get_by_role("dialog", name=re.compile("修改"))
    edit.locator(".mantine-Pill-root", has_text="长片").locator(".mantine-Pill-remove").click()
    edit.get_by_role("button", name="保存").click()
    wait_for(lambda: "长片" not in keywords(base_url, item_id), "用户删除后标签消失")
    api(base_url, "POST", f"/items/{item_id}/scrape", json={"force": True})
    wait_for(lambda: idle(base_url), "第三次刮削跑完")
    assert "长片" not in keywords(base_url, item_id)
    expect(page.get_by_text("长片", exact=True)).to_have_count(0)

    # 卸载。
    page.goto(base_url + "/settings?section=plugins")
    page.get_by_role("button", name="卸载").first.click()
    page.get_by_role("dialog").get_by_role("button", name="卸载").click()
    expect(page.get_by_text("已卸载 示例：按规则补标签")).to_be_visible(timeout=SLOW)
    expect(page.get_by_text("钩子 example.tag_rules_tagger")).to_have_count(0)
    assert not any((data_dir / "plugins").glob("*tag_rules*"))


FAILING = """
from mortis.plugin_api import HookPlugin, Plugin


class Boom(HookPlugin):
    id = "e2e.boom_hook"
    name = "故意失败"

    def on_event(self, host, event):
        raise RuntimeError("故意失败")


PLUGIN = Plugin(hooks=(Boom(),))
"""


def test_fuse_shows_reason_and_can_be_reenabled(
    page: Page, data_dir: Path, movies: Path, tmp_path: Path
) -> None:
    folder = data_dir / "plugins" / "e2e_boom"
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(
        'id = "e2e.boom"\nname = "故意失败的钩子"\nversion = "1"\n[mortis]\napi_version = 1\n'
        '[[hooks]]\nid = "e2e.boom_hook"\nevent = "scrape.finished"\n',
        encoding="utf-8",
    )
    (folder / "__init__.py").write_text(FAILING, encoding="utf-8")
    app = create_app(
        Settings(data_dir=data_dir, web_dist=WEB_DIST), http_transport=fake_tmdb().transport
    )
    with serve(app) as base_url:
        library = api(
            base_url,
            "POST",
            "/libraries",
            json={"name": "电影", "data_type_id": "movie", "paths": [str(movies)]},
        )
        api(base_url, "POST", f"/libraries/{library['id']}/organize", json={"scrape": True})
        wait_for(lambda: idle(base_url) and keywords(base_url, 1), "首次刮削")
        for _ in range(2):
            api(base_url, "POST", "/items/1/scrape", json={"force": True})
            time.sleep(0.5)
            wait_for(lambda: idle(base_url), "再刮削")
        page.goto(base_url + "/settings?section=plugins")
        expect(page.get_by_text("连续失败 3 次，已自动停用")).to_be_visible(timeout=SLOW)
        expect(page.get_by_text("已停用", exact=True)).to_be_visible()
        page.get_by_role("button", name="重新启用").click()
        expect(page.get_by_text("已重新启用钩子 e2e.boom_hook")).to_be_visible(timeout=SLOW)
        expect(page.get_by_role("button", name="重新启用")).to_have_count(0)
        assert json.dumps(api(base_url, "GET", "/plugins"))
