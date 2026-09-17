"""Compatibility contracts for application-owned legacy modules."""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    ("legacy_name", "canonical_name"),
    [
        ("scripts.state_graph", "agent_loop.application.state_graph"),
        (
            "scripts.requirement_execution_gate_models",
            "agent_loop.application.requirement_execution_gate_models",
        ),
        (
            "scripts.requirement_stage2_exclusion_policy",
            "agent_loop.application.requirement_stage2_exclusion_policy",
        ),
    ],
)
def test_legacy_module_is_canonical_owner(
    legacy_name: str,
    canonical_name: str,
) -> None:
    """Legacy imports resolve to the canonical application module object."""
    legacy = importlib.import_module(legacy_name)
    canonical = importlib.import_module(canonical_name)

    assert legacy is canonical
