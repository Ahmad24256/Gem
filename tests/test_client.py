from __future__ import annotations

import json

import httpx
import pytest

from gem.client import ChatResult, OpenRouterClient, OpenRouterError, ToolCall

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


def make_client(handler) -> OpenRouterClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(
        base_url="https://openrouter.ai/api/v1",
        transport=transport,
        headers={
            "Authorization": "Bearer sk-or-test",
            "Content-Type": "application/json",
        },
    )
    return OpenRouterClient("sk-or-test", http_client=http_client)


def test_chat_sends_tools_and_parses_tool_calls():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": "anthropic/claude-3.5-sonnet",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "src/main.py"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )

    client = make_client(handler)
    result = client.chat(
        [{"role": "user", "content": "read main.py"}], tools=TOOLS, model="m-x"
    )
    assert captured["auth"] == "Bearer sk-or-test"
    assert captured["body"]["model"] == "m-x"
    assert captured["body"]["stream"] is False
    assert captured["body"]["tools"] == TOOLS
    assert captured["body"]["messages"][0]["content"] == "read main.py"
    assert result.content is None
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.name == "read_file"
    assert tc.arguments == {"path": "src/main.py"}
    assert tc.to_message_dict()["function"]["arguments"] == '{"path": "src/main.py"}'


def test_chat_plain_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello there"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = make_client(handler)
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result.content == "Hello there"
    assert result.tool_calls == []


def test_chat_error_raises_with_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "Invalid API key"}}
        )

    client = make_client(handler)
    with pytest.raises(OpenRouterError) as exc_info:
        client.chat([{"role": "user", "content": "hi"}])
    assert exc_info.value.status_code == 401
    assert "Invalid API key" in str(exc_info.value)


def test_stream_chat_parses_sse_tokens_and_tool_calls():
    captured = {}

    chunks = [
        {"model": "m", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Let me "}}]},
        {"model": "m", "choices": [{"index": 0, "delta": {"content": "look."}}]},
        {"model": "m", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_9", "type": "function", "function": {"name": "gre", "arguments": ""}}]}}]},
        {"model": "m", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"name": "p", "arguments": '{"pat' }}]}}]},
        {"model": "m", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'tern": "def"}'}}]}}]},
    ]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=sse.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

    client = make_client(handler)
    tokens: list[str] = []
    result = client.stream_chat(
        [{"role": "user", "content": "search"}], tools=TOOLS, on_token=tokens.append
    )
    assert captured["body"]["stream"] is True
    assert captured["body"]["tools"] == TOOLS
    assert tokens == ["Let me ", "look."]
    assert result.content == "Let me look."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_9"
    assert result.tool_calls[0].name == "grep"
    assert result.tool_calls[0].arguments == {"pattern": "def"}


def test_stream_chat_skips_heartbeats_and_bad_json():
    sse = (
        ": heartbeat\n\n"
        "data: not-json\n\n"
        'data: {"choices": [{"index": 0, "delta": {"content": "ok"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse.encode())

    client = make_client(handler)
    result = client.stream_chat([{"role": "user", "content": "x"}])
    assert result.content == "ok"
    assert result.tool_calls == []


def test_list_models():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "anthropic/claude-3.5-sonnet"}]})

    client = make_client(handler)
    models = client.list_models()
    assert models == [{"id": "anthropic/claude-3.5-sonnet"}]


def test_tool_call_fallback_on_bad_arguments_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "f", "arguments": "{not json"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = make_client(handler)
    result = client.chat([{"role": "user", "content": "x"}])
    assert result.tool_calls[0].arguments == {"_unparsed": "{not json"}
