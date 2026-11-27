from __future__ import annotations

import io

from rich.console import Console

from gem.agent import Agent
from gem.client import ChatResult, ToolCall
from gem.config import Config
from gem.tools import build_default_registry


class FakeClient:
    def __init__(self, script: list[ChatResult]):
        self.script = list(script)
        self.calls: list[dict] = []

    def chat(self, messages, model=None, tools=None, temperature=0.2):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self.script:
            raise AssertionError("FakeClient script exhausted")
        return self.script.pop(0)

    def stream_chat(self, messages, model=None, tools=None, temperature=0.2, on_token=None):
        result = self.chat(messages, model=model, tools=tools, temperature=temperature)
        if on_token and result.content:
            for ch in result.content:
                on_token(ch)
        return result

    def close(self):
        pass


def make_agent(project, script, stream=False, max_turns=10) -> tuple[Agent, FakeClient]:
    config = Config(
        api_key="sk-or-test",
        model="test-model",
        project_dir=project,
        max_turns=max_turns,
        stream=stream,
    )
    registry = build_default_registry(project, secret=config.api_key)
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    client = FakeClient(script)
    agent = Agent(client, registry, config, console=console)
    return agent, client


def test_agent_executes_tool_then_answers(project):
    script = [
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "src/main.py"})],
        ),
        ChatResult(
            content='Done. Here is the code:\n\n```python\nprint("hello")\n```',
            tool_calls=[],
        ),
    ]
    agent, client = make_agent(project, script)
    answer = agent.run("read src/main.py and show me the print statement")

    assert "```python" in answer
    assert client.calls[0]["tools"], "first call should include tool schemas"
    assert client.calls[1]["tools"], "second call should include tool schemas"

    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]

    tool_msg = agent.messages[3]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "1"
    assert tool_msg["name"] == "read_file"
    assert 'print("hello")' in tool_msg["content"]

    assistant_msg = agent.messages[2]
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "read_file"


def test_agent_multi_step_chain(project):
    script = [
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="a", name="write_file", arguments={"path": "out.txt", "content": "v1"})],
        ),
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="b", name="edit_file", arguments={"path": "out.txt", "old_string": "v1", "new_string": "v2"})],
        ),
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="c", name="run_command", arguments={"command": "cat out.txt"})],
        ),
        ChatResult(content="The file now contains v2.", tool_calls=[]),
    ]
    agent, _ = make_agent(project, script)
    answer = agent.run("create, edit, and check out.txt")
    assert answer == "The file now contains v2."
    assert (project / "out.txt").read_text() == "v2"
    tool_outputs = [m["content"] for m in agent.messages if m["role"] == "tool"]
    assert any("v2" in c for c in tool_outputs)


def test_agent_handles_tool_error_and_recovers(project):
    script = [
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "missing.txt"})],
        ),
        ChatResult(content="That file does not exist.", tool_calls=[]),
    ]
    agent, _ = make_agent(project, script)
    answer = agent.run("read missing.txt")
    assert "does not exist" in answer
    tool_msg = [m for m in agent.messages if m["role"] == "tool"][0]
    assert "Error" in tool_msg["content"]


def test_agent_unknown_tool_reported(project):
    script = [
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="1", name="no_such_tool", arguments={})],
        ),
        ChatResult(content="ok", tool_calls=[]),
    ]
    agent, _ = make_agent(project, script)
    agent.run("do something")
    tool_msg = [m for m in agent.messages if m["role"] == "tool"][0]
    assert "unknown tool" in tool_msg["content"]


def test_agent_max_turns_forces_final_answer(project):
    script = [
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id=str(i), name="list_dir", arguments={"path": "."})],
        )
        for i in range(3)
    ] + [ChatResult(content="final answer after max turns", tool_calls=[])]
    agent, client = make_agent(project, script, max_turns=3)
    answer = agent.run("loop a bit")
    assert answer == "final answer after max turns"
    assert client.calls[-1]["tools"] is None


def test_agent_streaming_mode(project):
    script = [
        ChatResult(content="working...", tool_calls=[ToolCall(id="1", name="list_dir", arguments={})]),
        ChatResult(content="```python\nprint(1)\n```", tool_calls=[]),
    ]
    agent, _ = make_agent(project, script, stream=True)
    answer = agent.run("hi")
    assert "```python" in answer


def test_agent_redacts_api_key_in_tool_output(project, monkeypatch):
    monkeypatch.setenv("GEM_TEST_SECRET", "sk-or-test")
    script = [
        ChatResult(
            content=None,
            tool_calls=[
                ToolCall(id="1", name="run_command", arguments={"command": "echo sk-or-test"})
            ],
        ),
        ChatResult(content="done", tool_calls=[]),
    ]
    agent, _ = make_agent(project, script)
    agent.run("echo the key")
    tool_msg = [m for m in agent.messages if m["role"] == "tool"][0]
    assert "sk-or-test" not in tool_msg["content"]
    assert "***REDACTED***" in tool_msg["content"]


def test_agent_reset_clears_history(project):
    agent, _ = make_agent(
        project, [ChatResult(content="hi", tool_calls=[])]
    )
    agent.run("hello")
    assert len(agent.messages) == 3
    agent.reset()
    assert len(agent.messages) == 1
    assert agent.messages[0]["role"] == "system"
