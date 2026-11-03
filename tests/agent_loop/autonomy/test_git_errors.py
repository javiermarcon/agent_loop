"""Fail-closed Git discovery and guard tests."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.discovery.git import discover_git
from agent_loop.autonomy.discovery.git_attribution import GitAudit
from agent_loop.autonomy.discovery.git_attribution import GitBaseline
from agent_loop.autonomy.discovery.git_attribution import GitState
from agent_loop.autonomy.discovery.git_attribution import audit_git_changes
from agent_loop.autonomy.execution.loop import AutonomyLoop
from agent_loop.autonomy.execution.loop import AutonomyRequest
from agent_loop.autonomy.execution.loop import _apply_git_audit_guard
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.tools.base import ToolStatus
from tests.agent_loop.autonomy.support import FakeProbe
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_project

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from agent_loop.autonomy.execution.agent import AgentRunResult
    from agent_loop.autonomy.tools.base import ToolContext


class _UnusedAgent:
    def invoke(self, prompt: str, context: ToolContext) -> AgentRunResult:
        del prompt, context
        message = "agent must not run in validate-only mode"
        raise AssertionError(message)


def _workspace(tmp_path: Path) -> TargetWorkspace:
    return TargetWorkspace.open(make_project(tmp_path / "repo"))


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(("git",), returncode, stdout, stderr)


def _patch_git(monkeypatch: pytest.MonkeyPatch, *, status_result) -> None:
    def _git(root: Path, *args: str):
        del root
        if args[:1] == ("rev-parse",):
            return _completed(0, "true\n" if args[1] == "--is-inside-work-tree" else "abc123\n")
        if args[:1] == ("status",):
            return status_result
        return _completed(0, "")

    monkeypatch.setattr("agent_loop.autonomy.discovery.git._git", _git)


def test_git_status_nonzero_exit_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed status command cannot produce a clean state."""
    _patch_git(monkeypatch, status_result=_completed(1, "", "fatal: cannot read index"))
    state = discover_git(_workspace(tmp_path))
    assert state.is_repository is True
    assert state.error is not None
    assert "failed" in state.error
    assert state.changed_files == ()


def test_git_status_timeout_or_unexecutable_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing subprocess result is an indeterminate audit, not clean."""
    _patch_git(monkeypatch, status_result=None)
    state = discover_git(_workspace(tmp_path))
    assert state.error is not None
    assert "could not be executed" in state.error


def test_git_status_invalid_output_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed porcelain output fails closed instead of parsing to empty."""
    _patch_git(monkeypatch, status_result=_completed(0, "ZZ broken-record\0"))
    state = discover_git(_workspace(tmp_path))
    assert state.error is not None
    assert "malformed" in state.error or "unknown status" in state.error


def test_git_status_parse_error_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Truncated rename records are rejected as an audit error."""
    _patch_git(monkeypatch, status_result=_completed(0, "R  new.py\0"))
    state = discover_git(_workspace(tmp_path))
    assert state.error is not None
    assert "rename" in state.error


def test_git_audit_surfaces_state_errors(tmp_path: Path) -> None:
    """State errors become audit warnings and cannot be silently dropped."""
    workspace = _workspace(tmp_path)
    baseline = GitBaseline(GitState(is_repository=True, error="baseline unavailable"), ())
    final = GitState(is_repository=True, error="final unavailable")
    audit = audit_git_changes(workspace, baseline, final)
    assert any("baseline Git state error" in warning for warning in audit.warnings)
    assert any("final Git state error" in warning for warning in audit.warnings)


def test_guard_never_turns_an_error_into_pass() -> None:
    """The orchestration guard downgrades PASS when Git could not be audited."""
    audit = GitAudit(
        baseline=GitBaseline(GitState(is_repository=True, error="boom"), ()),
        final_state=GitState(is_repository=True, error="boom"),
        changes=(),
        unexpected_paths=(),
    )
    status, message = _apply_git_audit_guard(ToolStatus.PASS, audit)
    assert status is ToolStatus.UNKNOWN
    assert message is not None
    assert "Git audit failed" in message


def test_validate_only_with_git_error_is_not_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Git audit failure prevents validate-only from reporting success."""
    root = make_project(tmp_path / "repo")
    workspace = TargetWorkspace.open(root)
    request = AutonomyRequest(
        workspace=workspace,
        requirement="validate",
        config=AutonomyConfig(),
        validate_only=True,
    )
    monkeypatch.setattr(
        "agent_loop.autonomy.execution.loop.discover_git",
        lambda _workspace: GitState(is_repository=True, error="status failed", branch="main", head="abc"),
    )
    loop = AutonomyLoop(
        request.config,
        command_runner=RecordingRunner(),
        agent_invoker=_UnusedAgent(),
        probe=FakeProbe({}),
    )
    result = loop.run(request)
    assert result.validation_status is ToolStatus.UNKNOWN
    assert result.status is ToolStatus.UNKNOWN
    assert "Git audit failed" in result.message
