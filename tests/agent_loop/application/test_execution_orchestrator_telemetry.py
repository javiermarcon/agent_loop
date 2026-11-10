"""S23 telemetry integration through the S13/S14/S16 control-plane loop."""

from __future__ import annotations

from dataclasses import replace

from agent_loop.adapters.telemetry.sinks import InMemoryTelemetrySink
from agent_loop.application.execution_orchestrator import ExecutionRunOptions
from agent_loop.application.execution_orchestrator import ExecutionRunOutcome
from agent_loop.application.execution_orchestrator import ExecutionRunStatus
from agent_loop.application.execution_orchestrator import RoutingInputs
from agent_loop.application.execution_orchestrator import execute_work_item_with_recovery
from agent_loop.application.recovery_policy import RecoveryLimits
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.faults import ExecutionFailure
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import ExecutionUsage
from agent_loop.ports.agent_runtime import RuntimeIdentity
from agent_loop.ports.telemetry import TelemetryCostSource
from agent_loop.ports.telemetry import TelemetryEvent
from agent_loop.ports.telemetry import TelemetryEventName
from agent_loop.ports.telemetry import TelemetrySink
from agent_loop.ports.telemetry import TelemetryUsage
from tests.agent_loop.application.orchestrator_support import ScriptedRuntime
from tests.agent_loop.application.orchestrator_support import assessment as build_assessment
from tests.agent_loop.application.orchestrator_support import failure_result
from tests.agent_loop.application.orchestrator_support import options as run_options
from tests.agent_loop.application.orchestrator_support import profile as make_profile
from tests.agent_loop.application.orchestrator_support import routing as build_routing
from tests.agent_loop.application.orchestrator_support import success_result
from tests.agent_loop.application.orchestrator_support import work_item as build_work_item


class _FailingSink:
    def emit(self, event: TelemetryEvent) -> None:
        del event
        message = "telemetry backend unavailable"
        raise RuntimeError(message)


def _options(
    sink: TelemetrySink | None,
    *,
    execution_id: str = "exec-1",
    limits: RecoveryLimits | None = None,
) -> ExecutionRunOptions:
    return replace(
        run_options(limits=limits),
        telemetry=sink,
        execution_id=execution_id,
    )


def _run(
    options: ExecutionRunOptions,
    *runtimes: ScriptedRuntime,
    routing: RoutingInputs | None = None,
) -> ExecutionRunOutcome:
    profile = make_profile("alpha", "model-a")
    return execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=options,
        routing=routing or build_routing(("runtime-a", profile)),
        runtimes=runtimes or (ScriptedRuntime("runtime-a", (success_result("runtime-a"),)),),
    )


_SECOND_ATTEMPT = 2


def _names(events: tuple[TelemetryEvent, ...]) -> list[TelemetryEventName]:
    return [event.name for event in events]


def test_successful_run_emits_correlated_lifecycle_events() -> None:
    """The loop publishes WorkItem, routing, execution, and closure facts."""
    sink = InMemoryTelemetrySink()
    runtime = ScriptedRuntime(
        "runtime-a",
        (
            replace(
                success_result("runtime-a"),
                model_id="provider/model-a",
                session_ref="session-1",
                usage=ExecutionUsage(input_tokens=3, output_tokens=4, cost_micros=5),
            ),
        ),
    )
    outcome = _run(_options(sink), runtime)
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    events = sink.events
    assert _names(events) == [
        TelemetryEventName.WORK_ITEM_RECEIVED,
        TelemetryEventName.ROUTING_COMPLETED,
        TelemetryEventName.EXECUTION_STARTED,
        TelemetryEventName.EXECUTION_COMPLETED,
        TelemetryEventName.WORK_ITEM_CLOSED,
    ]
    assert all(event.execution_id == "exec-1" for event in events)
    assert all(event.target_repository == "workspace-fixture" for event in events)
    assert all(event.event_id == f"exec-1:{event.sequence}" for event in events)
    assert [event.sequence for event in events] == list(range(len(events)))
    routing = events[1]
    started = events[2]
    completed = events[3]
    closed = events[4]
    assert routing.runtime_id == "runtime-a"
    assert routing.model_id == "provider/model-a"
    assert routing.status == "selected"
    assert dict(routing.attributes)["eligible_runtimes"] == "runtime-a"
    assert started.attempt == 1
    assert started.runtime_id == "runtime-a"
    assert started.model_id == "provider/model-a"
    assert completed.session_ref == "session-1"
    assert completed.usage == TelemetryUsage(
        input_tokens=3,
        output_tokens=4,
        cost_micros=5,
        cost_source=TelemetryCostSource.DECLARED,
    )
    assert closed.status == "succeeded"
    assert closed.work_item_id == "wi-1"
    assert closed.usage == completed.usage
    assert dict(closed.attributes)["attempt_count"] == "1"
    assert "Implement the fixture task" not in repr(events)


def test_blocked_work_item_is_observable_without_routing_or_execution() -> None:
    """S16 readiness blocks the run and telemetry reports only observed closure."""
    sink = InMemoryTelemetrySink()
    outcome = execute_work_item_with_recovery(
        build_work_item(status=WorkItemStatus.BLOCKED),
        build_assessment(),
        options=_options(sink),
        routing=build_routing(("runtime-a", make_profile("alpha", "model-a"))),
        runtimes=(ScriptedRuntime("runtime-a", ()),),
    )
    assert outcome.status is ExecutionRunStatus.BLOCKED
    assert _names(sink.events) == [
        TelemetryEventName.WORK_ITEM_RECEIVED,
        TelemetryEventName.WORK_ITEM_CLOSED,
    ]
    assert dict(sink.events[-1].attributes)["reason"] == "work_item_not_schedulable"


def test_routing_terminal_is_observable_without_runtime_execution() -> None:
    """Empty S13 selection closes the run with an explicit no-selection event."""
    sink = InMemoryTelemetrySink()
    profile = make_profile("alpha", "model-a")
    routing = build_routing(("runtime-a", profile))
    unavailable = replace(
        routing.inventories[0],
        capabilities=tuple(
            replace(snapshot, available=CapabilityState.NO)
            if snapshot.capability_id.value == "runtime.runtime-a"
            else snapshot
            for snapshot in routing.inventories[0].capabilities
        ),
    )
    routing = replace(routing, inventories=(unavailable,))
    runtime = ScriptedRuntime("runtime-a", ())
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=_options(sink),
        routing=routing,
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.BLOCKED
    assert runtime.requests == []
    assert _names(sink.events) == [
        TelemetryEventName.WORK_ITEM_RECEIVED,
        TelemetryEventName.ROUTING_COMPLETED,
        TelemetryEventName.WORK_ITEM_CLOSED,
    ]
    assert sink.events[1].status == "no_selection"
    assert "routing_terminal" in dict(sink.events[2].attributes)["reason"]


def test_retry_and_escalation_events_preserve_attempt_and_route_changes() -> None:
    """S14 retry/escalation decisions are recorded without re-deciding them."""
    sink = InMemoryTelemetrySink()
    runtime = ScriptedRuntime(
        "runtime-a",
        (
            failure_result("runtime-a", FailureCategory.TIMEOUT, ExecutionState.NOT_STARTED),
            success_result("runtime-a"),
        ),
    )
    outcome = _run(
        _options(sink, limits=RecoveryLimits(max_attempts_per_combination=2, max_total_attempts=2)),
        runtime,
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert _names(sink.events) == [
        TelemetryEventName.WORK_ITEM_RECEIVED,
        TelemetryEventName.ROUTING_COMPLETED,
        TelemetryEventName.EXECUTION_STARTED,
        TelemetryEventName.EXECUTION_FAILED,
        TelemetryEventName.RETRY_STARTED,
        TelemetryEventName.EXECUTION_STARTED,
        TelemetryEventName.EXECUTION_COMPLETED,
        TelemetryEventName.WORK_ITEM_CLOSED,
    ]
    failed = sink.events[3]
    assert failed.attempt == 1
    assert failed.failure_category is FailureCategory.TIMEOUT
    assert failed.execution_state is ExecutionState.NOT_STARTED
    retry = sink.events[4]
    assert retry.attempt == _SECOND_ATTEMPT
    assert dict(retry.attributes)["recovery_action"] == "retry_same_combination"
    assert sink.events[5].attempt == _SECOND_ATTEMPT
    assert dict(sink.events[-1].attributes)["attempt_count"] == "2"


def test_model_escalation_records_source_and_target_route() -> None:
    """A model escalation event carries the S13-selected from/to combination."""
    sink = InMemoryTelemetrySink()
    first = make_profile("alpha", "model-a")
    second = make_profile("beta", "model-b")
    routing = build_routing(("runtime-a", first), ("runtime-a", second))
    runtime = ScriptedRuntime(
        "runtime-a",
        (
            failure_result("runtime-a", FailureCategory.MODEL_UNAVAILABLE, ExecutionState.NOT_STARTED),
            success_result("runtime-a"),
        ),
    )
    outcome = _run(_options(sink, limits=RecoveryLimits(max_total_attempts=2)), runtime, routing=routing)
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    escalation = next(event for event in sink.events if event.name is TelemetryEventName.ESCALATION_STARTED)
    attributes = dict(escalation.attributes)
    assert escalation.attempt == _SECOND_ATTEMPT
    assert escalation.runtime_id == "runtime-a"
    assert escalation.model_id == "provider/model-b"
    assert attributes["from_model"] == "provider/model-a"
    assert attributes["recovery_action"] == "escalate_model"


def test_usage_is_aggregated_only_when_every_attempt_reports_it() -> None:
    """The closure event sums complete usage and keeps partial usage unknown."""
    sink = InMemoryTelemetrySink()
    first = replace(
        failure_result("runtime-a", FailureCategory.TIMEOUT, ExecutionState.NOT_STARTED),
        usage=ExecutionUsage(input_tokens=1, output_tokens=2, cost_micros=3),
    )
    second = replace(
        success_result("runtime-a"),
        usage=ExecutionUsage(input_tokens=4, output_tokens=5, cost_micros=6),
    )
    runtime = ScriptedRuntime("runtime-a", (first, second))
    outcome = _run(
        _options(sink, limits=RecoveryLimits(max_attempts_per_combination=2, max_total_attempts=2)),
        runtime,
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert sink.events[-1].usage == TelemetryUsage(
        input_tokens=5,
        output_tokens=7,
        cost_micros=9,
        cost_source=TelemetryCostSource.DECLARED,
    )


def test_telemetry_failure_does_not_change_the_run_outcome() -> None:
    """A failing sink is best-effort and cannot break S13/S14/S16 execution."""
    runtime = ScriptedRuntime("runtime-a", (success_result("runtime-a"),))
    outcome = _run(_options(_FailingSink()), runtime)
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert len(runtime.requests) == 1


def test_disabled_telemetry_keeps_the_existing_contract() -> None:
    """No sink means no recorder and no behavior change."""
    runtime = ScriptedRuntime("runtime-a", (success_result("runtime-a"),))
    outcome = _run(_options(None), runtime)
    assert outcome.status is ExecutionRunStatus.SUCCEEDED


def test_blank_execution_id_is_generated_without_breaking_execution() -> None:
    """Invalid correlation input falls back to a generated id, never an exception."""
    sink = InMemoryTelemetrySink()
    runtime = ScriptedRuntime("runtime-a", (success_result("runtime-a"),))
    outcome = _run(_options(sink, execution_id=" "), runtime)
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert sink.events
    assert sink.events[0].execution_id.strip()


def test_failure_detail_and_evidence_never_enter_events() -> None:
    """Free-form failure payloads stay out of telemetry."""
    sink = InMemoryTelemetrySink()
    secret_failure = AgentExecutionResult(
        status=AgentExecutionStatus.FAILED,
        runtime_identity=RuntimeIdentity("runtime-a"),
        observed_failures=("password=hunter2",),
        evidence_refs=("evidence://hunter2",),
        failure=ExecutionFailure(
            category=FailureCategory.AUTHORIZATION_FAILURE,
            execution_state=ExecutionState.NOT_STARTED,
            evidence=("evidence://hunter2",),
            detail="password=hunter2",
        ),
    )
    runtime = ScriptedRuntime("runtime-a", (secret_failure,))
    outcome = _run(
        _options(sink, limits=RecoveryLimits(max_total_attempts=1, allow_human_intervention=False)),
        runtime,
    )
    assert outcome.status is ExecutionRunStatus.TERMINAL
    assert "hunter2" not in repr(sink.events)
