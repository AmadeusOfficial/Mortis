"""智能体的工具：在临时库上调用现有逻辑，结果交还模型。"""

import json
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.agent.client import ToolCall
from mortis.agent.tools import TOOLS, ToolContext, batch_update_targets, run_tool, tool_schemas
from mortis.api.app import create_app
from mortis.config.models import PlaybackConfig
from mortis.config.store import ConfigStore
from mortis.db.models import Item, ItemExternalId, MediaFile, Task, utc_now
from mortis.settings import Settings
from tests.fakes import FakeLauncher

LOCAL = "http://127.0.0.1"

FAKE_GAME = """
import os

from mortis.plugin_api import PlayerPlugin


class FakeGame(PlayerPlugin):
    id = "fake_game"
    name = "假启动器"
    file_kinds = ("game",)

    def targets(self, host, folder):
        found = []
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(".exe"):
                    found.append(os.path.relpath(os.path.join(root, name), folder))
        return sorted(found)

    def play(self, host, path):
        host.launch(path, [], cwd=os.path.dirname(path))


PLUGIN = FakeGame()
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    folder = data / "plugins" / "fake_game"
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(
        'id = "fake_game"\nname = "假启动器"\nversion = "1.0"\nkind = "player"\n',
        encoding="utf-8",
    )
    (folder / "__init__.py").write_text(textwrap.dedent(FAKE_GAME), encoding="utf-8")
    ConfigStore(data).save_section("playback", PlaybackConfig(player="system_player"))
    return data


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def app(data_dir: Path, launcher: FakeLauncher) -> FastAPI:
    return create_app(Settings(data_dir=data_dir), start_worker=False, launcher=launcher)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


@pytest.fixture
def session(app: FastAPI, client: TestClient) -> Iterator[Session]:
    with app.state.session_factory() as opened:
        yield opened


@pytest.fixture
def ctx(app: FastAPI, session: Session) -> ToolContext:
    return ToolContext(session=session, runner=app.state.task_runner, services=app.state.services)


def _library(client: TestClient, root: Path, name: str, data_type: str) -> int:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    body = {"name": name, "data_type_id": data_type, "paths": [str(folder)]}
    response = client.post("/api/libraries", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _item(
    session: Session,
    library_id: int,
    data_type: str,
    kind: str,
    title: str,
    *,
    parent: int | None = None,
    number: int | None = None,
    original: str | None = None,
    date: str | None = None,
    overview: str | None = None,
) -> int:
    now = utc_now()
    row = Item(
        library_id=library_id,
        data_type_id=data_type,
        parent_id=parent,
        kind=kind,
        index_number=number,
        title=title,
        original_title=original,
        release_date=date,
        overview=overview,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row.id


def _file(session: Session, library_id: int, item_id: int, path: Path, kind: str = "video") -> int:
    if kind == "video":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    now = utc_now()
    row = MediaFile(
        library_id=library_id,
        item_id=item_id,
        path=str(path),
        file_kind=kind,
        size=1,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row.id


@pytest.fixture
def shelf(client: TestClient, session: Session, tmp_path: Path) -> dict[str, int]:
    """电影库：黑客帝国系列两部、盗火线；剧库：黑客帝国动画版一季两集。"""
    movies = _library(client, tmp_path, "电影", "movie")
    shows = _library(client, tmp_path, "剧", "tv")
    collection = _item(session, movies, "movie", "collection", "黑客帝国（系列）")
    matrix = _item(
        session,
        movies,
        "movie",
        "work",
        "黑客帝国",
        parent=collection,
        original="The Matrix",
        date="1999-03-30",
        overview="长" * 1000,
    )
    reloaded = _item(
        session,
        movies,
        "movie",
        "work",
        "黑客帝国2：重装上阵",
        parent=collection,
        original="The Matrix Reloaded",
        date="2003-05-15",
    )
    heat = _item(session, movies, "movie", "work", "盗火线", original="Heat", date="1995-12-15")
    session.add(ItemExternalId(item_id=matrix, provider="tmdb", external_id="603", is_primary=1))
    session.commit()
    show = _item(
        session, shows, "tv", "work", "黑客帝国动画版", original="The Animatrix", date="2003-06-03"
    )
    season = _item(session, shows, "tv", "season", "第 1 季", parent=show, number=1)
    first = _item(session, shows, "tv", "episode", "超越极限", parent=season, number=1)
    second = _item(session, shows, "tv", "episode", "孩子的故事", parent=season, number=2)
    return {
        "movies": movies,
        "shows": shows,
        "collection": collection,
        "matrix": matrix,
        "reloaded": reloaded,
        "heat": heat,
        "show": show,
        "season": season,
        "first": first,
        "second": second,
    }


def _run(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    result = run_tool(ctx, name, json.dumps(arguments, ensure_ascii=False))
    json.dumps(result, ensure_ascii=False)
    return result


def test_schemas_are_openai_functions() -> None:
    schemas = tool_schemas()
    names = [schema["function"]["name"] for schema in schemas]
    assert names == [
        "search_items",
        "get_item",
        "update_fields",
        "scrape_item",
        "scan_library",
        "play_item",
    ]
    for schema in schemas:
        assert schema["type"] == "function"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"
    assert {name for name, tool in TOOLS.items() if tool.read_only} == {"search_items", "get_item"}


def test_search_across_libraries_and_filters(ctx: ToolContext, shelf: dict[str, int]) -> None:
    found = _run(ctx, "search_items", query="黑客帝国")["items"]
    rows = {row["id"]: row for row in found}
    assert set(rows) == {shelf["collection"], shelf["matrix"], shelf["reloaded"], shelf["show"]}
    assert rows[shelf["matrix"]] == {
        "id": shelf["matrix"],
        "title": "黑客帝国",
        "original_title": "The Matrix",
        "year": 1999,
        "library_id": shelf["movies"],
        "library_name": "电影",
        "data_type": "movie",
        "kind": "work",
        "structure": "单部",
    }
    assert rows[shelf["collection"]]["structure"] == "系列"
    assert rows[shelf["collection"]]["year"] == 1999
    assert rows[shelf["show"]]["structure"] == "剧"
    assert rows[shelf["show"]]["library_name"] == "剧"
    assert found[0]["id"] == shelf["matrix"], "完全同名的排在前面"

    by_original = _run(ctx, "search_items", query="heat")["items"]
    assert [row["id"] for row in by_original] == [shelf["heat"]]
    in_shows = _run(ctx, "search_items", query="黑客帝国", library_id=shelf["shows"])["items"]
    assert [row["id"] for row in in_shows] == [shelf["show"]]
    collections = _run(ctx, "search_items", query="黑客", kind="collection")["items"]
    assert [row["id"] for row in collections] == [shelf["collection"]]
    episodes = _run(ctx, "search_items", query="孩子的故事")
    assert episodes == {"items": [], "total": 0}


def test_search_caps_results(ctx: ToolContext, session: Session, shelf: dict[str, int]) -> None:
    for number in range(25):
        _item(session, shelf["movies"], "movie", "work", f"教父 {number}")
    result = _run(ctx, "search_items", query="教父")
    assert len(result["items"]) == 20
    assert result["total"] == 25
    assert "只列出前 20 条" in result["note"]


def test_get_movie_detail(ctx: ToolContext, session: Session, shelf: dict[str, int]) -> None:
    _file(session, shelf["movies"], shelf["matrix"], ctx.services.data_dir.parent / "m" / "a.mkv")
    detail = _run(ctx, "get_item", item_id=shelf["matrix"])
    assert detail["title"] == "黑客帝国"
    assert detail["year"] == 1999
    assert detail["library_name"] == "电影"
    assert detail["structure"] == "单部"
    assert detail["external_ids"] == {"tmdb": "603"}
    assert detail["file_count"] == 1
    assert detail["locked"] == []
    assert detail["collection"] == {"id": shelf["collection"], "title": "黑客帝国（系列）"}
    assert detail["fields"]["original_title"] == "The Matrix"
    assert detail["fields"]["release_date"] == "1999-03-30"
    overview = detail["fields"]["overview"]
    assert len(overview) < 400 and overview.endswith("…")
    assert "title" in detail["editable"] and "actors" not in detail["editable"]


def test_get_show_lists_seasons_and_episodes(ctx: ToolContext, shelf: dict[str, int]) -> None:
    detail = _run(ctx, "get_item", item_id=shelf["show"])
    assert detail["structure"] == "剧"
    assert detail["seasons"] == [
        {
            "id": shelf["season"],
            "number": 1,
            "title": "第 1 季",
            "episodes": [
                {"id": shelf["first"], "number": 1, "title": "超越极限", "has_file": False},
                {"id": shelf["second"], "number": 2, "title": "孩子的故事", "has_file": False},
            ],
        }
    ]
    episode = _run(ctx, "get_item", item_id=shelf["second"])
    assert episode["structure"] == "集"
    assert episode["show"] == {"id": shelf["show"], "title": "黑客帝国动画版"}
    assert episode["position"] == "第 1 季 第 2 集"


def test_get_collection_lists_movies(ctx: ToolContext, shelf: dict[str, int]) -> None:
    detail = _run(ctx, "get_item", item_id=shelf["collection"])
    assert detail["structure"] == "系列"
    assert detail["movies"] == [
        {"id": shelf["matrix"], "title": "黑客帝国", "year": 1999},
        {"id": shelf["reloaded"], "title": "黑客帝国2：重装上阵", "year": 2003},
    ]


def test_unknown_item_and_bad_arguments(ctx: ToolContext, shelf: dict[str, int]) -> None:
    for name in ("get_item", "update_fields", "scrape_item", "play_item"):
        arguments: dict[str, Any] = {"item_id": 99999}
        if name == "update_fields":
            arguments["values"] = {"title": "x"}
        assert _run(ctx, name, **arguments) == {"error": "没有这个条目：99999"}
    assert _run(ctx, "scan_library", library_id=99999) == {"error": "没有这个媒体库"}
    assert run_tool(ctx, "get_item", "{not json") == {"error": "参数不是合法的 JSON"}
    assert _run(ctx, "get_item") == {"error": "缺少参数 item_id"}
    assert _run(ctx, "get_item", item_id="三") == {"error": "参数 item_id 要是整数"}
    assert _run(ctx, "search_items", query="x", kind="season") == {
        "error": "参数 kind 只能是 work、collection"
    }
    assert _run(ctx, "get_item", item_id=1, extra=1) == {"error": "不认识的参数：extra"}
    assert run_tool(ctx, "delete_item", "{}") == {"error": "没有这个工具：delete_item"}


def test_update_fields_locks_changed_keys(
    ctx: ToolContext, session: Session, shelf: dict[str, int]
) -> None:
    result = _run(
        ctx,
        "update_fields",
        item_id=shelf["heat"],
        values={"title": "盗火线（导演剪辑）", "runtime": 170, "genres": ["犯罪", "动作"]},
    )
    assert result == {
        "item_id": shelf["heat"],
        "title": "盗火线（导演剪辑）",
        "values": {"title": "盗火线（导演剪辑）", "runtime": 170, "genres": ["犯罪", "动作"]},
        "locked": ["title", "runtime", "genres"],
    }
    session.expire_all()
    item = session.get(Item, shelf["heat"])
    assert item is not None and item.title == "盗火线（导演剪辑）"
    assert json.loads(item.locked_fields) == ["title", "runtime", "genres"]
    assert json.loads(item.field_sources)["title"] == "manual"


def test_update_fields_rejects_bad_values(
    ctx: ToolContext, session: Session, shelf: dict[str, int]
) -> None:
    assert _run(ctx, "update_fields", item_id=shelf["heat"], values={"actors": []}) == {
        "error": "「演员」不能手动修改"
    }
    assert _run(ctx, "update_fields", item_id=shelf["heat"], values={"nope": 1}) == {
        "error": "没有这个字段：nope"
    }
    assert _run(ctx, "update_fields", item_id=shelf["heat"], values={"title": " "}) == {
        "error": "片名不能为空"
    }
    assert _run(ctx, "update_fields", item_id=shelf["heat"], values={}) == {
        "error": "values 里至少要有一个字段"
    }
    session.expire_all()
    item = session.get(Item, shelf["heat"])
    assert item is not None and item.title == "盗火线" and json.loads(item.locked_fields) == []


def _tasks(session: Session) -> list[tuple[str, dict[str, Any]]]:
    session.expire_all()
    rows = session.scalars(select(Task).order_by(Task.id))
    return [(row.task_type, json.loads(row.payload or "{}")) for row in rows]


def test_scrape_and_scan_enqueue(ctx: ToolContext, session: Session, shelf: dict[str, int]) -> None:
    before = len(_tasks(session))
    scraped = _run(ctx, "scrape_item", item_id=shelf["heat"])
    assert scraped["item_id"] == shelf["heat"] and isinstance(scraped["task_id"], int)
    assert _run(ctx, "scrape_item", item_id=shelf["heat"])["task_id"] == scraped["task_id"]
    assert _run(ctx, "scrape_item", item_id=shelf["collection"]) == {
        "error": "系列电影跟着其中的电影一起刮削"
    }
    assert _run(ctx, "scrape_item", item_id=shelf["first"]) == {"error": "季和集跟着剧一起刮削"}

    scanned = _run(ctx, "scan_library", library_id=shelf["movies"])
    assert scanned["library_name"] == "电影" and isinstance(scanned["task_id"], int)
    added = _tasks(session)[before:]
    assert added == [
        ("scrape", {"item_id": shelf["heat"], "force": True}),
        ("organize", {"library_id": shelf["movies"], "scrape": None}),
    ]


def test_play_single_video_directly(
    ctx: ToolContext,
    session: Session,
    shelf: dict[str, int],
    tmp_path: Path,
    launcher: FakeLauncher,
) -> None:
    video = tmp_path / "电影" / "盗火线 (1995)" / "Heat.mkv"
    file_id = _file(session, shelf["movies"], shelf["heat"], video)
    assert _run(ctx, "play_item", item_id=shelf["heat"]) == {
        "played": True,
        "title": "盗火线",
        "player_name": "系统默认播放器",
        "file_id": file_id,
        "path": str(video),
    }
    assert launcher.opened == [str(video)]


def test_play_multiple_videos_returns_candidates(
    ctx: ToolContext,
    session: Session,
    shelf: dict[str, int],
    tmp_path: Path,
    launcher: FakeLauncher,
) -> None:
    folder = tmp_path / "电影" / "盗火线 (1995)"
    big = _file(session, shelf["movies"], shelf["heat"], folder / "Heat.2160p.mkv")
    small = _file(session, shelf["movies"], shelf["heat"], folder / "Heat.1080p.mkv")
    result = _run(ctx, "play_item", item_id=shelf["heat"])
    assert result["needs_choice"] is True
    assert result["detail"] == "有 2 个视频，选一个再播放"
    assert [(c["file_id"], c["name"]) for c in result["candidates"]] == [
        (small, "Heat.1080p.mkv"),
        (big, "Heat.2160p.mkv"),
    ]
    assert launcher.opened == []

    picked = _run(ctx, "play_item", item_id=shelf["heat"], file_id=big)
    assert picked["played"] is True and launcher.opened == [str(folder / "Heat.2160p.mkv")]
    assert _run(ctx, "play_item", item_id=shelf["show"]) == {
        "error": "剧不能直接播放，选一集再播放"
    }


def test_play_galgame_with_several_programs(
    client: TestClient, ctx: ToolContext, session: Session, tmp_path: Path, launcher: FakeLauncher
) -> None:
    games = _library(client, tmp_path, "游戏", "galgame")
    game = _item(session, games, "galgame", "work", "白色相簿2")
    folder = tmp_path / "游戏" / "白色相簿 2"
    for name in ("game.exe", "config.exe"):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"MZ")
    file_id = _file(session, games, game, folder, "game")

    result = _run(ctx, "play_item", item_id=game)
    assert result["needs_choice"] is True
    assert result["detail"] == "有 2 个可以启动的程序，选一个再启动"
    assert [(c["file_id"], c["target"]) for c in result["candidates"]] == [
        (file_id, "config.exe"),
        (file_id, "game.exe"),
    ]
    assert launcher.launched == []

    started = _run(ctx, "play_item", item_id=game, target="game.exe")
    assert started["played"] is True and started["path"] == str(folder / "game.exe")
    assert launcher.launched == [(str(folder / "game.exe"), [])]


def test_batch_update_targets() -> None:
    def call(name: str, arguments: Any) -> ToolCall:
        text = arguments if isinstance(arguments, str) else json.dumps(arguments)
        return ToolCall(id=name, name=name, arguments=text)

    assert batch_update_targets([call("update_fields", {"item_id": 1, "values": {}})]) == [1]
    assert batch_update_targets(
        [
            call("update_fields", {"item_id": 2, "values": {"title": "a"}}),
            call("search_items", {"query": "x"}),
            call("update_fields", {"item_id": 1, "values": {"title": "b"}}),
            call("update_fields", {"item_id": 2, "values": {"runtime": 1}}),
            call("update_fields", "{坏的"),
        ]
    ) == [1, 2]
    assert batch_update_targets([call("scrape_item", {"item_id": 1})]) == []
