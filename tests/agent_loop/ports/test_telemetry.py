"""S23 telemetry port contract tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC
from datetime import datetime

import pytest

from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.ports.telemetry import TelemetryCostSource
from agent_loop.ports.telemetry import TelemetryEvent
from agent_loop.ports.telemetry import TelemetryEventName
from agent_loop.ports.telemetry import TelemetryProvenance
from agent_loop.ports.telemetry import TelemetryUsage


def _event() -> TelemetryEvent:
    return TelemetryEvent(
        event_id="exec-1:0",
        execution_id="exec-1",
        sequence=0,
        name=TelemetryEventName.WORK_ITEM_RECEIVED,
        occurred_at=datetime.now(tz=UTC),
    )


def test_usage_keeps_unknown_cost_distinct_from_zero() -> None:
    """An unknown cost has no value and is never encoded as zero."""
    unknown = TelemetryUsage(input_tokens=10)
    assert unknown.cost_known is False
    assert unknown.cost_micros is None
    assert TelemetryUsage(cost_micros=0, cost_source=TelemetryCostSource.DECLARED).cost_known is True


def test_usage_rejects_contradictory_cost_state() -> None:
    """Known/unknown cost source and value must agree."""
    with pytest.raises(ValueError, match="UNKNOWN cost source"):
        TelemetryUsage(cost_micros=1, cost_source=TelemetryCostSource.UNKNOWN)
    with pytest.raises(ValueError, match="known cost source"):
        TelemetryUsage(cost_micros=None, cost_source=TelemetryCostSource.DECLARED)


@pytest.mark.parametrize(
    ("build", "field_name"),
    [
        (lambda: TelemetryUsage(input_tokens=-1), "input_tokens"),
        (lambda: TelemetryUsage(output_tokens=-1), "output_tokens"),
        (lambda: TelemetryUsage(reasoning_tokens=-1), "reasoning_tokens"),
        (lambda: TelemetryUsage(cost_micros=-1, cost_source=TelemetryCostSource.DECLARED), "cost_micros"),
    ],
)
def test_usage_rejects_negative_metrics(build, field_name: str) -> None:
    """Token and cost metrics are non-negative when observed."""
    with pytest.raises(ValueError, match=field_name):
        build()


def test_event_accepts_structured_optional_facts() -> None:
    """The port event carries neutral correlation and failure fields."""
    event = replace(
        _event(),
        provenance=TelemetryProvenance.INFERRED,
        target_repository="/repo",
        work_item_id="wi-1",
        attempt=2,
        runtime_id="runtime-a",
        model_id="provider/model-a",
        session_ref="session-1",
        status="failed",
        duration_ms=12,
        failure_category=FailureCategory.TIMEOUT,
        execution_state=ExecutionState.STARTED,
        usage=TelemetryUsage(input_tokens=1),
        attributes=(("retry", "true"),),
    )
    assert event.name is TelemetryEventName.WORK_ITEM_RECEIVED
    assert event.execution_id == "exec-1"
    assert event.failure_category is FailureCategory.TIMEOUT
    assert event.execution_state is ExecutionState.STARTED
    assert event.usage is not None


def test_event_rejects_invalid_identity_and_negative_values() -> None:
    """Correlation identity, sequence, attempt, and duration stay valid."""
    with pytest.raises(ValueError, match="event_id"):
        replace(_event(), event_id=" ")
    with pytest.raises(ValueError, match="execution_id"):
        replace(_event(), execution_id=" ")
    with pytest.raises(ValueError, match="sequence"):
        replace(_event(), sequence=-1)
    with pytest.raises(ValueError, match="duration_ms"):
        replace(_event(), duration_ms=-1)
    with pytest.raises(ValueError, match="attempt"):
        replace(_event(), attempt=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(_event(), occurred_at=datetime.now(tz=UTC).replace(tzinfo=None))
