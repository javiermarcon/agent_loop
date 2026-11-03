"""Requirement provenance and CLI input-error tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import cast

import pytest

from agent_loop.autonomy.cli import EXIT_USAGE
from agent_loop.autonomy.cli import main
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    from pathlib import Path


def _repo(tmp_path: Path) -> Path:
    return make_project(tmp_path / "repo")


def _json_output(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return json.loads(capsys.readouterr().out)


def test_inline_requirement_source_is_recorded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--requirement records an inline origin in the structured report."""
    root = _repo(tmp_path)
    code = main(["--repo", str(root), "--requirement", "Do the work", "--discover-only", "--json"])
    payload = _json_output(capsys)
    assert code == 0
    source = cast("dict[str, object]", payload["requirement_source"])
    assert source["origin"] == "inline"
    assert source["path"] is None
    assert source["label"] == "inline --requirement"


def test_absolute_requirement_file_is_traceable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--requirement-file preserves the user-provided path and its absolute location."""
    root = _repo(tmp_path)
    requirement = write(tmp_path / "requirements" / "feature.md", "Implement feature A\n")
    code = main(["--repo", str(root), "--requirement-file", str(requirement), "--dry-run", "--json"])
    payload = _json_output(capsys)
    assert code == 0
    source = cast("dict[str, object]", payload["requirement_source"])
    assert source["origin"] == "file"
    assert source["path"] == str(requirement)
    assert source["absolute_path"] == str(requirement.resolve())
    assert "Implement feature A" in cast("str", payload["prompt"])


def test_relative_requirement_file_is_traceable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A relative --requirement-file keeps the relative form and resolves for traceability."""
    root = _repo(tmp_path)
    write(tmp_path / "task.md", "Relative requirement\n")
    monkeypatch.chdir(tmp_path)
    code = main(["--repo", str(root), "--requirement-file", "task.md", "--discover-only", "--json"])
    payload = _json_output(capsys)
    assert code == 0
    source = cast("dict[str, object]", payload["requirement_source"])
    assert source["path"] == "task.md"
    assert source["absolute_path"] == str((tmp_path / "task.md").resolve())


def test_markdown_reports_source_and_discovered_instructions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The Markdown report exposes requirement provenance and instruction files."""
    root = _repo(tmp_path)
    write(root / "AGENTS.md", "Repository-specific safety rule\n")
    requirement = write(tmp_path / "task.md", "Documented requirement\n")
    code = main(["--repo", str(root), "--requirement-file", str(requirement), "--dry-run"])
    output = capsys.readouterr().out
    assert code == 0
    assert "## Requirement" in output
    assert f"- Source: file: {requirement}" in output
    assert "Documented requirement" in output
    assert "## Repository instructions" in output
    assert "AGENTS.md" in output


def test_missing_requirement_file_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing requirement file produces a deterministic error, not a traceback."""
    root = _repo(tmp_path)
    code = main(["--repo", str(root), "--requirement-file", str(tmp_path / "missing.md")])
    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert "cannot read requirement file" in captured.err
    assert "Traceback" not in captured.err


def test_binary_requirement_file_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Invalid UTF-8 input is reported as a usage error."""
    root = _repo(tmp_path)
    binary = tmp_path / "binary.md"
    binary.write_bytes(b"\xff\xfe\x00\x01")
    code = main(["--repo", str(root), "--requirement-file", str(binary)])
    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert "not valid UTF-8" in captured.err
    assert "Traceback" not in captured.err


def test_empty_requirement_file_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An empty requirement file is rejected explicitly."""
    root = _repo(tmp_path)
    empty = write(tmp_path / "empty.md", "   \n")
    code = main(["--repo", str(root), "--requirement-file", str(empty)])
    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert "requirement file is empty" in captured.err


def test_unclosed_agent_command_quote_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """shlex failures become deterministic CLI errors instead of tracebacks."""
    root = _repo(tmp_path)
    code = main(["--repo", str(root), "--requirement", "Do work", "--agent-command", "'unclosed"])
    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert "invalid --agent-command quoting" in captured.err
    assert "Traceback" not in captured.err


def test_unexpected_programming_error_is_not_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected internal errors must propagate instead of being reported as input misuse."""
    root = _repo(tmp_path)

    def _explode(*args: object, **kwargs: object) -> None:
        del args, kwargs
        message = "unexpected internal failure"
        raise RuntimeError(message)

    monkeypatch.setattr("agent_loop.autonomy.cli._build_config", _explode)
    with pytest.raises(RuntimeError, match="unexpected internal failure"):
        main(["--repo", str(root), "--requirement", "Do work"])
