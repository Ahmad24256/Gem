from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "anthropic/claude-3.5-sonnet"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MAX_TURNS = 25


class ConfigError(Exception):
    pass


@dataclass
class Config:
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    project_dir: Path = Path(".")
    max_turns: int = DEFAULT_MAX_TURNS
    stream: bool = True
    timeout: float = 180.0
    max_tool_output: int = 20_000


def load_config(
    project_dir: str | Path = ".",
    model: str | None = None,
    api_key: str | None = None,
    max_turns: int | None = None,
    stream: bool = True,
    base_url: str | None = None,
    load_env_files: bool = True,
) -> Config:
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ConfigError(f"Project directory does not exist or is not a directory: {root}")

    if load_env_files:
        for env_path in (Path.cwd() / ".env", root / ".env"):
            if env_path.is_file():
                load_dotenv(env_path, override=False)

    key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise ConfigError(
            "No OpenRouter API key found. Set OPENROUTER_API_KEY in the environment, "
            "add it to a .env file, or pass --api-key."
        )

    resolved_model = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL
    resolved_base_url = (
        base_url or os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL
    ).rstrip("/")
    resolved_max_turns = max_turns if max_turns is not None else DEFAULT_MAX_TURNS
    if resolved_max_turns < 1:
        raise ConfigError(f"--max-turns must be >= 1, got {resolved_max_turns}")

    return Config(
        api_key=key,
        model=resolved_model,
        base_url=resolved_base_url,
        project_dir=root,
        max_turns=resolved_max_turns,
        stream=stream,
    )
