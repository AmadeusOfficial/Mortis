"""一次提问的循环：调模型，执行工具，把结果交回模型，直到它给出回答。对话不落库。"""

import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from mortis.agent.client import ChatClient, ChatError, Delta, ToolCall
from mortis.agent.tools import (
    ToolContext,
    approval_summary,
    calls_needing_approval,
    describe_call,
    run_tool,
    tool_schemas,
)
from mortis.services import Services
from mortis.tasks.runner import TaskRunner

APPROVAL_TTL_SECONDS = 600.0
ARGUMENTS_PREVIEW = 1000
RESULT_PREVIEW = 4000
FULL_RESULT_TOOLS = frozenset({"search_items"})
"""这些工具的结果原样推给页面，好画条目卡片。"""
EXPIRED = "这次批准已过期，请重新提问"
MISMATCHED = "这次批准和当前对话对不上，请重新提问"
REJECTED = "用户拒绝了这次修改"
NOT_APPROVED = "用户没有批准这次修改，没有执行"

SYSTEM_PROMPT = "\n".join(
    [
        "你是莫提斯（Mortis）的片库助手，帮用户查找、播放和整理本机片库里的电影、剧、动漫和游戏。",
        "",
        "- 只能通过提供的工具查询和修改片库，不要编造库里没有的条目、编号或文件。",
        "- 播放前先用 search_items 检索。只有一条合适的结果才播放；"
        "多条同名或拿不准时，列出候选（标题、年份、所在库）让用户选，不要猜。",
        "- play_item 返回 needs_choice 时，把候选（文件名或程序名）告诉用户，"
        "等用户选了再带上 file_id 或 target 调用。",
        "- 用 update_fields 改过的字段会被锁定，以后刮削不再覆盖；"
        "改之前先用 get_item 看清当前值和能改的字段。",
        "- 一次修改两个及以上条目时，系统会先请用户批准。",
        "- 做不到的事直接说做不到：删除条目或文件、改设置、直接写数据库、转码、在别的设备上播放。",
        "- 回答简洁，用中文，可以用 Markdown。",
    ]
)

Event = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class Approval:
    id: str
    approved: bool


class ApprovalStore:
    """待批准的工具调用。只存最近一份，过期作废，取过一次就删掉。"""

    def __init__(
        self, *, ttl: float = APPROVAL_TTL_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._slot: tuple[str, list[ToolCall], float] | None = None

    def put(self, calls: list[ToolCall]) -> str:
        approval_id = uuid.uuid4().hex
        with self._lock:
            self._slot = (approval_id, list(calls), self._clock())
        return approval_id

    def take(self, approval_id: str) -> list[ToolCall] | None:
        with self._lock:
            if self._slot is None or self._slot[0] != approval_id:
                return None
            _, calls, created = self._slot
            self._slot = None
        if self._clock() - created > self._ttl:
            return None
        return calls


def run_chat(
    client: ChatClient,
    *,
    session_factory: sessionmaker[Session],
    runner: TaskRunner,
    services: Services,
    approvals: ApprovalStore,
    messages: list[dict[str, Any]],
    approval: Approval | None,
    max_rounds: int,
    cancel: threading.Event | None = None,
) -> Iterator[Event]:
    """产出 (事件名, 数据)。最后总是 messages（本次新增的消息）和 done；被取消时直接结束。"""
    stop = cancel if cancel is not None else threading.Event()
    history = [dict(message) for message in messages]
    added: list[dict[str, Any]] = []

    def execute(call: ToolCall) -> Iterator[Event]:
        yield "tool", {"id": call.id, "name": call.name, "arguments": _arguments_preview(call)}
        with session_factory() as session:
            result = run_tool(ToolContext(session, runner, services), call.name, call.arguments)
        yield (
            "tool_result",
            {"id": call.id, "name": call.name, "result": _result_preview(call, result)},
        )
        answer(call, result)

    def answer(call: ToolCall, result: dict[str, Any]) -> None:
        message = {
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(result, ensure_ascii=False),
        }
        history.append(message)
        added.append(message)

    if approval is not None:
        calls = approvals.take(approval.id)
        if calls is None or not {call.id for call in calls} <= _open_call_ids(history):
            yield "error", {"message": EXPIRED if calls is None else MISMATCHED}
            yield "messages", {"messages": []}
            yield "done", {}
            return
        history = _close_open_calls(history, keep={call.id for call in calls})
        for call in calls:
            if stop.is_set():
                return
            if approval.approved:
                yield from execute(call)
            else:
                answer(call, {"error": REJECTED})
    else:
        history = _close_open_calls(history, keep=set())

    tools = tool_schemas()
    rounds = 0
    while True:
        text: list[str] = []
        calls = []
        try:
            for delta in _stream(client, [_system(), *history], tools, stop):
                if delta.text:
                    text.append(delta.text)
                    yield "text", {"delta": delta.text}
                if delta.tool_calls:
                    calls = _with_ids(delta.tool_calls, rounds)
        except ChatError as error:
            yield "error", {"message": error.message}
            break
        if stop.is_set():
            return
        content = "".join(text)
        if not calls:
            _append(history, added, {"role": "assistant", "content": content})
            break
        if rounds >= max_rounds:
            if content:
                _append(history, added, {"role": "assistant", "content": content})
            message = f"调用工具超过 {max_rounds} 轮，先停下了，可以把问题拆小一点再问"
            yield "error", {"message": message}
            break
        rounds += 1
        _append(history, added, _assistant(content, calls))
        held = calls_needing_approval(calls)
        held_ids = {call.id for call in held}
        for call in calls:
            if stop.is_set():
                return
            if call.id not in held_ids:
                yield from execute(call)
        if held:
            with session_factory() as session:
                items = [describe_call(session, call) for call in held]
            yield (
                "approval",
                {"id": approvals.put(held), "summary": approval_summary(held), "items": items},
            )
            break
    yield "messages", {"messages": added}
    yield "done", {}


def _system() -> dict[str, Any]:
    return {"role": "system", "content": SYSTEM_PROMPT}


def _stream(
    client: ChatClient,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    stop: threading.Event,
) -> Iterator[Delta]:
    """取消时及时关掉模型的流，不再读剩下的增量。"""
    deltas = client.stream(messages, tools)
    try:
        for delta in deltas:
            if stop.is_set():
                return
            yield delta
    finally:
        close = getattr(deltas, "close", None)
        if close is not None:
            close()


def _with_ids(calls: list[ToolCall], rounds: int) -> list[ToolCall]:
    """有的兼容接口不给调用 id，补上一个，好让结果对得上。"""
    return [
        call
        if call.id
        else ToolCall(id=f"call_{rounds}_{n}", name=call.name, arguments=call.arguments)
        for n, call in enumerate(calls)
    ]


def _assistant(content: str, calls: list[ToolCall]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ],
    }


def _append(
    history: list[dict[str, Any]], added: list[dict[str, Any]], message: dict[str, Any]
) -> None:
    history.append(message)
    added.append(message)


def _call_ids(message: dict[str, Any]) -> list[str]:
    return [call["id"] for call in message.get("tool_calls") or []]


def _open_call_ids(history: list[dict[str, Any]]) -> set[str]:
    """最后一条带工具调用的助手消息之后只有工具结果时，其中还没有结果的调用。"""
    answered: set[str] = set()
    for message in reversed(history):
        if message["role"] == "tool":
            answered.add(message.get("tool_call_id") or "")
            continue
        if message["role"] == "assistant" and message.get("tool_calls"):
            return set(_call_ids(message)) - answered
        return set()
    return set()


def _close_open_calls(history: list[dict[str, Any]], *, keep: set[str]) -> list[dict[str, Any]]:
    """接口要求每个工具调用后面都有结果。没批准就换了话题的调用，补一条「没有执行」。"""
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(history):
        message = history[index]
        result.append(message)
        index += 1
        if message["role"] != "assistant" or not message.get("tool_calls"):
            continue
        answered: set[str] = set()
        while index < len(history) and history[index]["role"] == "tool":
            answered.add(history[index].get("tool_call_id") or "")
            result.append(history[index])
            index += 1
        for call_id in _call_ids(message):
            if call_id not in answered and call_id not in keep:
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps({"error": NOT_APPROVED}, ensure_ascii=False),
                    }
                )
    return result


def _arguments_preview(call: ToolCall) -> Any:
    try:
        parsed = json.loads(call.arguments)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and len(call.arguments) <= ARGUMENTS_PREVIEW:
        return parsed
    return call.arguments[:ARGUMENTS_PREVIEW]


def _result_preview(call: ToolCall, result: dict[str, Any]) -> Any:
    if call.name in FULL_RESULT_TOOLS:
        return result
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= RESULT_PREVIEW:
        return result
    return {"truncated": True, "preview": text[:RESULT_PREVIEW]}
