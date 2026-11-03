"""Argument-safety tests for changed file names passed to external tools."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agent_loop.autonomy.tools.adapters import PreCommitAdapter
from agent_loop.autonomy.tools.adapters import RuffAdapter
from agent_loop.autonomy.tools.base import ToolStatus
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_context
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    from pathlib import Path


def _context(tmp_path: Path, names: tuple[str, ...]):
    root = make_project(tmp_path / "repo")
    for name in names:
        write(root / name, "value = 1\n")
    return make_context(root, changed_files=names)


def test_option_like_filename_is_not_parsed_as_an_option(tmp_path: Path) -> None:
    """A changed file called --config=evil.py remains a positional path."""
    name = "--config=evil.py"
    context = _context(tmp_path, (name,))
    runner = RecordingRunner()
    result = RuffAdapter().run(context, runner)
    command = runner.calls[0][0]
    assert result.status is ToolStatus.PASS
    assert command[-1] == f"./{name}"
    assert name not in command


def test_option_like_filename_is_safe_for_pre_commit(tmp_path: Path) -> None:
    """Pre-commit receives the same neutralized path after --files."""
    name = "--all-files.py"
    context = _context(tmp_path, (name,))
    runner = RecordingRunner()
    result = PreCommitAdapter().run(context, runner)
    assert result.status is ToolStatus.PASS
    assert runner.calls[0][0][-3:] == ("run", "--files", f"./{name}")


def test_spaces_unicode_and_special_characters_are_preserved(tmp_path: Path) -> None:
    """Legitimate names with spaces and non-ASCII characters are not dropped."""
    names = ("with space.py", "café.py", "quote ' and \".py")
    context = _context(tmp_path, names)
    runner = RecordingRunner()
    RuffAdapter().run(context, runner)
    assert runner.calls[0][0][-3:] == tuple(sorted(names))


def test_absolute_workspace_path_is_normalized(tmp_path: Path) -> None:
    """An absolute path inside the workspace becomes a relative argv entry."""
    root = make_project(tmp_path / "repo")
    target = write(root / "lib" / "module.py", "value = 1\n")
    context = make_context(root, changed_files=(str(target),))
    runner = RecordingRunner()
    RuffAdapter().run(context, runner)
    assert runner.calls[0][0][-1] == "lib/module.py"


def test_traversal_and_external_symlink_are_skipped(tmp_path: Path) -> None:
    """Paths that escape the workspace are never turned into argv entries."""
    root = make_project(tmp_path / "repo")
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    (root / "link.py").symlink_to(outside)
    context = make_context(root, changed_files=("../outside.py", "link.py"))
    runner = RecordingRunner()
    result = RuffAdapter().run(context, runner)
    assert result.status is ToolStatus.SKIPPED
    assert runner.calls == []


def test_nul_path_is_rejected_without_crashing(tmp_path: Path) -> None:
    """A NUL-containing path is invalid input, not an uncaught exception."""
    context = _context(tmp_path, ("valid.py",))
    context = replace(context, changed_files=("bad\x00name.py",))
    runner = RecordingRunner()
    result = RuffAdapter().run(context, runner)
    assert result.status is ToolStatus.SKIPPED
    assert runner.calls == []
