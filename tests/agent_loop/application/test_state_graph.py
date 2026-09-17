"""Contract tests for the canonical durable transition graph."""

from __future__ import annotations

import pytest

from agent_loop.application.state_graph import assert_unit_transition_allowed
from agent_loop.application.state_graph import is_run_transition_allowed
from agent_loop.application.state_graph import is_unit_transition_allowed


def test_unit_graph_allows_declared_progression() -> None:
    """A declared unit edge is accepted."""
    assert is_unit_transition_allowed("pending", "ready")
    assert_unit_transition_allowed("ready", "running", unit_id="unit-1")


def test_graph_rejects_undeclared_edges() -> None:
    """Unknown or backwards edges fail closed."""
    assert not is_run_transition_allowed("completed", "running")
    with pytest.raises(ValueError, match="unit-1"):
        assert_unit_transition_allowed("completed", "running", unit_id="unit-1")
