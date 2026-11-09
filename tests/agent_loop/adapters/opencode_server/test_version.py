"""Tests for version compatibility and neutral error mapping."""

from __future__ import annotations

import pytest

from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.errors import failure_code_for_http_status
from agent_loop.adapters.opencode_server.redaction import redact_secret
from agent_loop.adapters.opencode_server.redaction import redact_url_credentials
from agent_loop.adapters.opencode_server.version import VersionCompatibility
from agent_loop.adapters.opencode_server.version import VersionPolicy
from agent_loop.adapters.opencode_server.version import classify_server_version


@pytest.mark.parametrize("version", ["2.2.6", "2.3.0", "v2.2.6", "2.10.1"])
def test_supported_server_versions(version: str) -> None:
    """Verified major/minor versions are supported."""
    assert classify_server_version(version) is VersionCompatibility.SUPPORTED


@pytest.mark.parametrize("version", ["3.0.0", "1.9.9", "2.1.4"])
def test_incompatible_server_versions(version: str) -> None:
    """Other parsable versions are explicitly incompatible."""
    assert classify_server_version(version) is VersionCompatibility.INCOMPATIBLE


@pytest.mark.parametrize("version", [None, "", "banana", "2.2", "v2"])
def test_unknown_versions_never_become_supported(version: str | None) -> None:
    """Missing or unparsable versions stay unknown."""
    assert classify_server_version(version) is VersionCompatibility.UNKNOWN


def test_custom_policy_floor_is_used() -> None:
    """The policy floor is explicit rather than hardcoded at call sites."""
    policy = VersionPolicy(supported_major=2, minimum_minor=5)
    assert classify_server_version("2.4.9", policy) is VersionCompatibility.INCOMPATIBLE
    assert classify_server_version("2.5.0", policy) is VersionCompatibility.SUPPORTED


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, ServerFailureCode.INVALID_REQUEST),
        (401, ServerFailureCode.AUTHENTICATION_FAILED),
        (403, ServerFailureCode.AUTHORIZATION_FAILED),
        (404, ServerFailureCode.SESSION_NOT_FOUND),
        (409, ServerFailureCode.INVALID_REQUEST),
        (500, ServerFailureCode.SERVER_UNAVAILABLE),
        (503, ServerFailureCode.SERVER_UNAVAILABLE),
        (418, ServerFailureCode.SERVER_UNAVAILABLE),
    ],
)
def test_http_status_maps_to_neutral_code(status: int, expected: ServerFailureCode) -> None:
    """HTTP statuses map to neutral runtime failure categories."""
    assert failure_code_for_http_status(status) is expected


def test_redaction_removes_configured_and_common_secrets() -> None:
    """Secrets and Basic auth forms are never retained in evidence/errors."""
    text = "server password abc123 Authorization: Basic eHl6 token=abc123"
    redacted = redact_secret(text, "abc123")
    assert "abc123" not in redacted
    assert "eHl6" not in redacted
    assert "<redacted>" in redacted


def test_url_credentials_are_redacted() -> None:
    """Embedded URL credentials are removed from display forms."""
    assert "secret" not in redact_url_credentials("http://user:secret@127.0.0.1:4096")
