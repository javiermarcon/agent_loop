"""Tool registry ownership tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.tools.base import ToolContext
from agent_loop.autonomy.tools.base import ToolResult
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from agent_loop.autonomy.tools.command import CommandRunner


class _Adapter:
    kind: ToolKind

    def __init__(self, kind: ToolKind) -> None:
        self.kind = kind

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        return ToolResult(tool=self.kind, status=ToolStatus.PASS, message="ok")


def test_registry_preserves_registration_order() -> None:
    """Validation order is explicit and stable."""
    registry = ToolRegistry()
    registry.register(_Adapter(ToolKind.RUFF))
    registry.register(_Adapter(ToolKind.PYTEST))
    assert registry.kinds() == (ToolKind.RUFF, ToolKind.PYTEST)
    assert [adapter.kind for adapter in registry.adapters()] == [ToolKind.RUFF, ToolKind.PYTEST]


def test_registry_rejects_duplicate_kind() -> None:
    """One tool kind cannot have two owners."""
    registry = ToolRegistry()
    registry.register(_Adapter(ToolKind.RUFF))
    with pytest.raises(ValueError, match="duplicate tool adapter"):
        registry.register(_Adapter(ToolKind.RUFF))


def test_registry_returns_none_for_unregistered_kind() -> None:
    """Missing adapters are explicit."""
    assert ToolRegistry().get(ToolKind.MYPY) is None
