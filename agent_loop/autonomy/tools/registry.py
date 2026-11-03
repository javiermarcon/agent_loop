"""Deterministic registry of tool adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_loop.autonomy.discovery.tools import ToolKind
    from agent_loop.autonomy.tools.base import ToolAdapter


class ToolRegistry:
    """Register and retrieve tool adapters by kind."""

    def __init__(self) -> None:
        self._adapters: dict[ToolKind, ToolAdapter] = {}

    def register(self, adapter: ToolAdapter) -> None:
        """Register one adapter, rejecting duplicate ownership."""
        if adapter.kind in self._adapters:
            message = f"duplicate tool adapter for {adapter.kind.value}"
            raise ValueError(message)
        self._adapters[adapter.kind] = adapter

    def get(self, kind: ToolKind) -> ToolAdapter | None:
        """Return the adapter for a kind, if registered."""
        return self._adapters.get(kind)

    def kinds(self) -> tuple[ToolKind, ...]:
        """Return registered kinds in registration order."""
        return tuple(self._adapters)

    def adapters(self) -> tuple[ToolAdapter, ...]:
        """Return registered adapters in registration order."""
        return tuple(self._adapters.values())
