import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol, Self

import httpx2 as httpx

from mortis.agent.check import error_reason, message_of, scrub, status_message
from mortis.config.models import AppConfig, Secrets
from mortis.errors import UserError
from mortis.net.http import build_client

READ_TIMEOUT_SECONDS = 120.0
"""两段增量之间最多等多久。推理模型开口前可能想很久。"""
CONNECT_TIMEOUT_SECONDS = 15.0
NO_KEY = "先在设置里填写 AI 的 key"


class ChatError(UserError):
    """模型接口出的错，信息里不带 key。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str
    """模型给的 JSON 文本，原样保留，执行时再解析。"""


@dataclass(frozen=True)
class Delta:
    """流里的一段：文字增量；或者结束时的一段，带拼好的工具调用和结束原因。"""

    text: str = ""
    reasoning: str = ""
    """推理模型的思考过程增量。"""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None


class ChatClient(Protocol):
    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Iterator[Delta]: ...


@dataclass
class _PendingCall:
    id: str = ""
    name: str = ""
    arguments: list[str] = field(default_factory=list)


class OpenAICompatClient:
    """OpenAI 兼容的 Chat Completions 流式接口。DeepSeek、通义、Kimi、Ollama 都走这一种。"""

    def __init__(self, base_url: str, model: str, api_key: str, http_client: httpx.Client) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._key = api_key.strip()
        self._http = http_client

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Iterator[Delta]:
        body: dict[str, Any] = {"model": self._model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        try:
            with self._http.stream(
                "POST",
                self._url,
                headers={"Authorization": f"Bearer {self._key}", "Accept": "text/event-stream"},
                json=body,
                timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            ) as response:
                if response.status_code != 200:
                    response.read()
                    raise self._status_error(response)
                yield from self._parse(response.iter_lines())
        except httpx.TimeoutException:
            raise ChatError("AI 接口超时，稍后再试") from None
        except httpx.HTTPError as error:
            text = str(error) or type(error).__name__
            raise ChatError(f"连不上 AI 接口：{scrub(text, self._key)}") from None

    def _status_error(self, response: httpx.Response) -> ChatError:
        reason = scrub(error_reason(response), self._key)
        return ChatError(status_message(response.status_code, reason))

    def _parse(self, lines: Iterator[str]) -> Iterator[Delta]:
        pending: dict[int, _PendingCall] = {}
        finish: str | None = None
        for line in lines:
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            if not data:
                continue
            try:
                chunk = json.loads(data)
            except ValueError:
                raise ChatError("AI 接口返回的内容看不懂，检查接口地址") from None
            if not isinstance(chunk, dict):
                raise ChatError("AI 接口返回的内容看不懂，检查接口地址")
            if "error" in chunk:
                reason = scrub(message_of(chunk), self._key) or "未知错误"
                raise ChatError(f"AI 接口出错：{reason}")
            for choice in chunk.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                # DeepSeek 的 reasoning_content 是思考过程，单独交出，不当作回答。
                thought = delta.get("reasoning_content")
                if isinstance(thought, str) and thought:
                    yield Delta(reasoning=thought)
                text = delta.get("content")
                if isinstance(text, str) and text:
                    yield Delta(text=text)
                for part in delta.get("tool_calls") or []:
                    _merge(pending, part)
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        calls = [
            ToolCall(id=call.id, name=call.name, arguments="".join(call.arguments) or "{}")
            for _, call in sorted(pending.items())
        ]
        yield Delta(tool_calls=calls, finish_reason=finish)


def _merge(pending: dict[int, _PendingCall], part: object) -> None:
    """按 index 拼：id 和名字只在第一段出现，arguments 分段到达。"""
    if not isinstance(part, dict):
        return
    index = part.get("index", len(pending))
    call = pending.setdefault(index if isinstance(index, int) else len(pending), _PendingCall())
    if isinstance(part.get("id"), str) and part["id"]:
        call.id = part["id"]
    function = part.get("function") or {}
    if isinstance(function.get("name"), str) and function["name"]:
        call.name = function["name"]
    if isinstance(function.get("arguments"), str):
        call.arguments.append(function["arguments"])


def make_chat_client(
    config: AppConfig, secrets: Secrets, *, transport: httpx.BaseTransport | None = None
) -> OpenAICompatClient:
    """按设置建客户端，走设置里的代理。用完要 close，或者放进 with。"""
    key = secrets.ai.api_key.strip()
    if not key:
        raise ChatError(NO_KEY)
    http = build_client(config.network, secrets, transport=transport)
    return OpenAICompatClient(config.ai.base_url, config.ai.model, key, http)
