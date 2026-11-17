import re
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from playwright.sync_api import Locator, Page, expect, sync_playwright
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.db.models import Schedule, Task
from mortis.libraries import create_library, update_library
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve

pytestmark = pytest.mark.e2e


class World:
    def __init__(self, app: FastAPI, root: Path) -> None:
        self.app = app
        self.root = root

    def library(self, name: str, data_type: str = "movie") -> tuple[int, Path]:
        folder = self.root / name
        folder.mkdir(parents=True)
        with self.app.state.session_factory() as session:
            library = create_library(
                session, name=name, data_type_id=data_type, paths=[str(folder)]
            )
            return library.id, folder

    def scans(self, library_id: int) -> list[str]:
        with self.app.state.session_factory() as session:
            return [
                task.status
                for task in session.scalars(select(Task).where(Task.task_type == "organize"))
                if f'"library_id": {library_id}' in (task.payload or "")
            ]

    def schedules(self) -> list[tuple[str, str]]:
        with self.app.state.session_factory() as session:
            return [(row.task_type, row.cron) for row in session.scalars(select(Schedule))]


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[World, str]]:
    app = create_app(
        Settings(data_dir=tmp_path / "data", web_dist=WEB_DIST),
        start_worker=False,
        start_automation=True,
        watch_debounce=0.5,
    )
    with serve(app) as url:
        yield World(app, tmp_path / "片库"), url


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield browser.new_page(viewport={"width": 1400, "height": 1000})
        finally:
            browser.close()


def _card(page: Page, name: str) -> Locator:
    return page.locator("article").filter(has=page.get_by_role("heading", name=name, exact=True))


def _edit(page: Page, name: str) -> Locator:
    _card(page, name).get_by_role("button", name="更多操作").click()
    page.get_by_role("menuitem", name="编辑").click()
    dialog = page.get_by_role("dialog", name=f"编辑「{name}」")
    expect(dialog).to_be_visible()
    return dialog


def test_schedule_shows_next_scan_on_card(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    state.library("剧", "tv")
    page.goto(f"{url}/libraries")
    expect(_card(page, "剧").get_by_label("扫描方式")).to_have_text("· 只手动扫描")

    dialog = _edit(page, "剧")
    dialog.get_by_role("radio", name="定时扫描").check()
    expect(dialog.get_by_label("下次扫描时间")).to_contain_text("下次扫描")
    dialog.get_by_label("什么时候扫描").click()
    page.get_by_role("option", name="自定义 cron").click()
    rule = dialog.get_by_label("cron 规则")
    rule.fill("0 25 * * *")
    expect(dialog.get_by_label("定时规则错误")).to_have_text("规则不对：「小时」的 25 超出 0~23")
    rule.fill("30 2 * * *")
    expect(dialog.get_by_label("下次扫描时间")).to_contain_text(re.compile(r"02:30"))
    dialog.get_by_label("同时刷新连载中的剧").check()
    dialog.get_by_role("button", name="保存").click()
    expect(dialog).to_be_hidden()

    status = _card(page, "剧").get_by_label("扫描方式")
    expect(status).to_have_text(re.compile(r"^· 下次扫描 (今天|明天) 02:30$"))
    assert sorted(state.schedules()) == [
        ("organize", "30 2 * * *"),
        ("refresh_running_shows", "30 2 * * *"),
    ]

    dialog = _edit(page, "剧")
    expect(dialog.get_by_role("radio", name="定时扫描")).to_be_checked()
    expect(dialog.get_by_label("cron 规则")).to_have_value("30 2 * * *")
    expect(dialog.get_by_label("同时刷新连载中的剧")).to_be_checked()
    dialog.get_by_role("radio", name="只手动扫描").check()
    dialog.get_by_role("button", name="保存").click()
    expect(status).to_have_text("· 只手动扫描")
    assert state.schedules() == []


def test_watch_queues_scan_when_video_appears(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    library_id, folder = state.library("电影")
    page.goto(f"{url}/libraries")
    dialog = _edit(page, "电影")
    dialog.get_by_role("radio", name="发现变动就扫描").check()
    expect(dialog.get_by_label("什么时候扫描")).to_have_count(0)
    dialog.get_by_role("button", name="保存").click()
    expect(dialog).to_be_hidden()
    expect(_card(page, "电影").get_by_label("扫描方式")).to_have_text("· 正在监听")

    # 等监听线程真正挂上读取再放文件
    manager = state.app.state.watch_manager
    deadline = time.monotonic() + 5
    while (status := manager.status(library_id)) is None or status.opening:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    (folder / "黑客帝国 (1999)").mkdir()
    (folder / "黑客帝国 (1999)" / "The.Matrix.1999.mkv").write_bytes(b"x" * 4096)

    deadline = time.monotonic() + 10
    while not state.scans(library_id):
        assert time.monotonic() < deadline, "没有自动出现扫描任务"
        time.sleep(0.1)
    page.goto(f"{url}/tasks")
    expect(page.get_by_text("扫描媒体库").first).to_be_visible()
    expect(page.get_by_text("电影", exact=True).first).to_be_visible()
    time.sleep(1.5)
    assert state.scans(library_id) == ["queued"]


def test_watch_failure_is_shown(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    library_id, folder = state.library("纪录片")
    folder.rmdir()
    with state.app.state.session_factory() as session:
        update_library(session, library_id, automation="watch")
    state.app.state.watch_manager.sync()
    page.goto(f"{url}/libraries")
    expect(_card(page, "纪录片").get_by_label("扫描方式")).to_have_text(
        re.compile(rf"^· 监听失败：{re.escape(str(folder))}：.+")
    )
