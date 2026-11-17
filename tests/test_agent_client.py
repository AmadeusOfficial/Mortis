"""OpenAI 兼容客户端：用录好的 SSE 文本测拼装和错误。"""

import json
from collections.abc import Callable

import httpx2 as httpx
import pytest

from mortis.agent.client import (
    ChatError,
    Delta,
    OpenAICompatClient,
    ToolCall,
    make_chat_client,
)
from mortis.config.models import AiConfig, AiSecrets, AppConfig, NetworkConfig, Secrets
from mortis.net.http import build_client

KEY = "sk-secret-123"
BASE = "https://api.example.com/v1"
TOOLS = [{"type": "function", "function": {"name": "search_items", "parameters": {}}}]


def _sse(*chunks: dict | str) -> bytes:
    lines = []
    for chunk in chunks:
        payload = chunk if isinstance(chunk, str) else json.dumps(chunk, ensure_ascii=False)
        lines.append(f"data: {payload}\n\n")
    return "".join(lines).encode("utf-8")


def _delta(delta: dict, finish: str | None = None) -> dict:
    return {"id": "c1", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class Recorder:
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def body(self) -> dict:
        return json.loads(self.requests[-1].content)


def _client(
    respond: Callable[[httpx.Request], httpx.Response],
) -> tuple[OpenAICompatClient, Recorder]:
    recorder = Recorder(respond)
    http = build_client(NetworkConfig(), Secrets(), transport=httpx.MockTransport(recorder))
    return OpenAICompatClient(BASE, "deepseek-chat", KEY, http), recorder


def _stream(body: bytes, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(
        status, content=body, headers={"content-type": "text/event-stream"}
    )


def _collect(deltas: list[Delta]) -> tuple[str, list[ToolCall], str | None]:
    text = "".join(d.text for d in deltas)
    calls = [call for d in deltas for call in d.tool_calls]
    finish = next((d.finish_reason for d in reversed(deltas) if d.finish_reason), None)
    return text, calls, finish


MESSAGES = [{"role": "user", "content": "库里有什么"}]


def test_plain_text_streams_and_sends_request() -> None:
    body = _sse(
        _delta({"role": "assistant", "content": ""}),
        _delta({"content": "库里"}),
        _delta({"content": "有三部电影。"}),
        _delta({}, "stop"),
        "[DONE]",
    )
    client, recorder = _client(_stream(body))
    deltas = list(client.stream(MESSAGES, TOOLS))
    assert [d.text for d in deltas if d.text] == ["库里", "有三部电影。"]
    assert _collect(deltas) == ("库里有三部电影。", [], "stop")

    request = recorder.requests[0]
    assert str(request.url) == f"{BASE}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    sent = recorder.body()
    assert sent["model"] == "deepseek-chat"
    assert sent["stream"] is True
    assert sent["messages"] == MESSAGES
    assert sent["tools"] == TOOLS and sent["tool_choice"] == "auto"


def test_without_tools_omits_tool_choice() -> None:
    client, recorder = _client(_stream(_sse(_delta({"content": "好"}, "stop"), "[DONE]")))
    assert _collect(list(client.stream(MESSAGES, [])))[0] == "好"
    assert "tools" not in recorder.body() and "tool_choice" not in recorder.body()


def test_single_tool_call_accumulates_arguments() -> None:
    body = _sse(
        _delta(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search_items", "arguments": ""},
                    }
                ]
            }
        ),
        _delta({"tool_calls": [{"index": 0, "function": {"arguments": '{"query":'}}]}),
        _delta({"tool_calls": [{"index": 0, "function": {"arguments": ' "教父"}'}}]}),
        _delta({}, "tool_calls"),
        "[DONE]",
    )
    client, _ = _client(_stream(body))
    text, calls, finish = _collect(list(client.stream(MESSAGES, TOOLS)))
    assert text == ""
    assert calls == [ToolCall(id="call_1", name="search_items", arguments='{"query": "教父"}')]
    assert finish == "tool_calls"


def test_parallel_tool_calls_arrive_interleaved() -> None:
    body = _sse(
        _delta({"content": "我查一下。"}),
        _delta(
            {
                "tool_calls": [
                    {"index": 0, "id": "a", "function": {"name": "get_item", "arguments": '{"ite'}},
                ]
            }
        ),
        _delta(
            {
                "tool_calls": [
                    {
                        "index": 1,
                        "id": "b",
                        "function": {"name": "get_item", "arguments": '{"item'},
                    },
                ]
            }
        ),
        _delta({"tool_calls": [{"index": 0, "function": {"arguments": 'm_id": 1}'}}]}),
        _delta({"tool_calls": [{"index": 1, "function": {"arguments": '_id": 2}'}}]}),
        _delta({}, "tool_calls"),
    )
    client, _ = _client(_stream(body))
    text, calls, finish = _collect(list(client.stream(MESSAGES, TOOLS)))
    assert text == "我查一下。"
    assert calls == [
        ToolCall(id="a", name="get_item", arguments='{"item_id": 1}'),
        ToolCall(id="b", name="get_item", arguments='{"item_id": 2}'),
    ]
    assert finish == "tool_calls"


def test_empty_arguments_become_empty_object() -> None:
    body = _sse(
        _delta({"tool_calls": [{"index": 0, "id": "x", "function": {"name": "list_unmatched"}}]}),
        _delta({}, "tool_calls"),
        "[DONE]",
    )
    client, _ = _client(_stream(body))
    assert _collect(list(client.stream(MESSAGES, TOOLS)))[1] == [
        ToolCall(id="x", name="list_unmatched", arguments="{}")
    ]


def test_reasoning_content_is_not_answer() -> None:
    body = (
        b": keep-alive\n\n"
        + _sse(
            _delta({"role": "assistant", "reasoning_content": "用户想要", "content": None}),
            _delta({"reasoning_content": "列出电影"}),
            _delta({"content": "有两部。"}),
            {"id": "c1", "choices": [], "usage": {"total_tokens": 9}},
            _delta({}, "stop"),
            "[DONE]",
        )
        + _sse(_delta({"content": "DONE 之后的不要"}))
    )
    client, _ = _client(_stream(body))
    assert _collect(list(client.stream(MESSAGES, TOOLS))) == ("有两部。", [], "stop")


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"error": {"message": f"bad key {KEY}"}}, "key 不对：接口返回 HTTP 401"),
        (403, {"error": {"message": "forbidden"}}, "key 不对：接口返回 HTTP 403"),
        (
            429,
            {"error": {"message": "rate limited"}},
            "请求太频繁或额度用完了：HTTP 429：rate limited",
        ),
        (500, {"error": {"message": f"boom {KEY}"}}, "AI 接口出错：HTTP 500：boom ***"),
        (404, {"error": "no route"}, "接口返回 HTTP 404：no route，检查接口地址"),
    ],
)
def test_http_errors_become_chinese_chat_errors(status: int, body: dict, message: str) -> None:
    client, _ = _client(lambda _request: httpx.Response(status, json=body))
    with pytest.raises(ChatError) as caught:
        list(client.stream(MESSAGES, TOOLS))
    assert caught.value.message == message
    assert KEY not in str(caught.value)


def test_timeout_and_connection_failure() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client, _ = _client(timeout)
    with pytest.raises(ChatError) as caught:
        list(client.stream(MESSAGES, TOOLS))
    assert caught.value.message == "AI 接口超时，稍后再试"

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused for {KEY}", request=request)

    client, _ = _client(refused)
    with pytest.raises(ChatError) as caught:
        list(client.stream(MESSAGES, TOOLS))
    assert caught.value.message == "连不上 AI 接口：refused for ***"


def test_bad_stream_and_error_events() -> None:
    client, _ = _client(_stream(b"data: {not json\n\n"))
    with pytest.raises(ChatError) as caught:
        list(client.stream(MESSAGES, TOOLS))
    assert caught.value.message == "AI 接口返回的内容看不懂，检查接口地址"

    client, _ = _client(_stream(_sse({"error": {"message": f"quota {KEY}"}})))
    with pytest.raises(ChatError) as caught:
        list(client.stream(MESSAGES, TOOLS))
    assert caught.value.message == "AI 接口出错：quota ***"


def test_factory_builds_from_settings() -> None:
    recorder = Recorder(_stream(_sse(_delta({"content": "嗯"}, "stop"), "[DONE]")))
    config = AppConfig(ai=AiConfig(base_url="https://llm.local/v1/", model="qwen"))
    secrets = Secrets(ai=AiSecrets(api_key=f"  {KEY} "))
    with make_chat_client(config, secrets, transport=httpx.MockTransport(recorder)) as client:
        assert _collect(list(client.stream(MESSAGES, TOOLS)))[0] == "嗯"
    request = recorder.requests[0]
    assert str(request.url) == "https://llm.local/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content)["model"] == "qwen"

    with pytest.raises(ChatError) as caught:
        make_chat_client(config, Secrets())
    assert caught.value.message == "先在设置里填写 AI 的 key"
