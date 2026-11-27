from __future__ import annotations

import json
from typing import Any, Iterator

import httpx
from rich.console import Console
from rich.json import JSON
from rich.markdown import Markdown
from rich.panel import Panel

from .client import ChatResult, OpenRouterClient, OpenRouterError
from .config import Config
from .prompts import build_system_prompt
from .tools.base import ToolRegistry

DISPLAY_RESULT_LINES = 40


class Agent:
    """Tool-calling agent loop shared by the CLI and the web server.

    run_events() yields events: token / tool_call / tool_result / status /
    final / error. run() renders those events to a rich Console.
    """

    def __init__(
        self,
        client: OpenRouterClient,
        registry: ToolRegistry,
        config: Config,
        console: Console | None = None,
    ):
        self.client = client
        self.registry = registry
        self.config = config
        self.console = console or Console()
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(config.project_dir)}
        ]

    def reset(self) -> None:
        self.messages = [
            {"role": "system", "content": build_system_prompt(self.config.project_dir)}
        ]

    def _model_turn(self, tools: list[dict[str, Any]] | None) -> Iterator[dict[str, Any]]:
        if self.config.stream:
            result: ChatResult | None = None
            for kind, payload in self.client.iter_chat_stream(self.messages, tools=tools):
                if kind == "token":
                    yield {"type": "token", "text": payload}
                else:
                    result = payload
            assert result is not None
            return result
        return self.client.chat(self.messages, tools=tools)

    def _append_assistant(self, result: ChatResult) -> None:
        self.messages.append(
            {
                "role": "assistant",
                "content": result.content,
                "tool_calls": [tc.to_message_dict() for tc in result.tool_calls],
            }
        )

    def run_events(self, user_message: str) -> Iterator[dict[str, Any]]:
        self.messages.append({"role": "user", "content": user_message})
        tool_schemas = self.registry.schemas()
        try:
            for _turn in range(1, self.config.max_turns + 1):
                result = yield from self._model_turn(tool_schemas)
                self._append_assistant(result)

                if not result.tool_calls:
                    content = result.content or ""
                    yield {"type": "final", "content": content}
                    return content

                for tool_call in result.tool_calls:
                    yield {
                        "type": "tool_call",
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    }
                    outcome = self.registry.dispatch(tool_call.name, tool_call.arguments)
                    yield {
                        "type": "tool_result",
                        "name": tool_call.name,
                        "ok": outcome.ok,
                        "output": outcome.output,
                    }
                    self.messages.append(outcome.to_message(tool_call.name, tool_call.id))

            yield {
                "type": "status",
                "text": f"Reached max turns ({self.config.max_turns}); requesting final answer.",
            }
            self.messages.append(
                {
                    "role": "user",
                    "content": (
                        "You have reached the maximum number of tool steps. "
                        "Do not call any more tools; give your final answer now."
                    ),
                }
            )
            result = yield from self._model_turn(None)
            self._append_assistant(result)
            content = result.content or ""
            yield {"type": "final", "content": content}
            return content
        except OpenRouterError as exc:
            yield {"type": "error", "message": str(exc)}
            return ""
        except httpx.HTTPError as exc:
            yield {
                "type": "error",
                "message": f"Network error while calling OpenRouter: {exc}",
            }
            return ""

    def _show_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        body = JSON(json.dumps(arguments, indent=2, ensure_ascii=False))
        self.console.print(
            Panel(body, title=f"[bold cyan]tool call: {name}[/]", border_style="cyan")
        )

    def _show_tool_result(self, name: str, ok: bool, output: str) -> None:
        lines = output.splitlines()
        shown = "\n".join(lines[:DISPLAY_RESULT_LINES])
        if len(lines) > DISPLAY_RESULT_LINES:
            shown += f"\n... [{len(lines) - DISPLAY_RESULT_LINES} more lines]"
        style = "green" if ok else "red"
        title = f"[bold {style}]{name} -> {'ok' if ok else 'error'}[/]"
        self.console.print(Panel(shown, title=title, border_style=style))

    def _render_final(self, content: str) -> None:
        self.console.print()
        self.console.print(
            Panel(Markdown(content), border_style="blue", title="[bold blue]Gem[/]")
        )

    def run(self, user_message: str) -> str:
        final = ""
        error: str | None = None
        for event in self.run_events(user_message):
            kind = event["type"]
            if kind == "token":
                self.console.print(event["text"], end="", highlight=False, soft_wrap=True)
            elif kind == "tool_call":
                if self.config.stream:
                    self.console.print()
                self._show_tool_call(event["name"], event["arguments"])
            elif kind == "tool_result":
                self._show_tool_result(event["name"], event["ok"], event["output"])
            elif kind == "status":
                self.console.print(f"[yellow]{event['text']}[/]")
            elif kind == "final":
                final = event["content"]
                if self.config.stream:
                    self.console.print()
                else:
                    self._render_final(final)
            elif kind == "error":
                error = event["message"]
        if error:
            raise OpenRouterError(error)
        return final
