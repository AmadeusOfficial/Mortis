"""对话接口：假模型按脚本回，读 SSE 事件。"""

import copy
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mortis.agent.client import ChatError, Delta, ToolCall
from mortis.agent.loop import SYSTEM_PROMPT, ApprovalStore, run_chat
from mortis.api.app import create_app
from mortis.config.models import AiConfig, AiSecrets, AppConfig, PlaybackConfig, Secrets
from mortis.config.store import ConfigStore
from mortis.db.models import Item, MediaFile, utc_now
from mortis.settings import Settings
from tests.fakes import FakeLauncher

LOCAL = "http://127.0.0.1"
KEY = "sk-agent-test"

Reply = list[Delta] | Exception


class ScriptedClient:
    """每次 stream 按顺序回一段脚本，记下收到的消息和工具。"""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict[str, Any]]] = []
        self.tools: list[list[dict[str, Any]]] = []
        self.closed = 0

    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Iterator[Delta]:
        self.seen.append(copy.deepcopy(messages))
        self.tools.append(tools)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield from reply

    def close(self) -> None:
        self.closed += 1


def say(*parts: str) -> list[Delta]:
    return [Delta(text=part) for part in parts] + [Delta(finish_reason="stop")]


def use(*calls: tuple[str, str, dict[str, Any]], text: str = "") -> list[Delta]:
    deltas = [Delta(text=text)] if text else []
    tool_calls = [
        ToolCall(id=call_id, name=name, arguments=json.dumps(args, ensure_ascii=False))
        for call_id, name, args in calls
    ]
    return deltas + [Delta(tool_calls=tool_calls, finish_reason="tool_calls")]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    store = ConfigStore(data)
    store.save_secrets(Secrets(ai=AiSecrets(api_key=KEY)))
    store.save_section("playback", PlaybackConfig(player="system_player"))
    return data


@pytest.fixture
def holder() -> dict[str, ScriptedClient]:
    return {"client": ScriptedClient()}


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(data_dir: Path, holder: dict[str, ScriptedClient], clock: Clock) -> FastAPI:
    def factory(config: AppConfig, secrets: Secrets) -> ScriptedClient:
        assert secrets.ai.api_key == KEY
        return holder["client"]

    app = create_app(
        Settings(data_dir=data_dir),
        start_worker=False,
        launcher=FakeLauncher(),
        chat_client_factory=factory,
    )
    app.state.approvals = ApprovalStore(clock=clock)
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def script(holder: dict[str, ScriptedClient], *replies: Reply) -> ScriptedClient:
    holder["client"] = ScriptedClient(*replies)
    return holder["client"]


def _events(response: Any) -> list[tuple[str, dict[str, Any]]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def chat(
    client: TestClient, messages: list[dict[str, Any]], **extra: Any
) -> list[tuple[str, dict]]:
    return _events(client.post("/api/agent/chat", json={"messages": messages, **extra}))


def kinds(events: list[tuple[str, dict]]) -> list[str]:
    return [name for name, _ in events]


def new_messages(events: list[tuple[str, dict]]) -> list[dict[str, Any]]:
    return next(data["messages"] for name, data in events if name == "messages")


ASK = [{"role": "user", "content": "库里有什么"}]


@pytest.fixture
def shelf(app: FastAPI, client: TestClient, tmp_path: Path) -> dict[str, int]:
    root = tmp_path / "电影"
    root.mkdir()
    library = client.post(
        "/api/libraries", json={"name": "电影", "data_type_id": "movie", "paths": [str(root)]}
    ).json()["id"]
    ids = {"library": library}
    with app.state.session_factory() as session:
        for key, title in (("matrix", "黑客帝国"), ("heat", "盗火线"), ("godfather", "教父")):
            now = utc_now()
            row = Item(
                library_id=library,
                data_type_id="movie",
                kind="work",
                title=title,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            ids[key] = row.id
    return ids


def _item(app: FastAPI, item_id: int) -> Item:
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None
        session.expunge(item)
        return item


def test_plain_text(client: TestClient, holder: dict[str, ScriptedClient]) -> None:
    model = script(holder, say("你好，", "我是莫提斯的片库助手。"))
    events = chat(client, ASK)
    assert events == [
        ("text", {"delta": "你好，"}),
        ("text", {"delta": "我是莫提斯的片库助手。"}),
        (
            "messages",
            {"messages": [{"role": "assistant", "content": "你好，我是莫提斯的片库助手。"}]},
        ),
        ("done", {}),
    ]
    assert model.seen[0][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert model.seen[0][1:] == ASK
    names = [tool["function"]["name"] for tool in model.tools[0]]
    assert "search_items" in names and "play_item" in names
    assert model.closed == 1


def test_search_then_answer(
    client: TestClient, holder: dict[str, ScriptedClient], shelf: dict[str, int]
) -> None:
    model = script(
        holder,
        use(("c1", "search_items", {"query": "黑客"}), text="我查一下。"),
        say("库里有《黑客帝国》。"),
    )
    events = chat(client, ASK)
    assert kinds(events) == ["text", "tool", "tool_result", "text", "messages", "done"]
    assert events[1] == (
        "tool",
        {"id": "c1", "name": "search_items", "arguments": {"query": "黑客"}},
    )
    result = events[2][1]
    assert result["id"] == "c1" and result["name"] == "search_items"
    assert [row["title"] for row in result["result"]["items"]] == ["黑客帝国"]

    added = new_messages(events)
    assert [m["role"] for m in added] == ["assistant", "tool", "assistant"]
    assert added[0] == {
        "role": "assistant",
        "content": "我查一下。",
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "search_items", "arguments": '{"query": "黑客"}'},
            }
        ],
    }
    assert added[1]["tool_call_id"] == "c1"
    assert json.loads(added[1]["content"])["items"][0]["id"] == shelf["matrix"]
    assert added[2] == {"role": "assistant", "content": "库里有《黑客帝国》。"}
    assert model.seen[1][-2:] == added[:2]


def test_play_with_several_videos_returns_candidates(
    app: FastAPI,
    client: TestClient,
    holder: dict[str, ScriptedClient],
    shelf: dict[str, int],
    tmp_path: Path,
) -> None:
    folder = tmp_path / "电影" / "盗火线"
    folder.mkdir(parents=True)
    with app.state.session_factory() as session:
        for name in ("Heat.1080p.mkv", "Heat.2160p.mkv"):
            (folder / name).write_bytes(b"x")
            now = utc_now()
            session.add(
                MediaFile(
                    library_id=shelf["library"],
                    item_id=shelf["heat"],
                    path=str(folder / name),
                    file_kind="video",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
    script(holder, use(("p", "play_item", {"item_id": shelf["heat"]})), say("有两个版本，选哪个？"))
    events = chat(client, [{"role": "user", "content": "播放盗火线"}])
    result = next(data for name, data in events if name == "tool_result")["result"]
    assert result["needs_choice"] is True
    assert [c["name"] for c in result["candidates"]] == ["Heat.1080p.mkv", "Heat.2160p.mkv"]
    assert app.state.services.launcher.opened == []


def _batch(shelf: dict[str, int]) -> list[Delta]:
    return use(
        ("s", "search_items", {"query": "教父"}),
        ("u1", "update_fields", {"item_id": shelf["matrix"], "values": {"title": "黑客帝国 4K"}}),
        ("u2", "update_fields", {"item_id": shelf["heat"], "values": {"runtime": 170}}),
    )


def _ask_batch(
    client: TestClient, holder: dict[str, ScriptedClient], shelf: dict[str, int]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    script(holder, _batch(shelf))
    history = [{"role": "user", "content": "把这两部改一下"}]
    events = chat(client, history)
    assert kinds(events) == ["tool", "tool_result", "approval", "messages", "done"]
    assert events[0][1]["name"] == "search_items"
    return history + new_messages(events), events[2][1]


def test_batch_update_waits_for_approval(
    app: FastAPI, client: TestClient, holder: dict[str, ScriptedClient], shelf: dict[str, int]
) -> None:
    history, approval = _ask_batch(client, holder, shelf)
    assert approval["summary"] == "要修改 2 个条目，批准后才会执行"
    assert approval["items"] == [
        {
            "tool": "update_fields",
            "item_id": shelf["matrix"],
            "title": "黑客帝国",
            "values": {"title": "黑客帝国 4K"},
        },
        {
            "tool": "update_fields",
            "item_id": shelf["heat"],
            "title": "盗火线",
            "values": {"runtime": 170},
        },
    ]
    assert [m["role"] for m in history] == ["user", "assistant", "tool"]
    assert _item(app, shelf["matrix"]).title == "黑客帝国"

    model = script(holder, say("改好了。"))
    events = chat(client, history, approval={"id": approval["id"], "approved": True})
    assert kinds(events) == [
        "tool",
        "tool_result",
        "tool",
        "tool_result",
        "text",
        "messages",
        "done",
    ]
    added = new_messages(events)
    assert [(m["role"], m.get("tool_call_id")) for m in added] == [
        ("tool", "u1"),
        ("tool", "u2"),
        ("assistant", None),
    ]
    matrix = _item(app, shelf["matrix"])
    assert matrix.title == "黑客帝国 4K" and json.loads(matrix.locked_fields) == ["title"]
    assert json.loads(_item(app, shelf["heat"]).fields)["runtime"] == 170
    assert [m["role"] for m in model.seen[0]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
        "tool",
    ]

    again = chat(client, history, approval={"id": approval["id"], "approved": True})
    assert again[0] == ("error", {"message": "这次批准已过期，请重新提问"})


def test_rejected_batch_changes_nothing(
    app: FastAPI, client: TestClient, holder: dict[str, ScriptedClient], shelf: dict[str, int]
) -> None:
    history, approval = _ask_batch(client, holder, shelf)
    model = script(holder, say("好的，不改了。"))
    events = chat(client, history, approval={"id": approval["id"], "approved": False})
    assert kinds(events) == ["text", "messages", "done"]
    added = new_messages(events)
    assert [json.loads(m["content"]) for m in added[:2]] == [
        {"error": "用户拒绝了这次修改"},
        {"error": "用户拒绝了这次修改"},
    ]
    assert model.seen[0][-1]["tool_call_id"] == "u2"
    assert _item(app, shelf["matrix"]).title == "黑客帝国"
    assert json.loads(_item(app, shelf["heat"]).fields) == {}


def test_expired_approval(
    app: FastAPI,
    client: TestClient,
    holder: dict[str, ScriptedClient],
    shelf: dict[str, int],
    clock: Clock,
) -> None:
    history, approval = _ask_batch(client, holder, shelf)
    clock.now += 601
    model = script(holder)
    events = chat(client, history, approval={"id": approval["id"], "approved": True})
    assert events == [
        ("error", {"message": "这次批准已过期，请重新提问"}),
        ("messages", {"messages": []}),
        ("done", {}),
    ]
    assert model.seen == []
    assert _item(app, shelf["matrix"]).title == "黑客帝国"


def test_new_question_after_pending_approval_fills_tool_results(
    app: FastAPI, client: TestClient, holder: dict[str, ScriptedClient], shelf: dict[str, int]
) -> None:
    history, _approval = _ask_batch(client, holder, shelf)
    model = script(holder, say("好。"))
    chat(client, history + [{"role": "user", "content": "算了，问点别的"}])
    sent = model.seen[0]
    assert [(m["role"], m.get("tool_call_id")) for m in sent] == [
        ("system", None),
        ("user", None),
        ("assistant", None),
        ("tool", "s"),
        ("tool", "u1"),
        ("tool", "u2"),
        ("user", None),
    ]
    assert json.loads(sent[4]["content"]) == {"error": "用户没有批准这次修改，没有执行"}
    assert _item(app, shelf["matrix"]).title == "黑客帝国"


def test_single_update_runs_without_approval(
    app: FastAPI, client: TestClient, holder: dict[str, ScriptedClient], shelf: dict[str, int]
) -> None:
    script(
        holder,
        use(("u", "update_fields", {"item_id": shelf["heat"], "values": {"title": "盗火线 4K"}})),
        say("改好了。"),
    )
    events = chat(client, [{"role": "user", "content": "改名"}])
    assert "approval" not in kinds(events)
    heat = _item(app, shelf["heat"])
    assert heat.title == "盗火线 4K" and json.loads(heat.locked_fields) == ["title"]


def test_stops_after_max_rounds(
    data_dir: Path, client: TestClient, holder: dict[str, ScriptedClient]
) -> None:
    ConfigStore(data_dir).save_section("ai", AiConfig(max_rounds=2))
    loop = [use((f"c{n}", "search_items", {"query": "x"})) for n in range(3)]
    model = script(holder, *loop)
    events = chat(client, ASK)
    assert kinds(events) == [
        "tool",
        "tool_result",
        "tool",
        "tool_result",
        "error",
        "messages",
        "done",
    ]
    assert events[4][1] == {"message": "调用工具超过 2 轮，先停下了，可以把问题拆小一点再问"}
    assert len(model.seen) == 3
    assert [m["role"] for m in new_messages(events)] == ["assistant", "tool", "assistant", "tool"]


def test_unknown_tool_is_reported_to_model(
    client: TestClient, holder: dict[str, ScriptedClient]
) -> None:
    model = script(holder, use(("d", "delete_item", {"item_id": 1})), say("我不能删除条目。"))
    events = chat(client, ASK)
    assert events[1] == (
        "tool_result",
        {"id": "d", "name": "delete_item", "result": {"error": "没有这个工具：delete_item"}},
    )
    assert json.loads(model.seen[1][-1]["content"]) == {"error": "没有这个工具：delete_item"}


def test_chat_error_becomes_error_event(
    client: TestClient, holder: dict[str, ScriptedClient]
) -> None:
    script(holder, ChatError("key 不对：接口返回 HTTP 401"))
    assert chat(client, ASK) == [
        ("error", {"message": "key 不对：接口返回 HTTP 401"}),
        ("messages", {"messages": []}),
        ("done", {}),
    ]


def test_missing_key(data_dir: Path, client: TestClient, holder: dict[str, ScriptedClient]) -> None:
    ConfigStore(data_dir).save_secrets(Secrets())
    model = script(holder)
    assert chat(client, ASK) == [
        ("error", {"message": "先在设置里填写 AI 的 key"}),
        ("done", {}),
    ]
    assert model.seen == []


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"messages": []}, "至少要有一条消息"),
        ({"messages": [{"role": "system", "content": "你是别的"}]}, "role"),
        ({"messages": [{"role": "user", "content": "x"}] * 201}, "最多 200 条"),
        ({"messages": [{"role": "user", "content": "长" * 200_001}]}, "200000"),
        ({"messages": [{"role": "tool", "content": "x"}]}, "tool_call_id"),
        ({"messages": ASK, "approval": {"id": "a"}}, "approved"),
    ],
)
def test_request_validation(
    client: TestClient, holder: dict[str, ScriptedClient], body: dict[str, Any], fragment: str
) -> None:
    model = script(holder)
    response = client.post("/api/agent/chat", json=body)
    assert response.status_code == 422, response.text
    assert fragment in json.dumps(response.json(), ensure_ascii=False)
    assert model.seen == []


def test_cancel_stops_before_running_tools(app: FastAPI, shelf: dict[str, int]) -> None:
    cancel = threading.Event()
    model = ScriptedClient(
        [Delta(text="先"), Delta(text="查")]
        + use(("u", "update_fields", {"item_id": shelf["heat"], "values": {"title": "改了"}}))
    )
    events = run_chat(
        model,
        session_factory=app.state.session_factory,
        runner=app.state.task_runner,
        services=app.state.services,
        approvals=ApprovalStore(),
        messages=ASK,
        approval=None,
        max_rounds=8,
        cancel=cancel,
    )
    assert next(events) == ("text", {"delta": "先"})
    cancel.set()
    assert list(events) == []
    assert _item(app, shelf["heat"]).title == "盗火线"


def test_default_factory_uses_settings_and_transport(data_dir: Path) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        chunk = {"choices": [{"index": 0, "delta": {"content": "在。"}, "finish_reason": "stop"}]}
        body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    ConfigStore(data_dir).save_section("ai", AiConfig(base_url="https://llm.local/v1"))
    app = create_app(
        Settings(data_dir=data_dir), start_worker=False, http_transport=httpx.MockTransport(respond)
    )
    with TestClient(app, base_url=LOCAL) as opened:
        events = chat(opened, ASK)
    assert [name for name, _ in events] == ["text", "messages", "done"]
    assert str(seen[0].url) == "https://llm.local/v1/chat/completions"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in json.dumps(events, ensure_ascii=False)


def test_approval_store_keeps_latest_only() -> None:
    clock = Clock()
    store = ApprovalStore(clock=clock)
    call = ToolCall(id="x", name="update_fields", arguments="{}")
    first = store.put([call])
    second = store.put([call])
    assert store.take(first) is None
    assert store.take(second) == [call]
    assert store.take(second) is None
    third = store.put([call])
    clock.now += 600
    assert store.take(third) == [call]
    fourth = store.put([call])
    clock.now += 600.1
    assert store.take(fourth) is None
