"""识别纠错的工具：假 TMDB、假来源插件，换匹配和绑文件一律先批准。"""

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

from mortis.agent import recognize
from mortis.agent.client import ToolCall
from mortis.agent.loop import ApprovalStore
from mortis.agent.tools import ToolContext, calls_needing_approval, describe_call, run_tool
from mortis.api.app import create_app
from mortis.config.models import AiSecrets, AppConfig, Secrets
from mortis.config.store import ConfigStore
from mortis.db.models import Item, ItemExternalId, MediaFile, Task, utc_now
from mortis.settings import Settings
from tests.fakes import FakeTmdb
from tests.test_api_agent import ScriptedClient, chat, kinds, new_messages, say, script, use

LOCAL = "http://127.0.0.1"
TMDB_KEY = "tmdb-secret-key-xyz"

FAKE_GAL = """
from mortis.plugin_api import Hit, SourcePlugin


class FakeGal(SourcePlugin):
    id = "fakegal"
    name = "假来源"
    data_type = "galgame"
    provider = "fakegal"
    requests_per_second = 0

    def search(self, host, title):
        return [
            Hit(external_id="g1", title=title + " 旧作", year=2001),
            Hit(external_id="g2", title=title, original_title="Dies irae", year=2007,
                poster_url="https://img.test/g2.jpg", overview="長" * 500),
        ]

    def fetch(self, host, external_id):
        return {}

    def candidates(self, raw):
        return {}


PLUGIN = FakeGal()
"""


def _hit(tmdb_id: int, title: str, date: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "title": title,
        "original_title": extra.pop("original", title),
        "release_date": date,
        "overview": extra.pop("overview", ""),
        "poster_path": extra.pop("poster", None),
        **extra,
    }


@pytest.fixture
def fake() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add_search(
        "Heat",
        [
            _hit(
                949,
                "盗火线",
                "1995-12-15",
                original="Heat",
                poster="/heat.jpg",
                overview="洛杉矶" * 100,
            ),
            _hit(1, "热火", "1986-01-01", original="Heat"),
        ],
    )
    fake.add("/3/movie/949", _hit(949, "盗火线", "1995-12-15", original="Heat"))
    fake.add("/3/movie/550", _hit(550, "搏击俱乐部", "1999-10-15", original="Fight Club"))
    return fake


@pytest.fixture
def holder() -> dict[str, ScriptedClient]:
    return {"client": ScriptedClient()}


@pytest.fixture
def app(tmp_path: Path, fake: FakeTmdb, holder: dict[str, ScriptedClient]) -> FastAPI:
    recognize.forget_all()
    data = tmp_path / "data"
    store = ConfigStore(data)
    store.save_secrets(
        Secrets(ai=AiSecrets(api_key="sk-test"), sources={"tmdb": {"api_key": TMDB_KEY}})
    )
    for plugin_id in ("bangumi", "vndb", "ymgal", "cngal"):
        store.save_plugin(plugin_id, disabled=True)
    folder = data / "plugins" / "fakegal"
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(
        'id = "fakegal"\nname = "假来源"\nversion = "1"\nkind = "source"\n'
        "[mortis]\napi_version = 1\n",
        encoding="utf-8",
    )
    (folder / "__init__.py").write_text(textwrap.dedent(FAKE_GAL), encoding="utf-8")

    def factory(config: AppConfig, secrets: Secrets) -> ScriptedClient:
        return holder["client"]

    app = create_app(
        Settings(data_dir=data),
        start_worker=False,
        http_transport=fake.transport,
        chat_client_factory=factory,
    )
    app.state.approvals = ApprovalStore()
    return app


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


def _file(
    session: Session, library_id: int, path: Path, item_id: int | None = None, kind: str = "video"
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "video":
        path.write_bytes(b"x")
    else:
        path.mkdir(exist_ok=True)
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
    """电影库里一部认错成“搏击俱乐部”的 Heat，一部剧，还有一个没识别的文件。"""
    root = tmp_path / "库"
    movies = _library(client, root, "电影", "movie")
    shows = _library(client, root, "剧", "tv")
    now = utc_now()
    wrong = Item(
        library_id=movies,
        data_type_id="movie",
        kind="work",
        title="搏击俱乐部",
        release_date="1999-10-15",
        locked_fields='["title"]',
        created_at=now,
        updated_at=now,
    )
    show = Item(
        library_id=shows,
        data_type_id="tv",
        kind="work",
        title="火线",
        created_at=now,
        updated_at=now,
    )
    session.add_all([wrong, show])
    session.commit()
    session.add(ItemExternalId(item_id=wrong.id, provider="tmdb", external_id="550", is_primary=1))
    session.commit()
    _file(session, movies, root / "电影" / "Heat (1995)" / "Heat.1995.1080p.mkv", wrong.id)
    _file(session, shows, root / "剧" / "The Wire" / "The.Wire.S01E01.mkv", show.id)
    loose = _file(session, movies, root / "电影" / "Heat.1995.mkv")
    return {"movies": movies, "shows": shows, "wrong": wrong.id, "show": show.id, "loose": loose}


def _run(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    result = run_tool(ctx, name, json.dumps(arguments, ensure_ascii=False))
    assert TMDB_KEY not in json.dumps(result, ensure_ascii=False)
    return result


def test_get_item_shows_file_and_match(ctx: ToolContext, shelf: dict[str, int]) -> None:
    result = _run(ctx, "get_item", item_id=shelf["wrong"])
    assert result["external_ids"] == {"tmdb": "550"}
    assert result["file"]["file_name"] == "Heat.1995.1080p.mkv"
    assert result["file"]["folder_name"] == "Heat (1995)"
    assert (result["file"]["title"], result["file"]["year"]) == ("Heat", 1995)


def test_search_items_carry_poster(
    ctx: ToolContext, session: Session, shelf: dict[str, int]
) -> None:
    item = session.get(Item, shelf["wrong"])
    item.poster_path = "items/1/poster.jpg"
    session.commit()
    row = _run(ctx, "search_items", query="搏击")["items"][0]
    assert row["poster_url"] and row["poster_url"].endswith("poster.jpg")


def test_candidates_for_item_use_parsed_name(
    ctx: ToolContext, fake: FakeTmdb, shelf: dict[str, int]
) -> None:
    result = _run(ctx, "search_candidates", item_id=shelf["wrong"])
    assert (result["query"], result["year"]) == ("Heat", 1995)
    assert result["candidates"] == [
        {
            "source": "tmdb",
            "external_id": "949",
            "title": "盗火线",
            "original_title": "Heat",
            "year": 1995,
            "release_date": "1995-12-15",
            "poster_url": "/api/tmdb/image/w185/heat.jpg",
            "overview": "洛杉矶" * 40 + "…",
        }
    ]
    assert fake.paths() == ["/3/search/movie"]


def test_candidates_retry_without_year_and_cap(
    ctx: ToolContext, fake: FakeTmdb, shelf: dict[str, int]
) -> None:
    fake.add_search("Heat", [_hit(n, f"热火 {n}", "2010-01-01") for n in range(1, 13)])
    result = _run(ctx, "search_candidates", item_id=shelf["wrong"], query="Heat", year=1995)
    assert len(result["candidates"]) == 10
    assert "只列出前 10 个" in result["note"]
    assert fake.paths() == ["/3/search/movie", "/3/search/movie"]


def test_candidates_for_show_search_tv(
    ctx: ToolContext, fake: FakeTmdb, shelf: dict[str, int]
) -> None:
    fake.add_search("The Wire", [{"id": 1438, "name": "火线", "first_air_date": "2002-06-02"}])
    result = _run(ctx, "search_candidates", item_id=shelf["show"])
    assert [(c["external_id"], c["title"], c["year"]) for c in result["candidates"]] == [
        ("1438", "火线", 2002)
    ]
    assert fake.paths() == ["/3/search/tv"]


def test_candidates_for_unmatched_file(ctx: ToolContext, shelf: dict[str, int]) -> None:
    result = _run(ctx, "search_candidates", file_id=shelf["loose"])
    assert (result["query"], result["year"]) == ("Heat", 1995)
    assert result["candidates"][0]["external_id"] == "949"


def test_candidates_need_exactly_one_target(ctx: ToolContext, shelf: dict[str, int]) -> None:
    assert "只给一个" in _run(ctx, "search_candidates")["error"]
    both = _run(ctx, "search_candidates", item_id=shelf["wrong"], file_id=shelf["loose"])
    assert "只给一个" in both["error"]
    assert "没有这个条目" in _run(ctx, "search_candidates", item_id=999)["error"]


def test_candidates_from_plugin_source(
    ctx: ToolContext, session: Session, client: TestClient, tmp_path: Path
) -> None:
    games = _library(client, tmp_path / "库", "游戏", "galgame")
    folder = _file(session, games, tmp_path / "库" / "游戏" / "Dies irae (2007)", kind="game")
    result = _run(ctx, "search_candidates", file_id=folder, year=2007)
    assert result["query"] == "Dies irae"
    first, second = result["candidates"]
    assert (first["source"], first["external_id"], first["year"]) == ("fakegal", "g2", 2007)
    assert first["poster_url"] == "https://img.test/g2.jpg"
    assert len(first["overview"]) == recognize.OVERVIEW_LIMIT + 1
    assert second["external_id"] == "g1"

    bound = _run(ctx, "bind_file", file_id=folder, source="fakegal", external_id="g2")
    assert "error" not in bound, bound
    session.expire_all()
    media_file = session.get(MediaFile, folder)
    assert media_file.item_id == bound["item_id"]
    external = session.scalars(
        select(ItemExternalId).where(ItemExternalId.item_id == bound["item_id"])
    ).all()
    assert [(row.provider, row.external_id) for row in external] == [("fakegal", "g2")]


def test_list_unmatched_caps_rows(
    ctx: ToolContext, session: Session, shelf: dict[str, int], tmp_path: Path
) -> None:
    result = _run(ctx, "list_unmatched")
    assert result["total"] == 1
    row = result["files"][0]
    assert row["file_id"] == shelf["loose"]
    assert row["file_name"] == "Heat.1995.mkv"
    assert (row["guess_title"], row["guess_year"]) == ("Heat", 1995)
    assert row["library_name"] == "电影"
    for number in range(55):
        _file(session, shelf["movies"], tmp_path / "库" / "电影" / f"Extra.{number:02}.mkv")
    many = _run(ctx, "list_unmatched", library_id=shelf["movies"])
    assert len(many["files"]) == 50 and many["total"] == 56
    assert "只列出前 50 个" in many["note"]


def test_rematch_and_bind_always_need_approval() -> None:
    single = [ToolCall(id="r", name="rematch_item", arguments='{"item_id": 1}')]
    assert calls_needing_approval(single) == single
    bind = [ToolCall(id="b", name="bind_file", arguments="{}")]
    assert calls_needing_approval(bind) == bind
    read = [ToolCall(id="s", name="search_candidates", arguments="{}")]
    assert calls_needing_approval(read) == []


def test_describe_rematch_shows_current_and_target(
    ctx: ToolContext, fake: FakeTmdb, shelf: dict[str, int]
) -> None:
    call = ToolCall(
        id="r",
        name="rematch_item",
        arguments=json.dumps({"item_id": shelf["wrong"], "source": "tmdb", "external_id": "949"}),
    )
    described = describe_call(ctx, call)
    assert described == {
        "tool": "rematch_item",
        "item_id": shelf["wrong"],
        "title": "搏击俱乐部",
        "current": {"title": "搏击俱乐部", "year": 1999, "external_ids": {"tmdb": "550"}},
        "target": {
            "source": "tmdb",
            "external_id": "949",
            "title": "盗火线",
            "original_title": "Heat",
            "year": 1995,
            "release_date": "1995-12-15",
        },
        "note": "锁定的字段保留",
    }
    assert fake.paths() == ["/3/movie/949"]
    _run(ctx, "search_candidates", item_id=shelf["wrong"])
    describe_call(ctx, call)
    assert fake.paths()[-1] == "/3/search/movie", "搜过的候选不再请求详情"

    unknown = ToolCall(
        id="u",
        name="rematch_item",
        arguments=json.dumps({"item_id": shelf["wrong"], "source": "tmdb", "external_id": "7"}),
    )
    assert describe_call(ctx, unknown)["target"] == {"source": "tmdb", "external_id": "7"}


def test_rematch_rejects_wrong_source(ctx: ToolContext, shelf: dict[str, int]) -> None:
    wrong = _run(ctx, "rematch_item", item_id=shelf["wrong"], source="fakegal", external_id="1")
    assert "只能是 tmdb" in wrong["error"]
    letters = _run(ctx, "rematch_item", item_id=shelf["wrong"], source="tmdb", external_id="x")
    assert "要是数字" in letters["error"]


def _rematch_call(shelf: dict[str, int]) -> list:
    return use(
        (
            "r",
            "rematch_item",
            {"item_id": shelf["wrong"], "source": "tmdb", "external_id": "949"},
        )
    )


def _primary(app: FastAPI, item_id: int) -> str | None:
    with app.state.session_factory() as session:
        return session.scalar(
            select(ItemExternalId.external_id).where(
                ItemExternalId.item_id == item_id, ItemExternalId.provider == "tmdb"
            )
        )


def _tasks(app: FastAPI) -> list[Task]:
    with app.state.session_factory() as session:
        return list(session.scalars(select(Task)).all())


def test_single_rematch_waits_then_runs(
    app: FastAPI, client: TestClient, holder: dict[str, ScriptedClient], shelf: dict[str, int]
) -> None:
    script(holder, _rematch_call(shelf))
    history = [{"role": "user", "content": "选第 1 个"}]
    events = chat(client, history)
    assert kinds(events) == ["approval", "messages", "done"]
    approval = events[0][1]
    assert approval["summary"] == "要重新识别 1 项，批准后才会执行"
    assert approval["items"][0]["target"]["title"] == "盗火线"
    assert approval["items"][0]["note"] == "锁定的字段保留"
    assert _primary(app, shelf["wrong"]) == "550"
    before = len(_tasks(app))

    history += new_messages(events)
    script(holder, say("换好了。"))
    done = chat(client, history, approval={"id": approval["id"], "approved": True})
    assert kinds(done)[:2] == ["tool", "tool_result"]
    result = done[1][1]["result"]
    assert result["task_id"] and "锁定的字段保留" in result["note"]
    assert _primary(app, shelf["wrong"]) == "949"
    assert len(_tasks(app)) == before + 1


def test_rejected_rematch_does_nothing(
    app: FastAPI, client: TestClient, holder: dict[str, ScriptedClient], shelf: dict[str, int]
) -> None:
    script(holder, _rematch_call(shelf))
    history = [{"role": "user", "content": "选第 1 个"}]
    events = chat(client, history)
    approval = events[0][1]
    history += new_messages(events)
    script(holder, say("好的，不换。"))
    chat(client, history, approval={"id": approval["id"], "approved": False})
    assert _primary(app, shelf["wrong"]) == "550"
    assert _tasks(app) == []


def test_bind_after_approval(
    app: FastAPI, client: TestClient, holder: dict[str, ScriptedClient], shelf: dict[str, int]
) -> None:
    calls = use(
        ("b", "bind_file", {"file_id": shelf["loose"], "source": "tmdb", "external_id": "949"}),
        ("r", "rematch_item", {"item_id": shelf["show"], "source": "tmdb", "external_id": "1438"}),
    )
    script(holder, calls)
    history = [{"role": "user", "content": "按我选的换"}]
    events = chat(client, history)
    assert kinds(events) == ["approval", "messages", "done"]
    approval = events[0][1]
    assert approval["summary"] == "要重新识别 2 项，批准后才会执行"
    assert [item["tool"] for item in approval["items"]] == ["bind_file", "rematch_item"]
    bind_item, show_item = approval["items"]
    assert bind_item["current"]["file_name"] == "Heat.1995.mkv"
    assert bind_item["target"]["title"] == "盗火线"
    assert show_item["target"] == {"source": "tmdb", "external_id": "1438"}, "拿不到标题就只给编号"
    with app.state.session_factory() as session:
        assert session.get(MediaFile, shelf["loose"]).item_id is None

    history += new_messages(events)
    script(holder, say("绑好了。"))
    chat(client, history, approval={"id": approval["id"], "approved": True})
    with app.state.session_factory() as session:
        item_id = session.get(MediaFile, shelf["loose"]).item_id
        assert item_id is not None
        assert session.get(Item, item_id).title == "盗火线"
    assert _primary(app, shelf["show"]) == "1438"
