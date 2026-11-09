"""Tests for the typed HTTP client surface."""

from __future__ import annotations

import pytest

from agent_loop.adapters.opencode_server.client import OpenCodeServerClient
from agent_loop.adapters.opencode_server.client import PermissionRule
from agent_loop.adapters.opencode_server.transport import SseStream

_TEST_CREDENTIAL = "test-credential-value"


def test_permission_rule_serializes_to_documented_shape() -> None:
    """Permission rules use action/resource/effect."""
    rule = PermissionRule(action="edit", resource="*", effect="deny")
    assert rule.as_payload() == {"action": "edit", "resource": "*", "effect": "deny"}


def test_client_exposes_base_url_and_credential_state() -> None:
    """The client reports configuration without exposing the password."""
    anonymous = OpenCodeServerClient("http://127.0.0.1:4096")
    authenticated = OpenCodeServerClient("http://127.0.0.1:4096", password=_TEST_CREDENTIAL)
    assert anonymous.base_url == "http://127.0.0.1:4096"
    assert anonymous.credentials_configured is False
    assert authenticated.credentials_configured is True
    assert _TEST_CREDENTIAL not in repr(authenticated)


def test_transport_stream_exposes_protocol_operations() -> None:
    """The transport stream provides the EventStream operations used by S22."""
    assert callable(SseStream.open)
    assert callable(SseStream.iter_events)
    assert callable(SseStream.shutdown)
    assert callable(SseStream.close)


def test_client_rejects_invalid_base_url() -> None:
    """Invalid base URLs fail before any network call."""
    with pytest.raises(ValueError, match="absolute http"):
        OpenCodeServerClient("not-a-url")
