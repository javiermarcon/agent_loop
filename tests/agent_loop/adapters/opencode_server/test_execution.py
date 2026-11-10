"""Tests for workspace validation and neutral failure result construction."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.execution import ExecutionPolicy
from agent_loop.adapters.opencode_server.execution import WaitOutcome
from agent_loop.adapters.opencode_server.execution import _failure_for
from agent_loop.adapters.opencode_server.execution import _notify_started
from agent_loop.adapters.opencode_server.execution import failure_result
from agent_loop.adapters.opencode_server.execution import validate_workspace
from agent_loop.adapters.opencode_server.models import SessionInfo
from agent_loop.adapters.opencode_server.models import TokenUsage
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import RuntimeIdentity

if TYPE_CHECKING:
    from pathlib import Path


def test_validate_workspace_resolves_existing_directory(tmp_path: Path) -> None:
    """An existing absolute directory is accepted."""
    assert validate_workspace(str(tmp_path)) == tmp_path.resolve()


def test_validate_workspace_rejects_relative_path() -> None:
    """Relative workspaces are ambiguous and fail closed."""
    with pytest.raises(OpenCodeServerError) as captured:
        validate_workspace("relative/path")
    assert captured.value.code is ServerFailureCode.WORKSPACE_INVALID


def test_validate_workspace_rejects_missing_directory(tmp_path: Path) -> None:
    """Missing workspaces are never handed to the server."""
    with pytest.raises(OpenCodeServerError) as captured:
        validate_workspace(str(tmp_path / "missing"))
    assert captured.value.code is ServerFailureCode.WORKSPACE_INVALID


def test_failure_result_preserves_session_and_redacts_message() -> None:
    """Failure results keep diagnostic identity but never secret-shaped text."""
    result = failure_result(
        RuntimeIdentity(runtime_id="opencode-server"),
        error=OpenCodeServerError(
            ServerFailureCode.EXECUTION_TIMEOUT,
            "server password super-secret timed out",
        ),
        session_ref="ses_1",
        server_version="2.2.6",
    )
    assert result.status.value == "failed"
    assert result.session_ref == "ses_1"
    assert result.evidence_refs == ("opencode-server:session:ses_1",)
    assert ("server_version", "2.2.6") in result.harness_metadata
    rendered = " ".join(result.observed_failures)
    assert "super-secret" not in rendered


def test_execution_policy_defaults_are_explicit() -> None:
    """Cancellation and observers default to absent."""
    policy = ExecutionPolicy(timeout_seconds=5.0)
    assert policy.cancel_event is None
    assert policy.on_session_started is None


def test_cancel_event_is_a_real_threading_event() -> None:
    """The policy accepts a standard cancellation signal."""
    event = threading.Event()
    event.set()
    policy = ExecutionPolicy(timeout_seconds=1.0, cancel_event=event)
    assert policy.cancel_event is not None
    assert policy.cancel_event.is_set()


def test_failure_for_session_failed_without_event() -> None:
    """A failed session outcome maps to a neutral completed execution failure."""
    session = SessionInfo("ses_1", "/workspace", None, "failed", TokenUsage(), "t")
    failure = _failure_for(
        AgentExecutionStatus.FAILED,
        WaitOutcome(),
        session,
        ("evidence://session",),
    )
    assert failure is not None
    assert failure.category is FailureCategory.EXECUTION_FAILURE
    assert failure.execution_state is ExecutionState.COMPLETED
    assert failure.evidence == ("evidence://session",)


def test_failure_for_unclassified_failed_status_is_invalid_result() -> None:
    """A failed status without event or session proof stays invalid/unknown."""
    session = SessionInfo("ses_1", "/workspace", None, None, TokenUsage(), "t")
    failure = _failure_for(
        AgentExecutionStatus.FAILED,
        WaitOutcome(),
        session,
        ("evidence://session",),
    )
    assert failure is not None
    assert failure.category is FailureCategory.INVALID_RESULT
    assert failure.execution_state is ExecutionState.UNKNOWN


def test_notify_started_invokes_observer_only_when_configured() -> None:
    """Session observers are optional and receive the real session id."""
    seen: list[str] = []
    _notify_started(seen.append, "ses_1")
    _notify_started(None, "ses_1")
    assert seen == ["ses_1"]
