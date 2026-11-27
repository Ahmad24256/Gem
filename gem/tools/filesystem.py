from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from .base import Tool, ToolError, ToolResult

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    "coverage",
}

MAX_READ_BYTES = 2_000_000
DEFAULT_READ_LIMIT = 2_000


def resolve_path(root: Path, raw: str) -> Path:
    if not raw or not str(raw).strip():
        raise ToolError("path must not be empty")
    candidate = Path(str(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ToolError(
            f"path '{raw}' resolves to '{resolved}', which is outside the project directory '{root}'"
        )
    return resolved


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file inside the project directory. Returns the file content, "
        "optionally a window of lines (1-based offset/limit). Large files are truncated."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file, relative to the project directory (or absolute, if inside it)."},
            "offset": {"type": "integer", "description": "1-based line number to start reading from. Default 1.", "default": 1},
            "limit": {"type": "integer", "description": "Max number of lines to return. Default 2000; 0 means all.", "default": 2000},
        },
        "required": ["path"],
    }

    def __init__(self, root: Path, max_bytes: int = MAX_READ_BYTES):
        self.root = root
        self.max_bytes = max_bytes

    def run(self, path: str, offset: int = 1, limit: int = DEFAULT_READ_LIMIT, **_: Any) -> ToolResult:
        target = resolve_path(self.root, path)
        if not target.exists():
            raise ToolError(f"file not found: {path}")
        if target.is_dir():
            raise ToolError(f"'{path}' is a directory; use list_dir instead")
        if target.stat().st_size > self.max_bytes:
            raise ToolError(
                f"file too large ({target.stat().st_size} bytes > {self.max_bytes}); use grep to search it"
            )
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        start = max(1, int(offset)) - 1
        if start >= total and total > 0:
            return ToolResult(
                ok=True,
                output=f"{_rel(self.root, target)}: offset {offset} is beyond end of file ({total} lines)",
            )
        end = total if limit <= 0 else min(total, start + int(limit))
        window = lines[start:end]
        header = f"{_rel(self.root, target)} (lines {start + 1}-{end} of {total})"
        truncated = end < total
        body = "\n".join(window)
        if truncated:
            body += f"\n... [truncated: {total - end} more lines; use offset={end + 1} to continue]"
        return ToolResult(ok=True, output=f"{header}\n{body}")


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a new file or overwrite an existing file inside the project directory. "
        "Parent directories are created automatically."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the file to write, relative to the project directory."},
            "content": {"type": "string", "description": "Full text content to write to the file."},
        },
        "required": ["path", "content"],
    }

    def __init__(self, root: Path):
        self.root = root

    def run(self, path: str, content: str, **_: Any) -> ToolResult:
        target = resolve_path(self.root, path)
        if target == self.root or target.is_dir():
            raise ToolError(f"cannot write to '{path}': it is a directory")
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        action = "Overwrote" if existed else "Created"
        return ToolResult(
            ok=True,
            output=f"{action} {_rel(self.root, target)} ({len(content.encode('utf-8'))} bytes)",
        )


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Apply an exact search/replace edit to a file inside the project directory. "
        "old_string must match exactly; if it occurs multiple times, pass replace_all=true."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the file to edit, relative to the project directory."},
            "old_string": {"type": "string", "description": "Exact text to replace."},
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence of old_string. Default false.", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def __init__(self, root: Path):
        self.root = root

    def run(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        **_: Any,
    ) -> ToolResult:
        target = resolve_path(self.root, path)
        if not target.exists():
            raise ToolError(f"file not found: {path}")
        if target.is_dir():
            raise ToolError(f"'{path}' is a directory")
        text = target.read_text(encoding="utf-8", errors="replace")
        if old_string == "":
            raise ToolError("old_string must not be empty")
        count = text.count(old_string)
        if count == 0:
            raise ToolError(f"old_string not found in {path}")
        if count > 1 and not replace_all:
            raise ToolError(
                f"old_string occurs {count} times in {path}; pass replace_all=true or make it more specific"
            )
        new_text = text.replace(old_string, new_string)
        target.write_text(new_text, encoding="utf-8")
        replaced = count if replace_all else 1
        diff = difflib.unified_diff(
            text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{_rel(self.root, target)}",
            tofile=f"b/{_rel(self.root, target)}",
        )
        diff_text = "".join(diff)
        if len(diff_text) > 4000:
            diff_text = diff_text[:4000] + "\n... [diff truncated]"
        return ToolResult(
            ok=True,
            output=f"Replaced {replaced} occurrence(s) in {_rel(self.root, target)}\n{diff_text}",
        )


class DeleteFileTool(Tool):
    name = "delete_file"
    description = "Delete a file inside the project directory. Directories are not supported."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the file to delete, relative to the project directory."},
        },
        "required": ["path"],
    }

    def __init__(self, root: Path):
        self.root = root

    def run(self, path: str, **_: Any) -> ToolResult:
        target = resolve_path(self.root, path)
        if target == self.root:
            raise ToolError("refusing to delete the project directory itself")
        if not target.exists() and not target.is_symlink():
            raise ToolError(f"file not found: {path}")
        if target.is_dir() and not target.is_symlink():
            raise ToolError(f"'{path}' is a directory; only files can be deleted")
        target.unlink()
        return ToolResult(ok=True, output=f"Deleted {_rel(self.root, target)}")


class ListDirTool(Tool):
    name = "list_dir"
    description = (
        "List entries of a directory inside the project directory. "
        "Directories are shown with a trailing '/'. Common junk directories are skipped when recursive."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list, relative to the project directory. Default '.'.", "default": "."},
            "recursive": {"type": "boolean", "description": "List recursively (tree). Default false.", "default": False},
            "max_entries": {"type": "integer", "description": "Max entries to return. Default 500.", "default": 500},
        },
        "required": [],
    }

    def __init__(self, root: Path):
        self.root = root

    def run(self, path: str = ".", recursive: bool = False, max_entries: int = 500, **_: Any) -> ToolResult:
        target = resolve_path(self.root, path)
        if not target.exists():
            raise ToolError(f"directory not found: {path}")
        if not target.is_dir():
            raise ToolError(f"'{path}' is not a directory")
        max_entries = max(1, int(max_entries))
        lines: list[str] = []

        if not recursive:
            try:
                entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except PermissionError as exc:
                raise ToolError(f"permission denied listing {path}: {exc}")
            for entry in entries[:max_entries]:
                name = entry.name + ("/" if entry.is_dir() else "")
                lines.append(name)
            if len(entries) > max_entries:
                lines.append(f"... [{len(entries) - max_entries} more entries truncated]")
        else:
            count = 0
            for dirpath, dirnames, filenames in target.walk():
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
                rel_dir = dirpath.relative_to(target)
                prefix = "" if str(rel_dir) == "." else str(rel_dir) + "/"
                for d in dirnames:
                    if count >= max_entries:
                        break
                    lines.append(prefix + d + "/")
                    count += 1
                for f in sorted(filenames):
                    if count >= max_entries:
                        break
                    lines.append(prefix + f)
                    count += 1
                if count >= max_entries:
                    lines.append(f"... [listing truncated at {max_entries} entries]")
                    break

        if not lines:
            return ToolResult(ok=True, output=f"{_rel(self.root, target)}/ (empty)")
        return ToolResult(ok=True, output=f"{_rel(self.root, target)}/\n" + "\n".join(lines))
