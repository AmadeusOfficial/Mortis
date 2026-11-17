"""详情页删除条目：确认框列出文件，点删除才删，删完回到海报墙。只用临时目录和假回收站。"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.db.models import Item, Library, LibraryPath, MediaFile, utc_now
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve
from tests.fakes import FakeTrash

pytestmark = pytest.mark.e2e


class World:
    def __init__(self, app: FastAPI, trash: FakeTrash, root: Path, ids: dict[str, int]) -> None:
        self.app = app
        self.trash = trash
        self.root = root
        self.ids = ids


def _seed(app: FastAPI, root: Path) -> dict[str, int]:
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(name="电影", data_type_id="movie", created_at=now, updated_at=now)
        library.paths = [LibraryPath(path=str(root))]
        session.add(library)
        session.flush()
        ids = {"library": library.id}
        for title, files in (
            ("教父", ["教父 (1972)/教父.mkv", "教父 (1972)/教父.nfo"]),
            ("乙片", ["乙片/乙片.mkv"]),
        ):
            item = Item(
                library_id=library.id,
                data_type_id="movie",
                kind="work",
                title=title,
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            session.flush()
            ids[title] = item.id
            for relative in files:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * 10)
                session.add(
                    MediaFile(
                        library_id=library.id,
                        item_id=item.id,
                        path=str(path),
                        file_kind="video" if relative.endswith(".mkv") else "nfo",
                        size=10,
                        created_at=now,
                        updated_at=now,
                    )
                )
        session.commit()
        return ids


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[World, str]]:
    root = tmp_path / "movies"
    root.mkdir()
    trash = FakeTrash()
    app = create_app(
        Settings(data_dir=tmp_path / "data", web_dist=WEB_DIST), start_worker=False, trash=trash
    )
    with serve(app) as url:
        yield World(app, trash, root, _seed(app, root)), url


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield browser.new_page(viewport={"width": 1400, "height": 1000})
        finally:
            browser.close()


def test_delete_from_detail_page(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    folder = state.root / "教父 (1972)"
    (folder / "花絮说明.txt").write_bytes(b"abc")
    page.goto(f"{url}/items/{state.ids['教父']}")

    page.get_by_role("button", name="更多操作").click()
    page.get_by_role("menuitem", name="删除").click()
    dialog = page.get_by_role("dialog", name="删除条目")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text(str(folder), exact=True)).to_be_visible()
    expect(dialog.get_by_text("文件会移到回收站，系统可能会弹出确认框")).to_be_visible()
    expect(dialog.get_by_text("花絮说明.txt", exact=True)).to_be_visible()
    expect(dialog.get_by_text("文件数 3")).to_be_visible()

    confirm = dialog.get_by_role("button", name="删除", exact=True)
    expect(confirm).to_be_enabled()
    confirm.click()

    expect(page.get_by_text("已删除《教父》")).to_be_visible()
    expect(page).to_have_url(f"{url}/libraries/{state.ids['library']}")
    expect(page.get_by_text("乙片", exact=True).first).to_be_visible()
    expect(page.get_by_text("教父", exact=True)).to_have_count(0)
    assert not folder.exists()
    assert (state.root / "乙片" / "乙片.mkv").exists()
    assert state.trash.removed == [(folder, False)]
    with state.app.state.session_factory() as session:
        assert session.get(Item, state.ids["教父"]) is None


def test_conflict_shows_reason_and_reloads(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    folder = state.root / "教父 (1972)"
    page.goto(f"{url}/items/{state.ids['教父']}")
    page.get_by_role("button", name="更多操作").click()
    page.get_by_role("menuitem", name="删除").click()
    dialog = page.get_by_role("dialog", name="删除条目")
    expect(dialog.get_by_text(str(folder), exact=True)).to_be_visible()

    (folder / "新写的.txt").write_bytes(b"new")
    confirm = dialog.get_by_role("button", name="删除", exact=True)
    confirm.click()
    expect(dialog.get_by_text("要删的文件变了，请重新确认")).to_be_visible()
    expect(dialog.get_by_text("没有删除", exact=True)).to_be_visible()
    expect(confirm).to_be_disabled()
    assert folder.exists()
    assert state.trash.removed == []

    dialog.get_by_role("button", name="重新读取要删的文件").click()
    expect(dialog.get_by_text("新写的.txt", exact=True)).to_be_visible()
    expect(confirm).to_be_enabled()
    confirm.click()
    expect(page.get_by_text("已删除《教父》")).to_be_visible()
    assert not folder.exists()


def test_permanent_warning(page: Page, world: tuple[World, str]) -> None:
    state, url = world
    state.trash.permanent = True
    page.goto(f"{url}/items/{state.ids['教父']}")
    page.get_by_role("button", name="更多操作").click()
    page.get_by_role("menuitem", name="删除").click()
    dialog = page.get_by_role("dialog", name="删除条目")
    warning = dialog.get_by_text("这个盘没有回收站，会永久删除")
    expect(warning).to_be_visible()
    expect(dialog.get_by_text("文件会移到回收站，系统可能会弹出确认框")).to_have_count(0)
    color = warning.evaluate("element => getComputedStyle(element).color")
    red, green, blue = (int(part) for part in color[color.index("(") + 1 : -1].split(",")[:3])
    assert red > green and red > blue


def _other_item(app: FastAPI, data_type: str, kind: str, title: str) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(name=title, data_type_id=data_type, created_at=now, updated_at=now)
        session.add(library)
        session.flush()
        item = Item(
            library_id=library.id,
            data_type_id=data_type,
            kind=kind,
            title=title,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.commit()
        return item.id


@pytest.mark.parametrize(("data_type", "kind"), [("tv", "work"), ("movie", "collection")])
def test_shows_and_collections_have_no_delete(
    page: Page, world: tuple[World, str], data_type: str, kind: str
) -> None:
    state, url = world
    item = _other_item(state.app, data_type, kind, "不能删的")
    with page.expect_response(lambda response: "/api/data-types" in response.url):
        page.goto(f"{url}/items/{item}")
    expect(page.get_by_role("heading", name="不能删的")).to_be_visible()
    expect(page.get_by_role("button", name="更多操作")).to_have_count(0)

    with page.expect_response(lambda response: "/api/data-types" in response.url):
        page.goto(f"{url}/items/{state.ids['教父']}")
    expect(page.get_by_role("button", name="更多操作")).to_be_visible()
