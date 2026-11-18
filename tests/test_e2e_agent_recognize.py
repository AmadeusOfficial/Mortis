"""让 AI 纠正识别：详情页和未识别页填好模板，候选卡片点选，批准后才换匹配。"""

import struct
import zlib
from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import select

from mortis.agent import recognize
from mortis.api.app import create_app
from mortis.config.models import AiSecrets, AppConfig, Secrets
from mortis.config.store import ConfigStore
from mortis.db.models import Item, ItemExternalId, Library, LibraryPath, MediaFile, Task, utc_now
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve
from tests.fakes import FakeTmdb
from tests.test_e2e_agent import Script, say, use

pytestmark = pytest.mark.e2e

PICK = "选第 1 个：《盗火线》（1995，TMDB 949）"


class World:
    def __init__(self, app: FastAPI, script: Script, ids: dict[str, int]) -> None:
        self.app = app
        self.script = script
        self.ids = ids

    def tmdb_id(self, key: str) -> str | None:
        with self.app.state.session_factory() as session:
            return session.scalar(
                select(ItemExternalId.external_id).where(
                    ItemExternalId.item_id == self.ids[key], ItemExternalId.provider == "tmdb"
                )
            )

    def tasks(self) -> list[Task]:
        with self.app.state.session_factory() as session:
            return list(session.scalars(select(Task)).all())


def _png(width: int, height: int) -> bytes:
    """一张纯色的真 PNG，好让候选卡片的海报能显示出来。"""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\x00" + b"\xb0\x40\x30" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _transport(fake: FakeTmdb) -> httpx.MockTransport:
    poster = _png(40, 60)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "image.tmdb.org":
            return httpx.Response(200, content=poster, headers={"content-type": "image/png"})
        return fake(request)

    return httpx.MockTransport(handle)


def _fake_tmdb() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add_search(
        "Heat",
        [
            {
                "id": 949,
                "title": "盗火线",
                "original_title": "Heat",
                "release_date": "1995-12-15",
                "overview": "洛杉矶的职业大盗和警探。",
                "poster_path": "/heat.jpg",
            },
            {
                "id": 1,
                "title": "热火",
                "original_title": "Heat",
                "release_date": "1995-01-01",
                "overview": "",
                "poster_path": None,
            },
        ],
    )
    return fake


def _seed(app: FastAPI, root: Path) -> dict[str, int]:
    folder = root / "Heat (1995)"
    folder.mkdir(parents=True)
    video = folder / "Heat.1995.1080p.mkv"
    loose = root / "Heat.1995.mkv"
    for path in (video, loose):
        path.write_bytes(b"x")
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(name="电影", data_type_id="movie", created_at=now, updated_at=now)
        library.paths = [LibraryPath(path=str(root))]
        session.add(library)
        session.flush()
        wrong = Item(
            library_id=library.id,
            data_type_id="movie",
            kind="work",
            title="搏击俱乐部",
            release_date="1999-10-15",
            created_at=now,
            updated_at=now,
        )
        session.add(wrong)
        session.flush()
        session.add(
            ItemExternalId(item_id=wrong.id, provider="tmdb", external_id="550", is_primary=1)
        )
        files = {}
        for key, path, item_id in (("video", video, wrong.id), ("loose", loose, None)):
            row = MediaFile(
                library_id=library.id,
                item_id=item_id,
                path=str(path),
                file_kind="video",
                size=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            files[key] = row.id
        session.commit()
        return {"wrong": wrong.id, **files}


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[World, str]]:
    recognize.forget_all()
    root = tmp_path / "movies"
    root.mkdir()
    data = tmp_path / "data"
    ConfigStore(data).save_secrets(
        Secrets(ai=AiSecrets(api_key="sk-e2e"), sources={"tmdb": {"api_key": "k"}})
    )
    script = Script()

    def factory(_config: AppConfig, _secrets: Secrets) -> Script:
        return script

    app = create_app(
        Settings(data_dir=data, web_dist=WEB_DIST),
        start_worker=False,
        http_transport=_transport(_fake_tmdb()),
        chat_client_factory=factory,
    )
    try:
        with serve(app) as url:
            yield World(app, script, _seed(app, root)), url
    finally:
        script.release.set()


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield browser.new_page(viewport={"width": 1400, "height": 1000})
        finally:
            browser.close()


def test_detail_rematch_through_ai(page: Page, world: tuple[World, str], tmp_path: Path) -> None:
    state, url = world
    item_id = state.ids["wrong"]
    state.script.replies = [
        use(("g", "get_item", {"item_id": item_id})),
        use(("c", "search_candidates", {"item_id": item_id})),
        say("找到 2 个候选，选哪个？"),
        use(("r", "rematch_item", {"item_id": item_id, "source": "tmdb", "external_id": "949"})),
        say("换好了，正在重新刮削。"),
    ]
    page.goto(f"{url}/items/{item_id}")
    expect(page.get_by_role("heading", name="搏击俱乐部")).to_be_visible()
    page.get_by_role("button", name="更多操作").click()
    page.get_by_role("menuitem", name="让 AI 重新识别").click()
    drawer = page.get_by_role("dialog", name="AI 助手")
    expect(drawer).to_be_visible()
    box = drawer.get_by_role("textbox", name="给 AI 的消息")
    expect(box).to_have_value(
        "\n".join(
            [
                f"帮我重新识别条目 {item_id}。",
                "文件名：Heat.1995.1080p.mkv",
                "文件夹：Heat (1995)",
                "从文件名解析出：标题「Heat」，年份 1995",
                "现在匹配的是：《搏击俱乐部》（1999，TMDB 550）",
                "请找出正确的作品，列候选给我选。",
            ]
        )
    )
    expect(drawer.get_by_role("button", name="让 AI 重新识别这部")).to_be_visible()
    assert state.script.seen == [], "只填不发"

    box.press("Enter")
    expect(drawer.get_by_text("找到 2 个候选，选哪个？")).to_be_visible()
    cards = drawer.get_by_role("list", name="候选").get_by_role("listitem")
    expect(cards).to_have_count(2)
    first = cards.nth(0)
    expect(first).to_contain_text("盗火线 (1995)")
    expect(first).to_contain_text("Heat")
    expect(first).to_contain_text("TMDB 949")
    poster = first.locator("img")
    expect(poster).to_have_attribute("src", "/api/tmdb/image/w185/heat.jpg")
    page.wait_for_function(
        "img => img.complete && img.naturalWidth > 0", arg=poster.element_handle()
    )
    drawer.screenshot(path=str(tmp_path / "agent-candidates.png"))
    assert state.tmdb_id("wrong") == "550"

    first.click()
    expect(drawer.get_by_text(PICK)).to_be_visible()
    approval = drawer.get_by_role("group", name="需要批准的修改")
    expect(approval.get_by_text("要重新识别 1 项，批准后才会执行")).to_be_visible()
    expect(approval.get_by_text("《搏击俱乐部》（1999，TMDB 550）")).to_be_visible()
    expect(approval.get_by_text("《盗火线》（1995，TMDB 949）")).to_be_visible()
    expect(approval.get_by_text("锁定的字段保留")).to_be_visible()
    drawer.screenshot(path=str(tmp_path / "agent-approval.png"))
    assert state.script.seen[-1][-1] == {"role": "user", "content": PICK}
    assert state.tmdb_id("wrong") == "550"
    assert state.tasks() == []

    approval.get_by_role("button", name="批准").click()
    expect(approval.get_by_text("已批准")).to_be_visible()
    expect(drawer.get_by_text("换好了，正在重新刮削。")).to_be_visible()
    assert state.tmdb_id("wrong") == "949"
    assert len(state.tasks()) == 1


def test_unmatched_fills_template(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    page.goto(f"{url}/unmatched")
    page.get_by_role("button", name="让 AI 识别Heat.1995.mkv").click()
    drawer = page.get_by_role("dialog", name="AI 助手")
    box = drawer.get_by_role("textbox", name="给 AI 的消息")
    expect(box).to_have_value(
        "\n".join(
            [
                f"帮我识别未识别的文件 {state.ids['loose']}。",
                "文件名：Heat.1995.mkv",
                "文件夹：movies",
                "从文件名解析出：标题「Heat」，年份 1995",
                "请找出正确的作品，列候选给我选。",
            ]
        )
    )
    preset = drawer.get_by_role("button", name="帮我识别这些未识别的文件")
    expect(preset).to_be_visible()
    preset.click()
    expect(box).to_have_value("帮我识别这些未识别的文件")
    assert state.script.seen == []
