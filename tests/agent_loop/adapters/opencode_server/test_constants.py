"""Tests for shared OpenCode Server adapter constants."""

from __future__ import annotations

from agent_loop.adapters.opencode_server.constants import ADAPTER_VERSION
from agent_loop.adapters.opencode_server.constants import CAPABILITY_RUNTIME
from agent_loop.adapters.opencode_server.constants import RUNTIME_ID
from agent_loop.adapters.opencode_server.constants import TERMINAL_EVENT_TYPES


def test_runtime_identity_is_stable_and_distinct() -> None:
    """The server identity differs from the S09 CLI identity."""
    assert RUNTIME_ID == "opencode-server"
    assert RUNTIME_ID != "opencode"
    assert ADAPTER_VERSION


def test_runtime_capability_id_is_explicit() -> None:
    """The capability id names the server runtime, not the CLI."""
    assert CAPABILITY_RUNTIME == "runtime.opencode.server"


def test_terminal_event_types_are_documented() -> None:
    """Only documented terminal/permission events are subscribed."""
    assert set(TERMINAL_EVENT_TYPES) == {
        "session.execution.succeeded",
        "session.execution.failed",
        "session.execution.interrupted",
        "session.idle",
        "permission.asked",
    }
