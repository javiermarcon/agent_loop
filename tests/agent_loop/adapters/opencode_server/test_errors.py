"""Tests for neutral adapter errors and HTTP status mapping."""

from __future__ import annotations

import pytest

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.errors import failure_code_for_http_status


def test_error_exposes_code_and_message() -> None:
    """Errors carry a stable neutral code and an actionable message."""
    error = OpenCodeServerError(ServerFailureCode.SESSION_NOT_FOUND, "missing")
    assert error.code is ServerFailureCode.SESSION_NOT_FOUND
    assert error.message == "missing"
    assert str(error).startswith("session_not_found:")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, ServerFailureCode.INVALID_REQUEST),
        (401, ServerFailureCode.AUTHENTICATION_FAILED),
        (403, ServerFailureCode.AUTHORIZATION_FAILED),
        (404, ServerFailureCode.SESSION_NOT_FOUND),
        (500, ServerFailureCode.SERVER_UNAVAILABLE),
        (503, ServerFailureCode.SERVER_UNAVAILABLE),
    ],
)
def test_http_status_mapping(status: int, expected: ServerFailureCode) -> None:
    """HTTP statuses map to neutral failure categories."""
    assert failure_code_for_http_status(status) is expected


def test_unknown_http_status_fails_closed_as_unavailable() -> None:
    """Unrecognized statuses are not treated as success."""
    assert failure_code_for_http_status(599) is ServerFailureCode.SERVER_UNAVAILABLE
