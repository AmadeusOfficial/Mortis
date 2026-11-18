"""侧栏 AI 抽屉：假模型按脚本回答，只用临时库。"""

import copy
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from playwright.sync_api import Page, expect, sync_playwright

from mortis.agent.client import Delta, ToolCall
from mortis.api.app import create_app
from mortis.config.models import AiSecrets, AppConfig, Secrets
from mortis.config.store import ConfigStore
from mortis.db.models import Item, Library, LibraryPath, utc_now
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve

pytestmark = pytest.mark.e2e

Reply = list[Delta] | threading.Event


class Script:
    """所有请求共用一份脚本，按顺序回；遇到 Event 就先回一段文字再等它。"""

    def __init__(self) -> None:
        self.replies: list[Reply] = []
        self.seen: list[list[dict[str, Any]]] = []
        self.release = threading.Event()

    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Iterator[Delta]:
        self.seen.append(copy.deepcopy(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, threading.Event):
            yield Delta(text="先说一半")
            reply.wait(timeout=20)
            yield Delta(tool_calls=[ToolCall(id="late", name="search_items", arguments="{}")])
            return
        yield from reply


def say(*parts: str) -> list[Delta]:
    return [Delta(text=part) for part in parts] + [Delta(finish_reason="stop")]


def use(*calls: tuple[str, str, dict[str, Any]]) -> list[Delta]:
    tool_calls = [
        ToolCall(id=call_id, name=name, arguments=json.dumps(args, ensure_ascii=False))
        for call_id, name, args in calls
    ]
    return [Delta(tool_calls=tool_calls, finish_reason="tool_calls")]


class World:
    def __init__(self, app: FastAPI, script: Script, ids: dict[str, int], data: Path) -> None:
        self.app = app
        self.script = script
        self.ids = ids
        self.data = data

    def item(self, key: str) -> Item:
        with self.app.state.session_factory() as session:
            item = session.get(Item, self.ids[key])
            assert item is not None
            session.expunge(item)
            return item


def _seed(app: FastAPI, root: Path) -> dict[str, int]:
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(name="电影", data_type_id="movie", created_at=now, updated_at=now)
        library.paths = [LibraryPath(path=str(root))]
        session.add(library)
        session.flush()
        ids = {"library": library.id}
        for key, title, date in (
            ("matrix", "黑客帝国", "1999-03-30"),
            ("heat", "盗火线", "1995-12-15"),
        ):
            item = Item(
                library_id=library.id,
                data_type_id="movie",
                kind="work",
                title=title,
                release_date=date,
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            session.flush()
            ids[key] = item.id
        session.commit()
        return ids


def _world(tmp_path: Path, *, key: bool) -> Iterator[tuple[World, str]]:
    root = tmp_path / "movies"
    root.mkdir()
    data = tmp_path / "data"
    if key:
        ConfigStore(data).save_secrets(Secrets(ai=AiSecrets(api_key="sk-e2e")))
    script = Script()

    def factory(_config: AppConfig, _secrets: Secrets) -> Script:
        return script

    app = create_app(
        Settings(data_dir=data, web_dist=WEB_DIST), start_worker=False, chat_client_factory=factory
    )
    try:
        with serve(app) as url:
            yield World(app, script, _seed(app, root), data), url
    finally:
        script.release.set()


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[World, str]]:
    yield from _world(tmp_path, key=True)


@pytest.fixture
def no_key_world(tmp_path: Path) -> Iterator[tuple[World, str]]:
    yield from _world(tmp_path, key=False)


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield browser.new_page(viewport={"width": 1400, "height": 1000})
        finally:
            browser.close()


def _open(page: Page, url: str) -> Any:
    page.goto(f"{url}/libraries")
    page.get_by_role("button", name="AI", exact=True).click()
    drawer = page.get_by_role("dialog", name="AI 助手")
    expect(drawer).to_be_visible()
    return drawer


def _ask(drawer: Any, text: str) -> None:
    box = drawer.get_by_role("textbox", name="给 AI 的消息")
    box.fill(text)
    box.press("Enter")


def test_ask_search_and_open_card(page: Page, world: tuple[World, str], tmp_path: Path) -> None:
    state, url = world
    state.script.replies = [
        use(("s1", "search_items", {"query": "黑客"})),
        say("库里有", "《黑客帝国》，", "1999 年的。"),
    ]
    drawer = _open(page, url)
    expect(drawer.get_by_role("button", name="库里都有些什么？")).to_be_visible()
    drawer.get_by_role("button", name="播放", exact=True).click()
    expect(drawer.get_by_role("textbox", name="给 AI 的消息")).to_have_value("播放")
    assert state.script.seen == []

    _ask(drawer, "有黑客帝国吗")
    expect(drawer.get_by_text("有黑客帝国吗")).to_be_visible()
    expect(drawer.get_by_text("库里有《黑客帝国》，1999 年的。")).to_be_visible()
    expect(drawer.get_by_text("检索：黑客")).to_be_visible()
    card = drawer.get_by_role("link", name="黑客帝国")
    expect(card).to_contain_text("1999 · 电影 · 单部")
    page.screenshot(path=str(tmp_path / "agent-drawer.png"))

    card.click()
    expect(page).to_have_url(f"{url}/items/{state.ids['matrix']}")
    expect(page.get_by_role("heading", name="黑客帝国")).to_be_visible()
    expect(drawer.get_by_text("库里有《黑客帝国》，1999 年的。")).to_be_visible()

    state.script.replies = [say("不客气。")]
    _ask(drawer, "谢谢")
    expect(drawer.get_by_text("不客气。")).to_be_visible()
    roles = [message["role"] for message in state.script.seen[-1]]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]

    drawer.get_by_role("button", name="清空对话").click()
    expect(drawer.get_by_text("不客气。")).to_have_count(0)
    expect(drawer.get_by_role("button", name="库里都有些什么？")).to_be_visible()


def _batch(state: World) -> list[Delta]:
    return use(
        (
            "u1",
            "update_fields",
            {"item_id": state.ids["matrix"], "values": {"title": "黑客帝国 4K"}},
        ),
        ("u2", "update_fields", {"item_id": state.ids["heat"], "values": {"runtime": 170}}),
    )


def test_batch_update_needs_approval(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    state.script.replies = [_batch(state), say("两部都改好了。")]
    drawer = _open(page, url)
    _ask(drawer, "把这两部改一下")
    card = drawer.get_by_role("group", name="需要批准的修改")
    expect(card.get_by_text("要修改 2 个条目，批准后才会执行")).to_be_visible()
    expect(card.get_by_text("黑客帝国", exact=True)).to_be_visible()
    expect(card.get_by_text("→ 黑客帝国 4K")).to_be_visible()
    expect(card.get_by_text("→ 170")).to_be_visible()
    assert state.item("matrix").title == "黑客帝国"

    card.get_by_role("button", name="批准").click()
    expect(card.get_by_text("已批准")).to_be_visible()
    expect(card.get_by_role("button", name="批准")).to_have_count(0)
    expect(drawer.get_by_text("两部都改好了。")).to_be_visible()
    matrix = state.item("matrix")
    assert matrix.title == "黑客帝国 4K" and json.loads(matrix.locked_fields) == ["title"]
    assert json.loads(state.item("heat").fields) == {"runtime": 170}


def test_batch_update_rejected(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    state.script.replies = [_batch(state), say("好的，没有改。")]
    drawer = _open(page, url)
    _ask(drawer, "把这两部改一下")
    card = drawer.get_by_role("group", name="需要批准的修改")
    card.get_by_role("button", name="拒绝").click()
    expect(card.get_by_text("已拒绝")).to_be_visible()
    expect(drawer.get_by_text("好的，没有改。")).to_be_visible()
    assert state.item("matrix").title == "黑客帝国"
    assert json.loads(state.item("heat").fields) == {}


def test_missing_key_points_to_settings(page: Page, no_key_world: tuple[World, str]) -> None:
    state, url = no_key_world
    drawer = _open(page, url)
    expect(drawer.get_by_text("先在设置里填写 AI 的 key")).to_be_visible()
    drawer.get_by_role("link", name="去设置").click()
    expect(page).to_have_url(f"{url}/settings?section=ai")
    expect(page.get_by_text("模型接口").first).to_be_visible()
    assert state.script.seen == []


def test_stop_keeps_partial_answer(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    state.script.replies = [state.script.release]
    drawer = _open(page, url)
    _ask(drawer, "慢慢说")
    expect(drawer.get_by_text("先说一半")).to_be_visible()
    expect(drawer.get_by_role("button", name="发送")).to_have_count(0)
    drawer.get_by_role("button", name="停止").click()
    expect(drawer.get_by_role("button", name="停止")).to_have_count(0)
    expect(drawer.get_by_text("先说一半")).to_be_visible()
    state.script.release.set()

    state.script.replies = [say("好的。")]
    _ask(drawer, "继续")
    expect(drawer.get_by_text("好的。")).to_be_visible()
    sent = state.script.seen[-1]
    assert [(m["role"], m.get("content")) for m in sent[1:]] == [
        ("user", "慢慢说"),
        ("assistant", "先说一半"),
        ("user", "继续"),
    ]
