from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from . import __version__
from .agent import Agent
from .client import OpenRouterClient, OpenRouterError
from .config import DEFAULT_MAX_TURNS, DEFAULT_MODEL, ConfigError, load_config
from .tools import build_default_registry

app = typer.Typer(
    name="gem",
    help="Gem — a terminal AI coding agent powered by OpenRouter.",
    add_completion=False,
    no_args_is_help=False,
)
console = Console()

REPL_HELP = """Commands:
  /help     Show this help
  /clear    Reset the conversation
  /exit     Quit (also: quit, exit, Ctrl-D)
Anything else is sent to the agent."""


def _print_banner(project_dir, model: str, stream: bool) -> None:
    console.print(
        Panel(
            f"[bold]Gem[/] v{__version__} — AI coding agent (OpenRouter)\n"
            f"project: [cyan]{project_dir}[/]\n"
            f"model:   [cyan]{model}[/]  (streaming: {'on' if stream else 'off'})\n"
            f"type [bold]/help[/] for commands, [bold]/exit[/] to quit",
            border_style="blue",
        )
    )


def _list_models(client: OpenRouterClient) -> None:
    models = client.list_models()
    console.print(f"[bold]{len(models)} models available on OpenRouter:[/]")
    for m in models:
        mid = m.get("id", "?")
        name = m.get("name", "")
        console.print(f"  [cyan]{mid}[/]  {name}")


def _make_agent(config) -> tuple[Agent, OpenRouterClient]:
    client = OpenRouterClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        timeout=config.timeout,
    )
    registry = build_default_registry(
        config.project_dir,
        max_tool_output=config.max_tool_output,
        secret=config.api_key,
    )
    agent = Agent(client, registry, config, console=console)
    return agent, client


def _run_repl(agent: Agent) -> None:
    while True:
        try:
            line = Prompt.ask("[bold green]you[/]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[bye]")
            return
        line = line.strip()
        if not line:
            continue
        if line in ("/exit", "/quit", "exit", "quit"):
            console.print("[bye]")
            return
        if line == "/help":
            console.print(REPL_HELP)
            continue
        if line == "/clear":
            agent.reset()
            console.print("[dim]conversation cleared[/]")
            continue
        try:
            agent.run(line)
        except OpenRouterError as exc:
            console.print(f"[bold red]API error:[/] {exc}")
        except KeyboardInterrupt:
            console.print("\n[dim]interrupted[/]")


@app.command()
def main(
    project_dir: str = typer.Option(
        ".", "--project-dir", "-p", help="Path to the project folder the agent works in."
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help=f"OpenRouter model id (default: {DEFAULT_MODEL}, or OPENROUTER_MODEL)."
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", help="OpenRouter API key (default: OPENROUTER_API_KEY env / .env)."
    ),
    max_turns: int = typer.Option(
        DEFAULT_MAX_TURNS, "--max-turns", help="Max tool-calling steps per task."
    ),
    execute: Optional[str] = typer.Option(
        None, "--execute", "-e", help="Run a single task non-interactively, then exit."
    ),
    no_stream: bool = typer.Option(
        False, "--no-stream", help="Disable SSE streaming; use plain non-streaming responses."
    ),
    list_models: bool = typer.Option(
        False, "--list-models", help="List available OpenRouter models and exit."
    ),
    serve: bool = typer.Option(
        False, "--serve", help="Start the web UI (loopback only) instead of the terminal REPL."
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Host for --serve (default: loopback only)."),
    port: int = typer.Option(8000, "--port", help="Port for --serve."),
    version: bool = typer.Option(False, "--version", help="Print version and exit."),
) -> None:
    if version:
        console.print(f"gem {__version__}")
        raise typer.Exit()

    if serve:
        from .server import run_server

        console.print(
            f"[bold]Gem web UI[/] at [cyan]http://{host}:{port}[/] (loopback only, Ctrl+C to stop)"
        )
        run_server(host=host, port=port)
        return

    try:
        config = load_config(
            project_dir=project_dir,
            model=model,
            api_key=api_key,
            max_turns=max_turns,
            stream=not no_stream,
        )
    except ConfigError as exc:
        console.print(f"[bold red]Configuration error:[/] {exc}")
        raise typer.Exit(code=2)

    try:
        agent, client = _make_agent(config)
    except Exception as exc:
        console.print(f"[bold red]Setup error:[/] {exc}")
        raise typer.Exit(code=2)

    try:
        if list_models:
            _list_models(client)
            return
        if execute is not None:
            try:
                agent.run(execute)
            except OpenRouterError as exc:
                console.print(f"[bold red]API error:[/] {exc}")
                raise typer.Exit(code=1)
            return
        _print_banner(config.project_dir, config.model, config.stream)
        _run_repl(agent)
    finally:
        client.close()


if __name__ == "__main__":
    app()
