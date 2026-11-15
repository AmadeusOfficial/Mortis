import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.settings import Settings

pytestmark = pytest.mark.e2e

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def base_url(data_dir: Path) -> Iterator[str]:
    if not (WEB_DIST / "index.html").is_file():
        pytest.fail("先在 web 目录运行 npm run build")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    app = create_app(Settings(data_dir=data_dir, web_dist=WEB_DIST), http_transport=transport)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            pytest.fail("测试服务器 15 秒内没有启动")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def test_create_edit_delete_library(page: Page, base_url: str, tmp_path: Path) -> None:
    movies = tmp_path / "影片" / "电影"
    more = tmp_path / "影片" / "单片"
    movies.mkdir(parents=True)
    more.mkdir(parents=True)

    page.goto(base_url + "/")
    expect(page).to_have_url(base_url + "/libraries")
    expect(page.get_by_text("还没有媒体库。")).to_be_visible()

    page.get_by_role("button", name="新建媒体库").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("目录").fill(str(movies))
    dialog.get_by_role("button", name="保存").click()
    expect(page.get_by_text(str(movies))).to_be_visible()

    page.get_by_role("button", name="新建媒体库").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("库名").fill("重复的库")
    dialog.get_by_label("目录").fill(str(movies))
    dialog.get_by_role("button", name="保存").click()
    expect(dialog.get_by_text("重复或互相包含")).to_be_visible()
    dialog.get_by_role("button", name="取消").click()

    page.reload()
    expect(page.get_by_text(str(movies))).to_be_visible()

    page.get_by_role("button", name="编辑").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("库名").fill("电影库")
    dialog.get_by_label("目录").fill(f"{movies}\n{more}")
    dialog.get_by_role("button", name="保存").click()
    expect(page.get_by_text(str(more))).to_be_visible()
    expect(page.get_by_role("heading", name="电影库")).to_be_visible()

    page.get_by_role("button", name="删除").click()
    page.get_by_role("dialog").get_by_role("button", name="确认删除").click()
    expect(page.get_by_text("还没有媒体库。")).to_be_visible()
    assert movies.is_dir() and more.is_dir()


def test_settings_save_secret_and_check(page: Page, base_url: str, data_dir: Path) -> None:
    page.goto(base_url + "/settings")
    expect(page.get_by_label("代理主机")).to_have_value("127.0.0.1")

    page.get_by_label("代理端口").fill("1080")
    page.get_by_role("button", name="保存网络设置").click()
    expect(page.get_by_text("网络设置已保存")).to_be_visible()

    # 按钮的无障碍名称里也含「TMDB API Key」，要精确匹配标签。
    page.get_by_label("TMDB API Key", exact=True).fill("e2e-key")
    page.get_by_role("button", name="保存TMDB API Key").click()
    expect(page.get_by_text("TMDB API Key已保存")).to_be_visible()
    expect(page.get_by_text("已填写。输入新值会覆盖，留空不修改。")).to_be_visible()

    page.get_by_role("button", name="测试连接").click()
    expect(page.get_by_text("连接成功").first).to_be_visible()

    page.reload()
    expect(page.get_by_label("代理端口")).to_have_value("1080")
    secrets_text = (data_dir / "secrets.toml").read_text(encoding="utf-8")
    config_text = (data_dir / "config.toml").read_text(encoding="utf-8")
    assert "e2e-key" in secrets_text
    assert "e2e-key" not in config_text
    assert "proxy_port = 1080" in config_text
