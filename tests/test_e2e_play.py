import re
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.config.models import PlaybackConfig
from mortis.config.store import ConfigStore
from mortis.db.models import Item, Library, MediaFile, utc_now
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve
from tests.fakes import FakeLauncher

pytestmark = pytest.mark.e2e

FAKE_PLAYER = """
from pydantic import BaseModel

from mortis.plugin_api import PlayerPlugin


class Settings(BaseModel):
    record: str = ""


class FakePlayer(PlayerPlugin):
    id = "fake_player"
    name = "假播放器"
    Settings = Settings

    def unavailable_reason(self, host):
        return None if host.settings.record else "没设记录文件"

    def play(self, host, path):
        with open(host.settings.record, "a", encoding="utf-8") as handle:
            handle.write(path + "\\n")


PLUGIN = FakePlayer()
"""


class World:
    def __init__(self, app: FastAPI, root: Path, record: Path) -> None:
        self.app = app
        self.root = root
        self.record = record

    def played(self) -> list[str]:
        if not self.record.is_file():
            return []
        return self.record.read_text(encoding="utf-8").splitlines()

    def library(self, name: str, data_type: str) -> int:
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        with self.app.state.session_factory() as session:
            now = utc_now()
            row = Library(name=name, data_type_id=data_type, created_at=now, updated_at=now)
            session.add(row)
            session.commit()
            return row.id

    def item(
        self, library: int, data_type: str, kind: str, title: str, parent: int | None = None
    ) -> int:
        with self.app.state.session_factory() as session:
            now = utc_now()
            row = Item(
                library_id=library,
                data_type_id=data_type,
                parent_id=parent,
                kind=kind,
                index_number=1 if kind in ("season", "episode") else None,
                title=title,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            return row.id

    def video(self, library: int, item: int, path: Path, *, write: bool = True) -> None:
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 2048)
        with self.app.state.session_factory() as session:
            now = utc_now()
            session.add(
                MediaFile(
                    library_id=library,
                    item_id=item,
                    path=str(path),
                    file_kind="video",
                    size=2048,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[World, str]]:
    data_dir = tmp_path / "data"
    plugin = data_dir / "plugins" / "fake_player"
    plugin.mkdir(parents=True)
    (plugin / "plugin.toml").write_text(
        'id = "fake_player"\nname = "假播放器"\nversion = "1.0"\nkind = "player"\n',
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text(textwrap.dedent(FAKE_PLAYER), encoding="utf-8")
    record = tmp_path / "played.txt"
    store = ConfigStore(data_dir)
    store.save_plugin("fake_player", values={"record": str(record)})
    potplayer = tmp_path / "PotPlayer" / "PotPlayerMini64.exe"
    potplayer.parent.mkdir()
    potplayer.write_bytes(b"")
    store.save_plugin("potplayer", values={"executable": str(potplayer)})
    store.save_section("playback", PlaybackConfig(player="fake_player"))
    app = create_app(
        Settings(data_dir=data_dir, web_dist=WEB_DIST), start_worker=False, launcher=FakeLauncher()
    )
    with serve(app) as url:
        yield World(app, tmp_path / "片库", record), url


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield browser.new_page(viewport={"width": 1400, "height": 1000})
        finally:
            browser.close()


def test_single_version_plays(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    library = state.library("电影", "movie")
    movie = state.item(library, "movie", "work", "黑客帝国")
    video = state.root / "电影" / "黑客帝国 (1999)" / "The Matrix.mkv"
    state.video(library, movie, video)

    page.goto(f"{url}/items/{movie}")
    page.get_by_role("button", name="播放", exact=True).click()
    expect(page.get_by_text("已用 假播放器 打开 The Matrix.mkv")).to_be_visible()
    assert state.played() == [str(video)]


def test_multiple_versions_ask_which_one(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    library = state.library("电影", "movie")
    movie = state.item(library, "movie", "work", "盗火线")
    folder = state.root / "电影" / "盗火线 (1995)"
    state.video(library, movie, folder / "Heat.2160p.mkv")
    state.video(library, movie, folder / "Heat.1080p.mkv")
    state.video(library, movie, folder / "Heat.720p.mkv", write=False)

    page.goto(f"{url}/items/{movie}")
    page.get_by_role("button", name="播放", exact=True).click()
    dialog = page.get_by_role("dialog", name="选择要播放的版本 · 盗火线")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("有 3 个视频文件，选一个打开。")).to_be_visible()
    expect(dialog.get_by_text("文件不在了")).to_be_visible()
    expect(dialog.get_by_role("button", name="播放 Heat.720p.mkv")).to_be_disabled()
    assert state.played() == []

    dialog.get_by_role("button", name="播放 Heat.2160p.mkv").click()
    expect(page.get_by_text("已用 假播放器 打开 Heat.2160p.mkv")).to_be_visible()
    expect(dialog).to_be_hidden()
    assert state.played() == [str(folder / "Heat.2160p.mkv")]


def test_missing_file_explains(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    library = state.library("电影", "movie")
    movie = state.item(library, "movie", "work", "消失的电影")
    video = state.root / "电影" / "消失的电影.mkv"
    state.video(library, movie, video, write=False)

    page.goto(f"{url}/items/{movie}")
    page.get_by_role("button", name="播放", exact=True).click()
    expect(page.get_by_text("播放失败")).to_be_visible()
    expect(page.get_by_text(f"文件不在了：{video}")).to_be_visible()
    assert state.played() == []


def test_episode_play_icon(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    library = state.library("剧", "tv")
    show = state.item(library, "tv", "work", "某剧")
    season = state.item(library, "tv", "season", "第 1 季", show)
    episode = state.item(library, "tv", "episode", "开端", season)
    video = state.root / "剧" / "某剧" / "Season 1" / "某剧.S01E01.mkv"
    state.video(library, episode, video)

    page.goto(f"{url}/items/{show}")
    expect(page.get_by_role("button", name="播放", exact=True)).to_have_count(0)
    page.get_by_role("link", name=re.compile("开端")).hover()
    page.get_by_role("button", name="播放第 1 集 开端").click()
    expect(page.get_by_text("已用 假播放器 打开 某剧.S01E01.mkv")).to_be_visible()
    assert state.played() == [str(video)]
    expect(page).to_have_url(f"{url}/items/{show}")


def test_playback_settings(page: Page, world: tuple[World, str], tmp_path: Path) -> None:
    _state, url = world
    page.goto(f"{url}/settings?section=playback")
    expect(page.get_by_role("heading", name="播放", exact=True)).to_be_visible()
    expect(page.get_by_label("默认播放器")).to_have_value("假播放器")
    expect(page.get_by_role("heading", name="PotPlayer", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="系统默认播放器", exact=True)).to_be_visible()
    exe = tmp_path / "PotPlayer" / "PotPlayerMini64.exe"
    expect(page.get_by_label("PotPlayer 会启动的程序")).to_have_text(str(exe))

    missing = tmp_path / "没有" / "PotPlayerMini64.exe"
    page.get_by_label("播放器路径").fill(str(missing))
    page.get_by_role("button", name="保存 PotPlayer 设置").click()
    expect(page.get_by_text(f"填的路径找不到：{missing}").first).to_be_visible()
    expect(page.get_by_label("PotPlayer 会启动的程序")).to_have_count(0)

    page.get_by_label("默认播放器").click()
    page.get_by_role("option", name="系统默认播放器").click()
    page.get_by_role("button", name="保存播放设置").click()
    expect(page.get_by_text("播放设置已保存")).to_be_visible()
    page.reload()
    expect(page.get_by_label("默认播放器")).to_have_value("系统默认播放器")
