from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import httpx

from .config import DEFAULT_BASE_URL


class OpenRouterError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_message_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[ToolCall]
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_tool_calls(raw_calls: Iterable[dict[str, Any]] | None) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw in raw_calls or []:
        fn = raw.get("function") or {}
        arguments_raw = fn.get("arguments") or "{}"
        try:
            arguments = json.loads(arguments_raw)
            if not isinstance(arguments, dict):
                arguments = {"value": arguments}
        except json.JSONDecodeError:
            arguments = {"_unparsed": arguments_raw}
        calls.append(
            ToolCall(
                id=raw.get("id") or f"call_{len(calls)}",
                name=fn.get("name") or "",
                arguments=arguments,
            )
        )
    return calls


class OpenRouterClient:
    """Minimal OpenAI-compatible client for the OpenRouter API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "anthropic/claude-3.5-sonnet",
        timeout: float = 180.0,
        transport: httpx.BaseTransport | None = None,
        http_client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        if http_client is not None:
            self._client = http_client
        else:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=timeout,
                headers=self._headers(),
                transport=transport,
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Ahmad24256/Gem",
            "X-Title": "Gem Agent",
        }

    def _build_body(
        self,
        messages: list[dict[str, Any]],
        model: str | None,
        tools: list[dict[str, Any]] | None,
        stream: bool,
        temperature: float,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
        return body

    def _check_response(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            detail = response.text
            try:
                payload = response.json()
                detail = payload.get("error", {}).get("message") or detail
            except (json.JSONDecodeError, AttributeError):
                pass
            raise OpenRouterError(
                f"OpenRouter request failed (HTTP {response.status_code}): {detail}",
                status_code=response.status_code,
            )

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> ChatResult:
        body = self._build_body(messages, model, tools, stream=False, temperature=temperature)
        response = self._client.post("/chat/completions", json=body)
        self._check_response(response)
        payload = response.json()
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return ChatResult(
            content=message.get("content"),
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
            model=payload.get("model"),
            raw=payload,
        )

    def iter_chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ):
        """Stream a chat completion, yielding ("token", text) events and finally
        ("done", ChatResult) with the accumulated content and tool calls."""
        body = self._build_body(messages, model, tools, stream=True, temperature=temperature)
        content_parts: list[str] = []
        pending: dict[int, dict[str, Any]] = {}
        model_seen: str | None = None

        with self._client.stream("POST", "/chat/completions", json=body) as response:
            self._check_response(response)
            for line in response.iter_lines():
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                model_seen = chunk.get("model") or model_seen
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    token = delta.get("content")
                    if token:
                        content_parts.append(token)
                        yield "token", token
                    for raw_call in delta.get("tool_calls") or []:
                        index = raw_call.get("index", 0)
                        slot = pending.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        if raw_call.get("id"):
                            slot["id"] = raw_call["id"]
                        fn = raw_call.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

        tool_calls: list[ToolCall] = []
        for index in sorted(pending):
            slot = pending[index]
            try:
                arguments = json.loads(slot["arguments"] or "{}")
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
            except json.JSONDecodeError:
                arguments = {"_unparsed": slot["arguments"]}
            tool_calls.append(
                ToolCall(
                    id=slot["id"] or f"call_{index}",
                    name=slot["name"],
                    arguments=arguments,
                )
            )

        yield "done", ChatResult(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            model=model_seen,
        )

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        on_token: Callable[[str], None] | None = None,
    ) -> ChatResult:
        result: ChatResult | None = None
        for kind, payload in self.iter_chat_stream(
            messages, model=model, tools=tools, temperature=temperature
        ):
            if kind == "token":
                if on_token:
                    on_token(payload)
            else:
                result = payload
        assert result is not None
        return result

    def list_models(self) -> list[dict[str, Any]]:
        response = self._client.get("/models")
        self._check_response(response)
        return response.json().get("data", [])

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OpenRouterClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
