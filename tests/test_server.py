from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from rich.console import Console

from gem.agent import Agent
from gem.client import ChatResult, ToolCall
from gem.config import Config
from gem.server import create_app
from gem.tools import build_default_registry


class FakeClient:
    def __init__(self, script: list[ChatResult]):
        self.script = list(script)

    def chat(self, messages, model=None, tools=None, temperature=0.2):
        if not self.script:
            raise AssertionError("FakeClient script exhausted")
        return self.script.pop(0)

    def iter_chat_stream(self, messages, model=None, tools=None, temperature=0.2):
        result = self.chat(messages, model=model, tools=tools, temperature=temperature)
        if result.content:
            for ch in result.content:
                yield "token", ch
        yield "done", result

    def close(self):
        pass


def make_script() -> list[ChatResult]:
    return [
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "src/main.py"})],
        ),
        ChatResult(content="Here you go:\n\n```python\nprint('hi')\n```", tool_calls=[]),
    ]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "main.py").write_text('print("hello")\n')

    def factory(config: Config) -> Agent:
        registry = build_default_registry(config.project_dir, secret=config.api_key)
        console = Console(file=io.StringIO(), force_terminal=False)
        return Agent(FakeClient(make_script()), registry, config, console=console)

    app = create_app(agent_factory=factory)
    return TestClient(app)


def test_index_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Gem" in resp.text
    assert "app.js" in resp.text


def test_static_assets_served(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_session_requires_api_key(client, tmp_path):
    resp = client.post("/api/sessions", json={"project_dir": str(tmp_path)})
    assert resp.status_code == 400
    assert "API key" in resp.json()["detail"]


def test_session_lifecycle_and_sse_chat(client, tmp_path):
    resp = client.post(
        "/api/sessions",
        json={
            "project_dir": str(tmp_path),
            "api_key": "sk-or-test",
            "model": "test-model",
            "max_turns": 5,
            "stream": True,
        },
    )
    assert resp.status_code == 201
    session = resp.json()
    sid = session["session_id"]
    assert session["model"] == "test-model"
    assert session["project_dir"] == str(tmp_path.resolve())

    info = client.get(f"/api/sessions/{sid}").json()
    assert info["message_count"] == 1

    chat = client.post(f"/api/sessions/{sid}/chat", json={"message": "read main.py"})
    assert chat.status_code == 200
    assert chat.headers["content-type"].startswith("text/event-stream")

    events = []
    for line in chat.text.split("\n\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    kinds = [e["type"] for e in events]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "final"
    tool_call = next(e for e in events if e["type"] == "tool_call")
    assert tool_call["name"] == "read_file"
    assert tool_call["arguments"] == {"path": "src/main.py"}
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["ok"] is True
    assert 'print("hello")' in tool_result["output"]
    final = events[-1]
    assert "```python" in final["content"]
    assert any(e["type"] == "token" for e in events)

    history = client.get(f"/api/sessions/{sid}/history").json()["messages"]
    assert [m["role"] for m in history] == ["system", "user", "assistant", "tool", "assistant"]

    assert client.post(f"/api/sessions/{sid}/reset", json={}).json() == {"ok": True}
    assert client.get(f"/api/sessions/{sid}").json()["message_count"] == 1
    assert client.delete(f"/api/sessions/{sid}").json() == {"ok": True}
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_chat_unknown_session(client):
    resp = client.post("/api/sessions/nope/chat", json={"message": "hi"})
    assert resp.status_code == 404


def test_chat_error_event_on_api_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    class FailingClient(FakeClient):
        def chat(self, messages, model=None, tools=None, temperature=0.2):
            from gem.client import OpenRouterError

            raise OpenRouterError("HTTP 401: bad key", status_code=401)

    def factory(config: Config) -> Agent:
        registry = build_default_registry(config.project_dir, secret=config.api_key)
        console = Console(file=io.StringIO(), force_terminal=False)
        return Agent(FailingClient([]), registry, config, console=console)

    test_client = TestClient(create_app(agent_factory=factory))
    resp = test_client.post(
        "/api/sessions", json={"project_dir": str(tmp_path), "api_key": "sk-or-bad"}
    )
    sid = resp.json()["session_id"]
    chat = test_client.post(f"/api/sessions/{sid}/chat", json={"message": "hi"})
    events = [
        json.loads(line[6:])
        for line in chat.text.split("\n\n")
        if line.startswith("data: ")
    ]
    assert events[-1]["type"] == "error"
    assert "401" in events[-1]["message"]
