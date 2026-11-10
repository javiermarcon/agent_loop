"""S23 telemetry recorder, redaction, and usage aggregation tests."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

import pytest

from agent_loop.domain.faults import ExecutionFailure
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import ExecutionUsage
from agent_loop.ports.agent_runtime import RuntimeIdentity
from agent_loop.ports.telemetry import TelemetryCostSource
from agent_loop.ports.telemetry import TelemetryEvent
from agent_loop.ports.telemetry import TelemetryEventName
from agent_loop.ports.telemetry import TelemetryProvenance
from agent_loop.ports.telemetry import TelemetryUsage
from agent_loop.telemetry.recorder import TelemetryFacts
from agent_loop.telemetry.recorder import TelemetryLimits
from agent_loop.telemetry.recorder import TelemetryRecorder
from agent_loop.telemetry.recorder import aggregate_usage
from agent_loop.telemetry.recorder import bounded_text
from agent_loop.telemetry.recorder import redact_sensitive
from agent_loop.telemetry.recorder import usage_from_result

_CLOCK_VALUE = datetime.now(tz=UTC)
_ATTRIBUTE_LIMIT = 40
_ATTRIBUTE_COUNT_LIMIT = 32


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)


class _FailingSink:
    def emit(self, event: TelemetryEvent) -> None:
        del event
        message = "sink is down"
        raise RuntimeError(message)


def _result(
    *,
    usage: ExecutionUsage | None = None,
    metadata: tuple[tuple[str, str], ...] = (),
) -> AgentExecutionResult:
    return AgentExecutionResult(
        status=AgentExecutionStatus.SUCCEEDED,
        runtime_identity=RuntimeIdentity("runtime-a"),
        usage=usage,
        harness_metadata=metadata,
    )


def test_recorder_builds_correlated_ordered_events() -> None:
    """Events share the execution id and receive deterministic sequence ids."""
    sink = _RecordingSink()
    recorder = TelemetryRecorder(
        sink,
        execution_id="exec-1",
        target_repository="/repo",
        clock=lambda: _CLOCK_VALUE,
        limits=TelemetryLimits(max_events=2),
    )
    first = recorder.record(TelemetryEventName.WORK_ITEM_RECEIVED, TelemetryFacts(work_item_id="wi-1"))
    second = recorder.record(TelemetryEventName.WORK_ITEM_CLOSED, TelemetryFacts(status="succeeded"))
    dropped = recorder.record(TelemetryEventName.RETRY_STARTED)
    assert first is not None
    assert second is not None
    assert dropped is None
    assert [event.event_id for event in sink.events] == ["exec-1:0", "exec-1:1"]
    assert [event.sequence for event in sink.events] == [0, 1]
    assert all(event.execution_id == "exec-1" for event in sink.events)
    assert all(event.target_repository == "/repo" for event in sink.events)
    assert all(event.occurred_at == _CLOCK_VALUE for event in sink.events)
    assert sink.events[0].name is TelemetryEventName.WORK_ITEM_RECEIVED
    assert sink.events[0].work_item_id == "wi-1"


def test_recorder_can_be_disabled_with_zero_budget() -> None:
    """``max_events=0`` disables emission without changing control flow."""
    sink = _RecordingSink()
    recorder = TelemetryRecorder(sink, execution_id="exec-1", limits=TelemetryLimits(max_events=0))
    assert recorder.record(TelemetryEventName.EXECUTION_STARTED) is None
    assert sink.events == []


def test_recorder_redacts_and_bounds_attributes() -> None:
    """Attribute values are redacted, collapsed, and length-bounded."""
    sink = _RecordingSink()
    recorder = TelemetryRecorder(
        sink,
        execution_id="exec-1",
        limits=TelemetryLimits(max_attribute_chars=_ATTRIBUTE_LIMIT),
    )
    event = recorder.record(
        TelemetryEventName.EXECUTION_FAILED,
        TelemetryFacts(
            attributes={
                "password": "password=hunter2",
                "url": "https://user:secret@example.test/path",
                "multiline": "line one\nline two",
                "long": "x" * 100,
                "": "empty key value",
            },
        ),
    )
    assert event is not None
    attributes = dict(event.attributes)
    assert "" not in attributes
    assert "hunter2" not in attributes["password"]
    assert "secret@example.test" not in attributes["url"]
    assert attributes["multiline"] == "line one line two"
    assert attributes["long"].endswith("...")
    assert len(attributes["long"]) == _ATTRIBUTE_LIMIT


def test_recorder_caps_the_attribute_count() -> None:
    """Only a bounded number of scalar attributes is retained."""
    sink = _RecordingSink()
    recorder = TelemetryRecorder(sink, execution_id="exec-1")
    event = recorder.record(
        TelemetryEventName.EXECUTION_STARTED,
        TelemetryFacts(attributes={f"key-{index}": str(index) for index in range(40)}),
    )
    assert event is not None
    assert len(event.attributes) == _ATTRIBUTE_COUNT_LIMIT


def test_recorder_keeps_only_structured_failure_fields() -> None:
    """A failure detail containing a secret never enters the event."""
    sink = _RecordingSink()
    recorder = TelemetryRecorder(sink, execution_id="exec-1")
    failure = ExecutionFailure(
        category=FailureCategory.AUTHORIZATION_FAILURE,
        execution_state=ExecutionState.NOT_STARTED,
        detail="password=hunter2",
        evidence=("evidence://secret",),
    )
    event = recorder.record(TelemetryEventName.EXECUTION_FAILED, TelemetryFacts(failure=failure))
    assert event is not None
    assert event.failure_category is FailureCategory.AUTHORIZATION_FAILURE
    assert event.execution_state is ExecutionState.NOT_STARTED
    assert "hunter2" not in repr(event)
    assert "evidence://secret" not in repr(event)


def test_recorder_swallows_sink_failures() -> None:
    """A broken sink must not break the observed operation."""
    recorder = TelemetryRecorder(_FailingSink(), execution_id="exec-1")
    event = recorder.record(TelemetryEventName.WORK_ITEM_CLOSED)
    assert event is not None
    assert event.event_id == "exec-1:0"


def test_recorder_rejects_invalid_configuration() -> None:
    """Recorder and limits fail closed on invalid bounds."""
    with pytest.raises(ValueError, match="execution_id"):
        TelemetryRecorder(_RecordingSink(), execution_id=" ")
    with pytest.raises(ValueError, match="max_events"):
        TelemetryLimits(max_events=-1)
    with pytest.raises(ValueError, match="max_attribute_chars"):
        TelemetryLimits(max_attribute_chars=0)


def test_bounded_text_and_redaction_are_deterministic() -> None:
    """Standalone helpers expose the same privacy guarantees used by events."""
    assert redact_sensitive("token=abc123") == "token=<redacted>"
    assert redact_sensitive("Authorization: Bearer abcdefgh") == "Authorization: Bearer <redacted>"
    assert bounded_text("a\nb", 10) == "a b"
    assert bounded_text("abcdef", 5) == "ab..."
    assert bounded_text("abcdef", 2) == "ab"


def test_usage_from_result_never_invents_metrics() -> None:
    """Missing usage stays absent and unknown cost stays unknown."""
    assert usage_from_result(_result()) is None
    unknown = usage_from_result(_result(usage=ExecutionUsage(input_tokens=3)))
    assert unknown == TelemetryUsage(input_tokens=3)
    declared = usage_from_result(
        _result(
            usage=ExecutionUsage(input_tokens=3, output_tokens=4, cost_micros=5),
            metadata=(("reasoning_tokens", "2"),),
        ),
    )
    assert declared == TelemetryUsage(
        input_tokens=3,
        output_tokens=4,
        reasoning_tokens=2,
        cost_micros=5,
        cost_source=TelemetryCostSource.DECLARED,
    )


def test_usage_from_result_ignores_invalid_reasoning_metadata() -> None:
    """Malformed or negative reasoning metadata does not become a metric."""
    for raw in ("not-a-number", "-1"):
        usage = usage_from_result(
            _result(
                usage=ExecutionUsage(input_tokens=1),
                metadata=(("reasoning_tokens", raw),),
            ),
        )
        assert usage is not None
        assert usage.reasoning_tokens is None


def test_aggregate_usage_requires_complete_attempt_metrics() -> None:
    """Partial attempt data yields an unknown aggregate instead of a partial sum."""
    assert aggregate_usage(()) is None
    assert aggregate_usage((TelemetryUsage(input_tokens=1), None)) is None
    assert aggregate_usage(
        (
            TelemetryUsage(input_tokens=1, output_tokens=2),
            TelemetryUsage(input_tokens=3, output_tokens=None),
        ),
    ) == TelemetryUsage(input_tokens=4, output_tokens=None)


def test_aggregate_usage_sums_declared_and_calculated_costs() -> None:
    """Cost totals are only reported when every attempt declared a known cost."""
    declared = aggregate_usage(
        (
            TelemetryUsage(cost_micros=10, cost_source=TelemetryCostSource.DECLARED),
            TelemetryUsage(cost_micros=20, cost_source=TelemetryCostSource.DECLARED),
        ),
    )
    assert declared == TelemetryUsage(cost_micros=30, cost_source=TelemetryCostSource.DECLARED)
    calculated = aggregate_usage(
        (
            TelemetryUsage(cost_micros=10, cost_source=TelemetryCostSource.DECLARED),
            TelemetryUsage(cost_micros=20, cost_source=TelemetryCostSource.CALCULATED),
        ),
    )
    assert calculated == TelemetryUsage(cost_micros=30, cost_source=TelemetryCostSource.CALCULATED)
    unknown = aggregate_usage(
        (
            TelemetryUsage(cost_micros=10, cost_source=TelemetryCostSource.DECLARED),
            TelemetryUsage(),
        ),
    )
    assert unknown is not None
    assert unknown.cost_micros is None
    assert unknown.cost_known is False


def test_recorder_preserves_provenance() -> None:
    """Inferred facts are explicitly distinguishable from observations."""
    sink = _RecordingSink()
    recorder = TelemetryRecorder(sink, execution_id="exec-1")
    event = recorder.record(
        TelemetryEventName.CHANGES_DETECTED,
        TelemetryFacts(provenance=TelemetryProvenance.INFERRED),
    )
    assert event is not None
    assert event.provenance is TelemetryProvenance.INFERRED
