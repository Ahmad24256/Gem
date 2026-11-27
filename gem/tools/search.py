from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from .base import Tool, ToolError, ToolResult
from .filesystem import SKIP_DIRS

MAX_GREP_FILE_BYTES = 2_000_000


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(8192)
        return b"\0" in chunk
    except OSError:
        return True


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            files.append(Path(dirpath) / name)
    return files


class GrepTool(Tool):
    name = "grep"
    description = (
        "Search file contents in the project directory with a regular expression. "
        "Returns matches as 'path:line: text', optionally with surrounding context lines."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression to search for."},
            "path": {"type": "string", "description": "File or directory to search in, relative to the project directory. Default '.'.", "default": "."},
            "include": {"type": "string", "description": "Glob for file names to include, e.g. '*.py'. Default '*' (all files).", "default": "*"},
            "ignore_case": {"type": "boolean", "description": "Case-insensitive search. Default false.", "default": False},
            "context": {"type": "integer", "description": "Number of context lines before/after each match. Default 0.", "default": 0},
            "max_matches": {"type": "integer", "description": "Max matches to return. Default 200.", "default": 200},
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path):
        self.root = root

    def run(
        self,
        pattern: str,
        path: str = ".",
        include: str = "*",
        ignore_case: bool = False,
        context: int = 0,
        max_matches: int = 200,
        **_: Any,
    ) -> ToolResult:
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}")

        from .filesystem import resolve_path

        target = resolve_path(self.root, path)
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = _iter_files(target)
        else:
            raise ToolError(f"path not found: {path}")

        max_matches = max(1, int(max_matches))
        context = max(0, int(context))
        matches: list[str] = []
        match_count = 0
        files_with_matches = 0
        truncated = False

        for file_path in files:
            try:
                if file_path.stat().st_size > MAX_GREP_FILE_BYTES or _is_binary(file_path):
                    continue
            except OSError:
                continue
            rel = file_path.relative_to(self.root)
            if not fnmatch.fnmatch(file_path.name, include) and not fnmatch.fnmatch(str(rel), include):
                continue
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            file_hit = False
            i = 0
            while i < len(lines):
                if regex.search(lines[i]):
                    if not file_hit:
                        files_with_matches += 1
                        file_hit = True
                    if match_count >= max_matches:
                        truncated = True
                        break
                    start = max(0, i - context)
                    end = min(len(lines), i + context + 1)
                    for j in range(start, end):
                        sep = ":" if j == i else "-"
                        matches.append(f"{rel}{sep}{j + 1}{sep}{lines[j]}")
                    match_count += 1
                    i = end
                else:
                    i += 1
            if truncated:
                break

        if not matches:
            return ToolResult(ok=True, output=f"No matches for /{pattern}/ in {path}")
        header = f"{match_count} match(es) in {files_with_matches} file(s)"
        if truncated:
            header += f" (truncated at max_matches={max_matches})"
        return ToolResult(ok=True, output=header + "\n" + "\n".join(matches))


class GlobFilesTool(Tool):
    name = "glob_files"
    description = (
        "Find files/directories in the project directory by glob pattern, e.g. '*.py', "
        "'src/**/*.ts'. Patterns without '**' also match at any depth."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern relative to the project directory."},
            "path": {"type": "string", "description": "Base directory to search in, relative to the project directory. Default '.'.", "default": "."},
            "max_results": {"type": "integer", "description": "Max results to return. Default 500.", "default": 500},
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path):
        self.root = root

    def run(self, pattern: str, path: str = ".", max_results: int = 500, **_: Any) -> ToolResult:
        from .filesystem import resolve_path

        base = resolve_path(self.root, path)
        if not base.is_dir():
            raise ToolError(f"base path is not a directory: {path}")
        max_results = max(1, int(max_results))

        found: set[Path] = set()
        if "**" in pattern:
            found.update(base.glob(pattern))
        else:
            found.update(base.glob(pattern))
            found.update(base.rglob(pattern))

        def visible(p: Path) -> bool:
            try:
                rel = p.relative_to(self.root)
            except ValueError:
                return False
            return not any(part in SKIP_DIRS or (part.startswith(".") and part != pattern) for part in rel.parts[:-1]) and not (
                rel.parts[-1].startswith(".") and not pattern.startswith(".")
            )

        results = sorted(p for p in found if visible(p))
        shown = results[:max_results]
        lines = []
        for p in shown:
            rel = p.relative_to(self.root)
            lines.append(str(rel) + ("/" if p.is_dir() else ""))
        if len(results) > max_results:
            lines.append(f"... [{len(results) - max_results} more results truncated]")
        if not lines:
            return ToolResult(ok=True, output=f"No files match '{pattern}' in {path}")
        return ToolResult(
            ok=True,
            output=f"{len(results)} result(s) for '{pattern}':\n" + "\n".join(lines),
        )
