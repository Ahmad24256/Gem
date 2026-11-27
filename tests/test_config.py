from __future__ import annotations

import pytest

from gem.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ConfigError,
    load_config,
)


def test_defaults_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    cfg = load_config(tmp_path, load_env_files=False)
    assert cfg.api_key == "sk-or-test"
    assert cfg.model == DEFAULT_MODEL
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.project_dir == tmp_path.resolve()
    assert cfg.stream is True


def test_cli_overrides_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setenv("OPENROUTER_MODEL", "env-model")
    cfg = load_config(
        tmp_path, model="cli-model", api_key="sk-or-cli", max_turns=7, load_env_files=False
    )
    assert cfg.api_key == "sk-or-cli"
    assert cfg.model == "cli-model"
    assert cfg.max_turns == 7


def test_env_model_used_when_no_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o")
    cfg = load_config(tmp_path, load_env_files=False)
    assert cfg.model == "openai/gpt-4o"


def test_dotenv_file_loaded(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=sk-or-from-dotenv\nOPENROUTER_MODEL=dotenv-model\n"
    )
    cfg = load_config(tmp_path, load_env_files=True)
    assert cfg.api_key == "sk-or-from-dotenv"
    assert cfg.model == "dotenv-model"


def test_real_env_beats_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-real-env")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-dotenv\n")
    cfg = load_config(tmp_path, load_env_files=True)
    assert cfg.api_key == "sk-or-real-env"


def test_missing_key_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="API key"):
        load_config(tmp_path, load_env_files=False)


def test_missing_project_dir_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "nope", load_env_files=False)


def test_invalid_max_turns(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    with pytest.raises(ConfigError, match="max-turns"):
        load_config(tmp_path, max_turns=0, load_env_files=False)
