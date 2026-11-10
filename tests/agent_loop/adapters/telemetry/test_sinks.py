"""S23 built-in telemetry sink tests."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

import pytest

from agent_loop.adapters.telemetry.sinks import InMemoryTelemetrySink
from agent_loop.adapters.telemetry.sinks import NullTelemetrySink
from agent_loop.ports.telemetry import TelemetryEvent
from agent_loop.ports.telemetry import TelemetryEventName


def _event(sequence: int) -> TelemetryEvent:
    return TelemetryEvent(
        event_id=f"exec-1:{sequence}",
        execution_id="exec-1",
        sequence=sequence,
        name=TelemetryEventName.WORK_ITEM_RECEIVED,
        occurred_at=datetime.now(tz=UTC),
    )


def test_in_memory_sink_preserves_order_and_returns_snapshot() -> None:
    """The in-memory sink exposes immutable snapshots in emission order."""
    sink = InMemoryTelemetrySink()
    sink.emit(_event(0))
    sink.emit(_event(1))
    events = sink.events
    assert [event.sequence for event in events] == [0, 1]
    assert isinstance(events, tuple)


def test_in_memory_sink_enforces_its_own_limit() -> None:
    """A sink limit independently bounds retained events."""
    sink = InMemoryTelemetrySink(max_events=1)
    sink.emit(_event(0))
    sink.emit(_event(1))
    assert [event.sequence for event in sink.events] == [0]
    with pytest.raises(ValueError, match="max_events"):
        InMemoryTelemetrySink(max_events=-1)


def test_null_sink_discards_events_without_side_effects() -> None:
    """The disabled sink accepts events and keeps no state."""
    NullTelemetrySink().emit(_event(0))
