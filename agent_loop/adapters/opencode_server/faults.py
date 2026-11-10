"""Neutral S14 failure mapping for the OpenCode Server adapter.

The adapter classifies only what it directly observed. It never decides retry
or escalation; that policy belongs to the control plane.
"""

from __future__ import annotations

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.redaction import redact_secret
from agent_loop.domain.faults import ExecutionFailure
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory

_CATEGORIES: dict[ServerFailureCode, FailureCategory] = {
    ServerFailureCode.CONNECTION_REFUSED: FailureCategory.RUNTIME_UNAVAILABLE,
    ServerFailureCode.SERVER_UNAVAILABLE: FailureCategory.RUNTIME_UNAVAILABLE,
    ServerFailureCode.SERVER_NOT_CONFIGURED: FailureCategory.RUNTIME_UNAVAILABLE,
    ServerFailureCode.SERVER_START_FAILED: FailureCategory.RUNTIME_UNAVAILABLE,
    ServerFailureCode.UNSUPPORTED_VERSION: FailureCategory.RUNTIME_UNAVAILABLE,
    ServerFailureCode.AUTHENTICATION_FAILED: FailureCategory.AUTHORIZATION_FAILURE,
    ServerFailureCode.AUTHORIZATION_FAILED: FailureCategory.AUTHORIZATION_FAILURE,
    ServerFailureCode.INVALID_REQUEST: FailureCategory.INVALID_REQUEST,
    ServerFailureCode.UNSUPPORTED_REQUEST: FailureCategory.INVALID_REQUEST,
    ServerFailureCode.WORKSPACE_INVALID: FailureCategory.INVALID_REQUEST,
    ServerFailureCode.SESSION_NOT_FOUND: FailureCategory.EXECUTION_FAILURE,
    ServerFailureCode.SESSION_CLOSED: FailureCategory.EXECUTION_FAILURE,
    ServerFailureCode.EXECUTION_TIMEOUT: FailureCategory.TIMEOUT,
    ServerFailureCode.CANCELLED: FailureCategory.CANCELLED,
    ServerFailureCode.STREAM_INTERRUPTED: FailureCategory.EXECUTION_FAILURE,
    ServerFailureCode.RUNTIME_EXECUTION_FAILURE: FailureCategory.EXECUTION_FAILURE,
    ServerFailureCode.INVALID_RESPONSE: FailureCategory.INVALID_RESULT,
    ServerFailureCode.INCOMPLETE_RESULT: FailureCategory.INVALID_RESULT,
}


def category_for(code: ServerFailureCode) -> FailureCategory:
    """Return the neutral control-plane category for one adapter code."""
    return _CATEGORIES.get(code, FailureCategory.UNKNOWN)


def execution_state_for(session_ref: str | None) -> ExecutionState:
    """Derive the observed state: a session ref proves execution started."""
    return ExecutionState.NOT_STARTED if session_ref is None else ExecutionState.STARTED


def failure_detail_for(
    error: OpenCodeServerError,
    *,
    session_ref: str | None,
    execution_state: ExecutionState | None = None,
) -> ExecutionFailure:
    """Build a redacted neutral failure without retry policy."""
    state = execution_state or execution_state_for(session_ref)
    evidence = () if session_ref is None else (f"opencode-server:session:{session_ref}",)
    return ExecutionFailure(
        category=category_for(error.code),
        execution_state=state,
        evidence=evidence,
        detail=redact_secret(error.message),
    )
