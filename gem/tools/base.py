from __future__ import annotations

import json
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class ToolError(Exception):
    pass


@dataclass
class ToolResult:
    ok: bool
    output: str

    def to_message(self, name: str, tool_call_id: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "name": name,
            "tool_call_id": tool_call_id,
            "content": self.output,
        }


class Tool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]

    @abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        ...


class ToolRegistry:
    def __init__(self, secret: str | None = None, max_output: int = 20_000):
        self._tools: dict[str, Tool] = {}
        self.secret = secret
        self.max_output = max_output

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def _redact(self, text: str) -> str:
        if self.secret and self.secret in text:
            text = text.replace(self.secret, "***REDACTED***")
        return text

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output:
            return text
        omitted = len(text) - self.max_output
        return (
            text[: self.max_output]
            + f"\n... [output truncated, {omitted} more characters]"
        )

    def dispatch(self, name: str, arguments: dict[str, Any] | str) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(
                ok=False,
                output=f"Error: unknown tool '{name}'. Available tools: {', '.join(self.names)}",
            )
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as exc:
                return ToolResult(
                    ok=False, output=f"Error: invalid JSON arguments for '{name}': {exc}"
                )
        if not isinstance(arguments, dict):
            return ToolResult(
                ok=False, output=f"Error: arguments for '{name}' must be a JSON object"
            )
        try:
            result = tool.run(**arguments)
        except ToolError as exc:
            return ToolResult(ok=False, output=f"Error: {exc}")
        except TypeError as exc:
            return ToolResult(
                ok=False, output=f"Error: bad arguments for '{name}': {exc}"
            )
        except Exception:
            return ToolResult(
                ok=False,
                output=f"Error: tool '{name}' crashed:\n{traceback.format_exc()}",
            )
        result.output = self._truncate(self._redact(result.output))
        return result
