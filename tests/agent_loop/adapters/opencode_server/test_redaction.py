"""Tests for secret redaction in adapter evidence and errors."""

from __future__ import annotations

from agent_loop.adapters.opencode_server.redaction import redact_secret
from agent_loop.adapters.opencode_server.redaction import redact_url_credentials


def test_configured_secret_is_removed_everywhere() -> None:
    """A configured password is removed from arbitrary text."""
    assert redact_secret("before hunter2 after", "hunter2") == "before <redacted> after"


def test_password_and_basic_forms_are_redacted_without_configuration() -> None:
    """Common secret shapes are redacted defensively."""
    text = "server password abc Authorization: Basic eHl6 bearer tokenvalue123"
    redacted = redact_secret(text)
    assert "abc" not in redacted
    assert "eHl6" not in redacted
    assert "tokenvalue123" not in redacted


def test_empty_or_none_input_is_safe() -> None:
    """Redaction handles absent values without raising."""
    assert redact_secret(None) == ""


def test_url_credentials_are_removed() -> None:
    """Embedded URL credentials are never shown."""
    assert redact_url_credentials("https://user:secret@example.invalid/api") == (
        "https://<redacted>@example.invalid/api"
    )
