"""Neutral failure mapping tests for the OpenCode Server adapter (S14)."""

from __future__ import annotations

from typing import cast

import pytest

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.execution import failure_result
from agent_loop.adapters.opencode_server.faults import category_for
from agent_loop.adapters.opencode_server.faults import execution_state_for
from agent_loop.adapters.opencode_server.faults import failure_detail_for
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.ports.agent_runtime import RuntimeIdentity


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (ServerFailureCode.CONNECTION_REFUSED, FailureCategory.RUNTIME_UNAVAILABLE),
        (ServerFailureCode.SERVER_UNAVAILABLE, FailureCategory.RUNTIME_UNAVAILABLE),
        (ServerFailureCode.SERVER_NOT_CONFIGURED, FailureCategory.RUNTIME_UNAVAILABLE),
        (ServerFailureCode.SERVER_START_FAILED, FailureCategory.RUNTIME_UNAVAILABLE),
        (ServerFailureCode.UNSUPPORTED_VERSION, FailureCategory.RUNTIME_UNAVAILABLE),
        (ServerFailureCode.AUTHENTICATION_FAILED, FailureCategory.AUTHORIZATION_FAILURE),
        (ServerFailureCode.AUTHORIZATION_FAILED, FailureCategory.AUTHORIZATION_FAILURE),
        (ServerFailureCode.INVALID_REQUEST, FailureCategory.INVALID_REQUEST),
        (ServerFailureCode.UNSUPPORTED_REQUEST, FailureCategory.INVALID_REQUEST),
        (ServerFailureCode.WORKSPACE_INVALID, FailureCategory.INVALID_REQUEST),
        (ServerFailureCode.SESSION_NOT_FOUND, FailureCategory.EXECUTION_FAILURE),
        (ServerFailureCode.SESSION_CLOSED, FailureCategory.EXECUTION_FAILURE),
        (ServerFailureCode.EXECUTION_TIMEOUT, FailureCategory.TIMEOUT),
        (ServerFailureCode.CANCELLED, FailureCategory.CANCELLED),
        (ServerFailureCode.STREAM_INTERRUPTED, FailureCategory.EXECUTION_FAILURE),
        (ServerFailureCode.RUNTIME_EXECUTION_FAILURE, FailureCategory.EXECUTION_FAILURE),
        (ServerFailureCode.INVALID_RESPONSE, FailureCategory.INVALID_RESULT),
        (ServerFailureCode.INCOMPLETE_RESULT, FailureCategory.INVALID_RESULT),
    ],
)
def test_server_codes_map_to_neutral_categories(code: ServerFailureCode, category: FailureCategory) -> None:
    """Every adapter code has a deterministic neutral category."""
    assert category_for(code) is category


def test_unknown_adapter_code_maps_to_unknown() -> None:
    """Unexpected adapter codes are never optimistic."""
    assert category_for(cast("ServerFailureCode", "not-a-real-code")) is FailureCategory.UNKNOWN


@pytest.mark.parametrize(
    ("session_ref", "state"),
    [("ses_1", ExecutionState.STARTED), (None, ExecutionState.NOT_STARTED)],
)
def test_execution_state_is_derived_from_observed_session(
    session_ref: str | None,
    state: ExecutionState,
) -> None:
    """A session reference is the only proof that execution started."""
    assert execution_state_for(session_ref) is state


def test_failure_detail_preserves_session_evidence_and_redacts() -> None:
    """Neutral failures keep evidence without leaking secrets."""
    failure = failure_detail_for(
        OpenCodeServerError(ServerFailureCode.EXECUTION_TIMEOUT, "server password super-secret timed out"),
        session_ref="ses_1",
    )
    assert failure.category is FailureCategory.TIMEOUT
    assert failure.execution_state is ExecutionState.STARTED
    assert failure.evidence == ("opencode-server:session:ses_1",)
    assert "super-secret" not in failure.detail


def test_failure_detail_accepts_explicit_execution_state() -> None:
    """Adapters may prove a completed execution explicitly."""
    failure = failure_detail_for(
        OpenCodeServerError(ServerFailureCode.RUNTIME_EXECUTION_FAILURE, "boom"),
        session_ref="ses_1",
        execution_state=ExecutionState.COMPLETED,
    )
    assert failure.execution_state is ExecutionState.COMPLETED


def test_adapter_failure_result_exposes_neutral_failure() -> None:
    """The port result carries the same neutral failure object."""
    result = failure_result(
        RuntimeIdentity("opencode-server"),
        error=OpenCodeServerError(ServerFailureCode.SERVER_UNAVAILABLE, "down"),
        session_ref=None,
        server_version=None,
    )
    assert result.failure is not None
    assert result.failure.category is FailureCategory.RUNTIME_UNAVAILABLE
    assert result.failure.execution_state is ExecutionState.NOT_STARTED
    assert result.evidence_refs == ()
    assert result.observed_failures
