"""Autonomy error hierarchy tests."""

from __future__ import annotations

from agent_loop.autonomy.errors import AutonomyError
from agent_loop.autonomy.errors import ConfigurationError
from agent_loop.autonomy.errors import DiscoveryError
from agent_loop.autonomy.errors import SecurityError


def test_autonomy_errors_share_a_stable_base() -> None:
    """Callers can catch one base error without swallowing unrelated failures."""
    for error in (SecurityError("x"), DiscoveryError("x"), ConfigurationError("x")):
        assert isinstance(error, AutonomyError)
        assert isinstance(error, RuntimeError)
