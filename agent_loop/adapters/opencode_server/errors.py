"""Neutral failure codes and exceptions for the OpenCode Server adapter.

These codes are adapter infrastructure vocabulary. They are surfaced through
``AgentExecutionResult.observed_failures``; retry and escalation policy belongs
to the control plane (S14), not to this package.
"""

from __future__ import annotations

from enum import StrEnum


class ServerFailureCode(StrEnum):
    """Stable, neutral categories for OpenCode Server failures."""

    CONNECTION_REFUSED = "connection_refused"
    SERVER_UNAVAILABLE = "server_unavailable"
    UNSUPPORTED_VERSION = "unsupported_version"
    AUTHENTICATION_FAILED = "authentication_failed"
    AUTHORIZATION_FAILED = "authorization_failed"
    INVALID_REQUEST = "invalid_request"
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_CLOSED = "session_closed"
    EXECUTION_TIMEOUT = "execution_timeout"
    CANCELLED = "cancelled"
    STREAM_INTERRUPTED = "stream_interrupted"
    RUNTIME_EXECUTION_FAILURE = "runtime_execution_failure"
    INVALID_RESPONSE = "invalid_response"
    INCOMPLETE_RESULT = "incomplete_result"
    WORKSPACE_INVALID = "workspace_invalid"
    UNSUPPORTED_REQUEST = "unsupported_request"
    SERVER_NOT_CONFIGURED = "server_not_configured"
    SERVER_START_FAILED = "server_start_failed"


class OpenCodeServerError(RuntimeError):
    """One adapter failure with a neutral code and a redaction-safe message."""

    def __init__(self, code: ServerFailureCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


_HTTP_STATUS_CODES: dict[int, ServerFailureCode] = {
    400: ServerFailureCode.INVALID_REQUEST,
    401: ServerFailureCode.AUTHENTICATION_FAILED,
    403: ServerFailureCode.AUTHORIZATION_FAILED,
    404: ServerFailureCode.SESSION_NOT_FOUND,
    409: ServerFailureCode.INVALID_REQUEST,
    422: ServerFailureCode.INVALID_REQUEST,
    500: ServerFailureCode.SERVER_UNAVAILABLE,
    502: ServerFailureCode.SERVER_UNAVAILABLE,
    503: ServerFailureCode.SERVER_UNAVAILABLE,
    504: ServerFailureCode.SERVER_UNAVAILABLE,
}


def failure_code_for_http_status(status: int) -> ServerFailureCode:
    """Return the neutral code for one non-success HTTP status."""
    return _HTTP_STATUS_CODES.get(status, ServerFailureCode.SERVER_UNAVAILABLE)
