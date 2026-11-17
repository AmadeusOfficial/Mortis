from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from playwright.sync_api import Page, expect, sync_playwright

from mortis.api.app import create_app
from mortis.db.models import Item, utc_now
from mortis.settings import Settings
from tests.e2e_server import WEB_DIST, serve

pytestmark = pytest.mark.e2e


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[FastAPI, str]]:
    app = create_app(Settings(data_dir=tmp_path / "data", web_dist=WEB_DIST), start_worker=False)
    with serve(app) as url:
        yield app, url


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            yield browser.new_page(viewport={"width": 1400, "height": 1000})
        finally:
            browser.close()


def _library(page: Page, url: str, folder: Path, name: str, data_type: str) -> int:
    folder.mkdir(parents=True)
    response = page.request.post(
        url + "/api/libraries",
        data={"name": name, "data_type_id": data_type, "paths": [str(folder)]},
    )
    assert response.status == 201
    return response.json()["id"]


def _item(app: FastAPI, library: int, kind: str, title: str, parent: int | None = None) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        row = Item(
            library_id=library,
            data_type_id="movie",
            parent_id=parent,
            kind=kind,
            title=title,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


def _wall_titles(page: Page) -> list[str]:
    return page.get_by_role("region", name="海报墙").get_by_role("heading").all_inner_texts()


def _edit(page: Page, library_name: str) -> None:
    card = page.locator("article", has=page.get_by_role("heading", name=library_name, exact=True))
    expect(page.get_by_role("menuitem")).to_have_count(0)
    card.get_by_role("button", name="更多操作").click()
    page.get_by_role("menuitem", name="编辑").click()


def test_wall_collections_setting(page: Page, server: tuple[FastAPI, str], tmp_path: Path) -> None:
    app, url = server
    movies = _library(page, url, tmp_path / "影片" / "电影", "电影", "movie")
    _library(page, url, tmp_path / "影片" / "剧", "电视剧", "tv")
    godfather = _item(app, movies, "collection", "教父（系列）")
    _item(app, movies, "work", "教父", godfather)
    _item(app, movies, "work", "教父2", godfather)
    _item(app, movies, "work", "孤片")

    page.goto(f"{url}/libraries/{movies}")
    expect(page.get_by_role("region", name="海报墙").get_by_role("heading")).to_have_count(2)
    assert sorted(_wall_titles(page)) == sorted(["教父（系列）", "孤片"])

    page.get_by_role("region", name="海报墙").get_by_role("link", name="教父（系列）").click()
    expect(page).to_have_url(f"{url}/items/{godfather}")

    page.goto(url + "/libraries")
    _edit(page, "电影")
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("radio", name="系列收起")).to_be_checked()
    dialog.get_by_role("radio", name="只显示电影").check()
    dialog.get_by_role("button", name="保存").click()
    expect(page.get_by_text("媒体库已保存")).to_be_visible()
    expect(dialog).to_be_hidden()

    page.goto(f"{url}/libraries/{movies}")
    expect(page.get_by_role("region", name="海报墙").get_by_role("heading")).to_have_count(3)
    assert sorted(_wall_titles(page)) == sorted(["教父", "教父2", "孤片"])

    page.goto(url + "/libraries")
    _edit(page, "电影")
    expect(page.get_by_role("dialog").get_by_role("radio", name="只显示电影")).to_be_checked()
    page.get_by_role("dialog").get_by_role("button", name="取消").click()
    expect(page.get_by_role("dialog")).to_have_count(0)

    _edit(page, "电视剧")
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_label("库名")).to_have_value("电视剧")
    expect(dialog.get_by_text("海报墙里的系列电影")).to_have_count(0)
    dialog.get_by_role("button", name="取消").click()
    expect(page.get_by_role("dialog")).to_have_count(0)

    page.get_by_role("button", name="新建媒体库", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("radio", name="系列收起")).to_be_checked()
    dialog.get_by_label("数据类型").click()
    page.get_by_role("option", name="电视剧", exact=True).click()
    expect(dialog.get_by_text("海报墙里的系列电影")).to_have_count(0)
