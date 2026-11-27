from __future__ import annotations

import pytest

from gem.tools.base import ToolError
from gem.tools.search import GlobFilesTool, GrepTool


def test_grep_finds_matches_with_line_numbers(project):
    result = GrepTool(project).run(pattern="print")
    assert result.ok
    assert "src/main.py:2:    print(\"hello\")" in result.output
    assert "1 match(es) in 1 file(s)" in result.output


def test_grep_include_filter(project):
    result = GrepTool(project).run(pattern="def", include="*.py")
    assert result.ok
    assert "src/main.py" in result.output
    assert "src/util.py" in result.output
    assert "README.md" not in result.output


def test_grep_ignore_case(project):
    result = GrepTool(project).run(pattern="some TEXT here", ignore_case=True)
    assert result.ok
    assert "README.md:3" in result.output


def test_grep_context(project):
    result = GrepTool(project).run(pattern="return 42", path="src", context=1)
    assert result.ok
    assert "src/util.py-1-def helper():" in result.output
    assert "src/util.py:2:    return 42" in result.output


def test_grep_no_match(project):
    result = GrepTool(project).run(pattern="zzz_not_present")
    assert result.ok
    assert "No matches" in result.output


def test_grep_invalid_regex(project):
    with pytest.raises(ToolError, match="invalid regular expression"):
        GrepTool(project).run(pattern="([")


def test_grep_skips_git_dir(project):
    result = GrepTool(project).run(pattern="core")
    assert result.ok
    assert "No matches" in result.output


def test_grep_skips_binary(project):
    (project / "blob.bin").write_bytes(b"\x00\x01\x02needle\x00")
    result = GrepTool(project).run(pattern="needle")
    assert "No matches" in result.output


def test_glob_py_files_recurses(project):
    result = GlobFilesTool(project).run(pattern="*.py")
    assert result.ok
    assert "src/main.py" in result.output
    assert "src/util.py" in result.output


def test_glob_explicit_rglob(project):
    result = GlobFilesTool(project).run(pattern="src/**/*.py")
    assert result.ok
    assert "src/util.py" in result.output
    assert "README.md" not in result.output


def test_glob_skips_git(project):
    result = GlobFilesTool(project).run(pattern="*")
    assert ".git/" not in result.output and ".git" not in result.output


def test_glob_no_match(project):
    result = GlobFilesTool(project).run(pattern="*.rs")
    assert result.ok
    assert "No files match" in result.output
