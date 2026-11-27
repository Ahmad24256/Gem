from __future__ import annotations

import pytest

from gem.tools.base import ToolError
from gem.tools.filesystem import (
    DeleteFileTool,
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
    resolve_path,
)


def test_resolve_path_blocks_escape(project, tmp_path):
    with pytest.raises(ToolError, match="outside the project directory"):
        resolve_path(project, "../outside.txt")
    with pytest.raises(ToolError, match="outside the project directory"):
        resolve_path(project, str(tmp_path.parent / "etc_passwd"))


def test_resolve_path_allows_inside(project):
    assert resolve_path(project, "src/main.py") == project / "src" / "main.py"
    assert resolve_path(project, str(project / "src" / "main.py")) == project / "src" / "main.py"


def test_read_file(project):
    result = ReadFileTool(project).run(path="src/main.py")
    assert result.ok
    assert 'print("hello")' in result.output
    assert "lines 1-5 of 5" in result.output


def test_read_file_offset_limit(project):
    result = ReadFileTool(project).run(path="src/main.py", offset=2, limit=1)
    assert result.ok
    assert "lines 2-2 of 5" in result.output
    assert '    print("hello")' in result.output
    assert "truncated: 3 more lines" in result.output


def test_read_file_missing(project):
    with pytest.raises(ToolError, match="not found"):
        ReadFileTool(project).run(path="nope.txt")


def test_read_file_directory(project):
    with pytest.raises(ToolError, match="directory"):
        ReadFileTool(project).run(path="src")


def test_write_file_creates_and_overwrites(project):
    tool = WriteFileTool(project)
    r1 = tool.run(path="src/new.py", content="x = 1\n")
    assert r1.ok and "Created" in r1.output
    assert (project / "src" / "new.py").read_text() == "x = 1\n"
    r2 = tool.run(path="src/new.py", content="x = 2\n")
    assert "Overwrote" in r2.output
    assert (project / "src" / "new.py").read_text() == "x = 2\n"


def test_write_file_creates_parents_and_blocks_escape(project):
    WriteFileTool(project).run(path="a/b/c.txt", content="deep")
    assert (project / "a" / "b" / "c.txt").read_text() == "deep"
    with pytest.raises(ToolError, match="outside the project directory"):
        WriteFileTool(project).run(path="../evil.txt", content="nope")


def test_edit_file_basic(project):
    tool = EditFileTool(project)
    result = tool.run(
        path="src/util.py", old_string="return 42", new_string="return 43"
    )
    assert result.ok
    assert "Replaced 1 occurrence(s)" in result.output
    assert "-    return 42" in result.output
    assert "+    return 43" in result.output
    assert (project / "src" / "util.py").read_text() == "def helper():\n    return 43\n"


def test_edit_file_not_found(project):
    with pytest.raises(ToolError, match="not found"):
        EditFileTool(project).run(path="src/util.py", old_string="nope", new_string="x")


def test_edit_file_multiple_matches_requires_replace_all(project):
    (project / "multi.txt").write_text("ab ab ab\n")
    tool = EditFileTool(project)
    with pytest.raises(ToolError, match="3 times"):
        tool.run(path="multi.txt", old_string="ab", new_string="x")
    result = tool.run(path="multi.txt", old_string="ab", new_string="x", replace_all=True)
    assert result.ok
    assert (project / "multi.txt").read_text() == "x x x\n"


def test_delete_file(project):
    (project / "tmp.txt").write_text("bye")
    result = DeleteFileTool(project).run(path="tmp.txt")
    assert result.ok and "Deleted" in result.output
    assert not (project / "tmp.txt").exists()


def test_delete_file_guards(project):
    tool = DeleteFileTool(project)
    with pytest.raises(ToolError, match="project directory itself"):
        tool.run(path=".")
    with pytest.raises(ToolError, match="directory"):
        tool.run(path="src")
    with pytest.raises(ToolError, match="not found"):
        tool.run(path="missing.txt")


def test_list_dir_flat(project):
    result = ListDirTool(project).run(path=".")
    assert result.ok
    assert "src/" in result.output
    assert "README.md" in result.output


def test_list_dir_recursive_skips_git(project):
    result = ListDirTool(project).run(path=".", recursive=True)
    assert result.ok
    assert "src/main.py" in result.output
    assert ".git/config" not in result.output


def test_list_dir_missing(project):
    with pytest.raises(ToolError, match="not found"):
        ListDirTool(project).run(path="nope")
