from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolRegistry, ToolResult
from .filesystem import (
    DeleteFileTool,
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from .search import GlobFilesTool, GrepTool
from .shell import RunCommandTool


def build_default_registry(
    project_dir: str | Path,
    max_tool_output: int = 20_000,
    secret: str | None = None,
) -> ToolRegistry:
    root = Path(project_dir).resolve()
    registry = ToolRegistry(secret=secret, max_output=max_tool_output)
    registry.register(ReadFileTool(root))
    registry.register(WriteFileTool(root))
    registry.register(EditFileTool(root))
    registry.register(DeleteFileTool(root))
    registry.register(ListDirTool(root))
    registry.register(GrepTool(root))
    registry.register(GlobFilesTool(root))
    registry.register(RunCommandTool(root, max_output=max_tool_output, secret=secret))
    return registry


__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
]
