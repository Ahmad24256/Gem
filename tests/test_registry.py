from __future__ import annotations

from gem.tools import build_default_registry
from gem.tools.base import Tool, ToolRegistry, ToolResult


class EchoTool(Tool):
    name = "echo"
    description = "Echo back"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def run(self, text: str, **_):
        return ToolResult(ok=True, output=text)


class BoomTool(Tool):
    name = "boom"
    description = "Always crashes"
    parameters = {"type": "object", "properties": {}}

    def run(self, **_):
        raise RuntimeError("kaboom")


def test_schemas_openai_format(project):
    registry = build_default_registry(project)
    schemas = registry.schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {
        "read_file",
        "write_file",
        "edit_file",
        "delete_file",
        "list_dir",
        "grep",
        "glob_files",
        "run_command",
    }
    for s in schemas:
        assert s["type"] == "function"
        assert s["function"]["parameters"]["type"] == "object"


def test_dispatch_ok_and_redaction(project):
    registry = ToolRegistry(secret="sk-or-abc")
    registry.register(EchoTool())
    result = registry.dispatch("echo", {"text": "key is sk-or-abc"})
    assert result.ok
    assert "sk-or-abc" not in result.output
    assert "***REDACTED***" in result.output


def test_dispatch_accepts_json_string(project):
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = registry.dispatch("echo", '{"text": "hi"}')
    assert result.ok and result.output == "hi"


def test_dispatch_unknown_tool(project):
    registry = ToolRegistry()
    result = registry.dispatch("nope", {})
    assert not result.ok
    assert "unknown tool" in result.output


def test_dispatch_bad_json(project):
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = registry.dispatch("echo", "{bad json")
    assert not result.ok
    assert "invalid JSON" in result.output


def test_dispatch_bad_arguments_type(project):
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = registry.dispatch("echo", {"wrong": 1})
    assert not result.ok
    assert "bad arguments" in result.output


def test_dispatch_crash_is_caught(project):
    registry = ToolRegistry()
    registry.register(BoomTool())
    result = registry.dispatch("boom", {})
    assert not result.ok
    assert "kaboom" in result.output


def test_dispatch_truncates_long_output(project):
    registry = ToolRegistry(max_output=50)
    registry.register(EchoTool())
    result = registry.dispatch("echo", {"text": "x" * 500})
    assert len(result.output) < 200
    assert "truncated" in result.output
