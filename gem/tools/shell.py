from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .base import Tool, ToolError, ToolResult
from .filesystem import resolve_path

BLOCKED_COMMANDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r":\s*\(\s*\)\s*{\s*:\s*\|\s*:\s*&\s*}\s*;"), "fork bomb"),
    (re.compile(r"\bmkfs(\.\w+)?\b", re.I), "disk formatting"),
    (re.compile(r"\bdd\b[^|;&]*\bof\s*=\s*/dev/", re.I), "raw write to a block device"),
    (re.compile(r"(?<!\w)>+\s*/dev/(?:sd|hd|vd|xvd|nvme|mmcblk|disk)", re.I), "write to a block device"),
    (re.compile(r"\brm\b[^|;&]*-[a-zA-Z]*[rf][a-zA-Z]*[^|;&]*(?:^|\s)(/|~|\$HOME|/\*|\*|\.|\.\.)(\s|$)", re.I), "recursive/forced delete of a critical path"),
    (re.compile(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+--no-preserve-root\s+/", re.I), "recursive delete of /"),
    (re.compile(r"\b(chmod|chown)\s+[^|;&]*-R[^|;&]*\s+/", re.I), "recursive permission change on /"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff|init\s+[06])\b", re.I), "system power command"),
    (re.compile(r"\b(sudo|doas|su\b|pkexec)\b", re.I), "privilege escalation"),
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b", re.I), "piping remote content into a shell"),
    (re.compile(r"\b(apt|apt-get|yum|dnf|pacman|brew)\s+(install|remove|purge)\b.*\|\s*(ba|z)?sh", re.I), "piping package commands into a shell"),
    (re.compile(r"\bkill\s+(-9\s+)?-1\b", re.I), "kill all processes"),
]

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "Run a shell command inside the project directory and return its combined "
        "stdout/stderr and exit code. Dangerous commands (rm -rf /, mkfs, dd to devices, "
        "sudo, piping downloads to sh, ...) are refused. Keep commands read-only when possible."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute."},
            "workdir": {"type": "string", "description": "Working directory, relative to the project directory. Default '.'.", "default": "."},
            "timeout": {"type": "integer", "description": f"Timeout in seconds (max {MAX_TIMEOUT}). Default {DEFAULT_TIMEOUT}.", "default": DEFAULT_TIMEOUT},
        },
        "required": ["command"],
    }

    def __init__(self, root: Path, max_output: int = 20_000, secret: str | None = None):
        self.root = root
        self.max_output = max_output
        self.secret = secret

    def _check_blocked(self, command: str) -> str | None:
        for pattern, reason in BLOCKED_COMMANDS:
            if pattern.search(command):
                return reason
        return None

    def _redact(self, text: str) -> str:
        if self.secret and self.secret in text:
            text = text.replace(self.secret, "***REDACTED***")
        return text

    def run(self, command: str, workdir: str = ".", timeout: int = DEFAULT_TIMEOUT, **_: Any) -> ToolResult:
        if not command or not command.strip():
            raise ToolError("command must not be empty")
        reason = self._check_blocked(command)
        if reason:
            return ToolResult(
                ok=False,
                output=f"Refused to run command ({reason}): {command}",
            )

        cwd = resolve_path(self.root, workdir)
        if not cwd.is_dir():
            raise ToolError(f"workdir is not a directory: {workdir}")
        timeout = max(1, min(int(timeout), MAX_TIMEOUT))

        env = os.environ.copy()
        env["GEM_PROJECT_DIR"] = str(self.root)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=timeout,
            )
            output = proc.stdout or ""
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", errors="replace")
            output = partial + f"\n[timed out after {timeout}s]"
            exit_code = -1

        if len(output) > self.max_output:
            output = output[: self.max_output] + f"\n... [output truncated, {len(output) - self.max_output} more chars]"
        header = f"$ {command}\n(cwd: {cwd.relative_to(self.root) if cwd != self.root else '.'})"
        if exit_code != 0:
            header += f"\nExit code: {exit_code}"
        return ToolResult(ok=True, output=self._redact(f"{header}\n{output}"))
