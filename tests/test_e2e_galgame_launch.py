from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.config.store import ConfigStore
from mortis.db.models import Item, Library, MediaFile, utc_now
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve
from tests.fakes import FakeLauncher

pytestmark = pytest.mark.e2e


class World:
    def __init__(self, app: FastAPI, launcher: FakeLauncher, folder: Path, item: int) -> None:
        self.app = app
        self.launcher = launcher
        self.folder = folder
        self.item = item


def _game(app: FastAPI, folder: Path) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(name="Galgame", data_type_id="galgame", created_at=now, updated_at=now)
        session.add(library)
        session.flush()
        item = Item(
            library_id=library.id,
            data_type_id="galgame",
            kind="work",
            title="某游戏",
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.flush()
        session.add(
            MediaFile(
                library_id=library.id,
                item_id=item.id,
                path=str(folder),
                file_kind="game",
                size=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        return item.id


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[World, str]]:
    data_dir = tmp_path / "data"
    folder = tmp_path / "Galgame" / "某游戏"
    folder.mkdir(parents=True)
    (folder / "Start.exe").write_bytes(b"")
    (folder / "Launcher.exe").write_bytes(b"")
    launcher = FakeLauncher()
    app = create_app(
        Settings(data_dir=data_dir, web_dist=WEB_DIST), start_worker=False, launcher=launcher
    )
    with serve(app) as url:
        yield World(app, launcher, folder, _game(app, folder)), url


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield browser.new_page(viewport={"width": 1400, "height": 1000})
        finally:
            browser.close()


def _launched(state: World) -> list[str]:
    return [executable for executable, _args in state.launcher.launched]


def test_choose_remember_and_change_program(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    start = str(state.folder / "Start.exe")
    other = str(state.folder / "Launcher.exe")
    page.goto(f"{url}/items/{state.item}")
    expect(page.get_by_role("button", name="播放", exact=True)).to_have_count(0)

    page.get_by_role("button", name="启动", exact=True).click()
    dialog = page.get_by_role("dialog", name="选择启动程序 · 某游戏")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("有 2 个可以启动的程序，选一个启动，下次直接用它。")).to_be_visible()
    expect(dialog.get_by_role("button", name="启动 Launcher.exe")).to_be_visible()
    assert state.launcher.launched == []

    dialog.get_by_role("button", name="启动 Start.exe").click()
    expect(page.get_by_text("已启动 Start.exe")).to_be_visible()
    expect(dialog).to_be_hidden()
    assert _launched(state) == [start]
    assert state.launcher.cwds == [str(state.folder)]

    with page.expect_response(lambda response: response.url.endswith("/play")) as played:
        page.get_by_role("button", name="启动", exact=True).click()
    assert played.value.status == 200
    expect(dialog).to_be_hidden()
    assert _launched(state) == [start, start]

    page.get_by_role("button", name="更换启动程序").click()
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="启动 Launcher.exe").click()
    expect(page.get_by_text("已启动 Launcher.exe")).to_be_visible()
    expect(dialog).to_be_hidden()
    assert _launched(state) == [start, start, other]

    with page.expect_response(lambda response: response.url.endswith("/play")) as played:
        page.get_by_role("button", name="启动", exact=True).click()
    assert played.value.status == 200
    assert _launched(state) == [start, start, other, other]


def test_change_program_confirms_single_candidate(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    (state.folder / "Launcher.exe").unlink()
    start = str(state.folder / "Start.exe")
    page.goto(f"{url}/items/{state.item}")

    page.get_by_role("button", name="更换启动程序").click()
    dialog = page.get_by_role("dialog", name="选择启动程序 · 某游戏")
    expect(dialog).to_be_visible()
    expect(
        dialog.get_by_text("只找到这一个可以启动的程序，确认后启动，下次直接用它。")
    ).to_be_visible()
    assert state.launcher.launched == []

    dialog.get_by_role("button", name="启动 Start.exe").click()
    expect(page.get_by_text("已启动 Start.exe")).to_be_visible()
    expect(dialog).to_be_hidden()
    assert _launched(state) == [start]


def test_disabled_launcher_reason_is_shown(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    disabled = page.request.put(f"{url}/api/plugins/galgame_launcher", data={"enabled": False})
    assert disabled.status == 200, disabled.text()
    page.goto(f"{url}/items/{state.item}")
    page.get_by_role("button", name="启动", exact=True).click()
    expect(page.get_by_text("Galgame 启动已停用，在插件管理里启用")).to_be_visible()
    assert state.launcher.launched == []


def test_launcher_settings_not_a_video_player(
    page: Page, world: tuple[World, str], tmp_path: Path
) -> None:
    state, url = world
    page.goto(f"{url}/settings?section=plugins")
    expect(page.get_by_text("Galgame 启动", exact=True)).to_be_visible()

    page.goto(f"{url}/settings?section=playback")
    expect(page.get_by_role("heading", name="Galgame 启动", exact=True)).to_be_visible()
    page.get_by_label("默认播放器").click()
    expect(page.get_by_role("option", name="系统默认播放器", exact=True)).to_be_visible()
    expect(page.get_by_role("option", name="Galgame 启动")).to_have_count(0)
    page.keyboard.press("Escape")

    magpie = tmp_path / "Magpie" / "Magpie.exe"
    magpie.parent.mkdir()
    magpie.write_bytes(b"")
    (magpie.parent / "README.txt").write_text("x", encoding="utf-8")
    page.get_by_role("button", name="浏览", exact=True).click()
    picker = page.get_by_role("dialog", name="选择文件")
    expect(picker).to_be_visible()
    expect(picker.get_by_role("button", name="选择这个目录")).to_have_count(0)
    picker.get_by_label("当前目录").fill(str(magpie.parent))
    picker.get_by_label("当前目录").press("Enter")
    expect(picker.get_by_label("选择文件 README.txt")).to_be_visible()
    picker.get_by_label("选择文件 Magpie.exe").click()
    expect(picker).to_be_hidden()
    expect(page.get_by_label("Magpie 路径")).to_have_value(str(magpie))
    page.get_by_role("button", name="保存 Galgame 启动 设置").click()
    expect(page.get_by_text("Galgame 启动 的设置已保存")).to_be_visible()
    page.reload()
    expect(page.get_by_label("Magpie 路径")).to_have_value(str(magpie))
    values = ConfigStore(tmp_path / "data").load_config().sources["galgame_launcher"]
    assert values["magpie_path"] == str(magpie)
    assert state.launcher.launched == []
