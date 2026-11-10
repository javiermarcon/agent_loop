"""Built-in telemetry sinks for Agent Loop (S23).

These sinks are local and dependency-free. They never contact an external
observability service, and they do not persist events to disk. Composition roots
may inject any ``TelemetrySink``; ``NullTelemetrySink`` disables observability
and ``InMemoryTelemetrySink`` exposes bounded events for tests and embedded use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_loop.ports.telemetry import TelemetryEvent

_DEFAULT_MAX_EVENTS = 10_000


class NullTelemetrySink:
    """Disabled telemetry sink that accepts and discards every event."""

    def emit(self, event: TelemetryEvent) -> None:
        """Discard ``event`` without observing it."""
        del event


class InMemoryTelemetrySink:
    """Bounded in-memory event sink for tests and embedded callers."""

    def __init__(self, max_events: int = _DEFAULT_MAX_EVENTS) -> None:
        if max_events < 0:
            message = "InMemoryTelemetrySink.max_events must be >= 0"
            raise ValueError(message)
        self._max_events = max_events
        self._events: list[TelemetryEvent] = []

    @property
    def events(self) -> tuple[TelemetryEvent, ...]:
        """Return a snapshot of the events accepted so far."""
        return tuple(self._events)

    def emit(self, event: TelemetryEvent) -> None:
        """Append ``event`` unless this sink reached its own event limit."""
        if len(self._events) >= self._max_events:
            return
        self._events.append(event)
