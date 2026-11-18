import json
import logging
import threading
import traceback
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import iterate_in_threadpool

from mortis.agent.check import scrub
from mortis.agent.client import NO_KEY, ChatClient, ChatError
from mortis.agent.loop import Approval, Event, run_chat
from mortis.config.models import AppConfig, Secrets
from mortis.errors import UserError

router = APIRouter(prefix="/api/agent")
logger = logging.getLogger("mortis.agent")

MAX_MESSAGES = 200
MAX_CHARS = 100_000

ChatClientFactory = Callable[[AppConfig, Secrets], ChatClient]


class FunctionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    arguments: str = "{}"


class ToolCallIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: Literal["function"] = "function"
    function: FunctionIn


class MessageIn(BaseModel):
    """system 由后端加在最前面，前端不能给。"""

    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCallIn] | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None
    """推理模型的思考过程，只在带工具调用的助手消息上保留。"""


class ApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    approved: bool


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[MessageIn]
    approval: ApprovalIn | None = None


def _messages(body: ChatRequest) -> list[dict[str, Any]]:
    if not body.messages:
        raise UserError("至少要有一条消息")
    if len(body.messages) > MAX_MESSAGES:
        raise UserError(f"对话太长了：消息最多 {MAX_MESSAGES} 条，清空对话再问")
    total = 0
    result: list[dict[str, Any]] = []
    for message in body.messages:
        total += len(message.content or "") + len(message.reasoning_content or "")
        total += sum(len(call.function.arguments) for call in message.tool_calls or [])
        if message.role == "user":
            if message.content is None:
                raise UserError("user 消息要有 content")
            result.append({"role": "user", "content": message.content})
        elif message.role == "tool":
            if not message.tool_call_id:
                raise UserError("tool 消息要带 tool_call_id")
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content or "",
                }
            )
        elif message.tool_calls:
            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [call.model_dump() for call in message.tool_calls],
            }
            if message.reasoning_content:
                assistant["reasoning_content"] = message.reasoning_content
            result.append(assistant)
        else:
            result.append({"role": "assistant", "content": message.content or ""})
    if total > MAX_CHARS:
        raise UserError(f"对话太长了：总长度不能超过 {MAX_CHARS} 字，清空对话再问")
    return result


def _unexpected(error: Exception, key: str) -> Iterator[Event]:
    trace = "".join(traceback.format_exception(error))
    logger.error("对话出错：%s", scrub(trace, key))
    yield "error", {"message": f"出错了：{type(error).__name__}"}
    yield "messages", {"messages": []}
    yield "done", {}


def _sse(event: Event) -> str:
    name, data = event
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    messages = _messages(body)
    state = request.app.state
    store = state.config_store
    config = store.load_config()
    secrets = store.load_secrets()
    approval = Approval(body.approval.id, body.approval.approved) if body.approval else None
    cancel = threading.Event()

    def events() -> Iterator[Event]:
        key = secrets.ai.api_key.strip()
        if not key:
            yield "error", {"message": NO_KEY}
            yield "done", {}
            return
        try:
            client = state.chat_client_factory(config, secrets)
        except ChatError as error:
            yield "error", {"message": error.message}
            yield "done", {}
            return
        except Exception as error:
            yield from _unexpected(error, key)
            return
        try:
            yield from run_chat(
                client,
                session_factory=state.session_factory,
                runner=state.task_runner,
                services=state.services,
                approvals=state.approvals,
                messages=messages,
                approval=approval,
                max_rounds=config.ai.max_rounds,
                cancel=cancel,
            )
        except Exception as error:
            yield from _unexpected(error, key)
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                close()

    async def stream() -> AsyncIterator[str]:
        source = events()
        try:
            async for event in iterate_in_threadpool(source):
                if await request.is_disconnected():
                    break
                yield _sse(event)
        finally:
            cancel.set()
            try:
                source.close()
            except ValueError:
                # 还在线程里跑着；它看到 cancel 会自己停。
                pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
