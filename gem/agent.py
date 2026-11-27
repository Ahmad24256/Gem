from __future__ import annotations

import json
from typing import Any

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

    def run(self, user_message: str) -> str:
        self.messages.append({"role": "user", "content": user_message})
        return self._loop()

    def _call_model(self, tools: list[dict[str, Any]] | None) -> ChatResult:
        if self.config.stream:
            def on_token(token: str) -> None:
                self.console.print(token, end="", highlight=False, soft_wrap=True)

            result = self.client.stream_chat(
                self.messages, tools=tools, on_token=on_token
            )
            self.console.print()
        else:
            result = self.client.chat(self.messages, tools=tools)
        return result

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
        self.console.print(Panel(Markdown(content), border_style="blue", title="[bold blue]Gem[/]"))

    def _append_assistant(self, result: ChatResult) -> None:
        self.messages.append(
            {
                "role": "assistant",
                "content": result.content,
                "tool_calls": [tc.to_message_dict() for tc in result.tool_calls],
            }
        )

    def _loop(self) -> str:
        tool_schemas = self.registry.schemas()
        for _turn in range(1, self.config.max_turns + 1):
            try:
                result = self._call_model(tool_schemas)
            except OpenRouterError:
                raise
            except httpx.HTTPError as exc:
                raise OpenRouterError(f"Network error while calling OpenRouter: {exc}") from exc

            self._append_assistant(result)

            if not result.tool_calls:
                content = result.content or "(no content returned)"
                if not self.config.stream:
                    self._render_final(content)
                return content

            for tool_call in result.tool_calls:
                self._show_tool_call(tool_call.name, tool_call.arguments)
                outcome = self.registry.dispatch(tool_call.name, tool_call.arguments)
                self._show_tool_result(tool_call.name, outcome.ok, outcome.output)
                self.messages.append(
                    outcome.to_message(tool_call.name, tool_call.id)
                )

        self.console.print(
            f"[yellow]Reached max turns ({self.config.max_turns}); requesting final answer.[/]"
        )
        self.messages.append(
            {
                "role": "user",
                "content": (
                    "You have reached the maximum number of tool steps. "
                    "Do not call any more tools; give your final answer now."
                ),
            }
        )
        result = self._call_model(None)
        self._append_assistant(result)
        content = result.content or "(no content returned)"
        if not self.config.stream:
            self._render_final(content)
        return content
