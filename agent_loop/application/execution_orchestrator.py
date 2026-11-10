"""Control-plane execution loop with deterministic failure recovery (S13/S14/S16).

The loop composes explicit collaborators only:

- S13 ``select_routing`` chooses runtime/model/strategy;
- S16 ``build_execution_request`` gates each WorkItem handoff;
- an injected ``AgentRuntime`` executes the request;
- S14 ``classify_failure`` / ``decide_recovery`` choose the next action;
- escalation goes back through S13 with explicit exclusions.

It never imports concrete adapters and never persists attempt history; durable
resume is outside S14. Every loop is bounded by ``RecoveryLimits``.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from time import monotonic
from typing import TYPE_CHECKING
from typing import cast
from uuid import uuid4

from agent_loop.application.execution_request_builder import ExecutionRequestOptions
from agent_loop.application.execution_request_builder import build_execution_request
from agent_loop.application.execution_routing import RoutingConstraints
from agent_loop.application.execution_routing import select_routing
from agent_loop.application.recovery_policy import EscalationAvailability
from agent_loop.application.recovery_policy import RecoveryAction
from agent_loop.application.recovery_policy import RecoveryContext
from agent_loop.application.recovery_policy import RecoveryDecision
from agent_loop.application.recovery_policy import RecoveryLimits
from agent_loop.application.recovery_policy import decide_recovery
from agent_loop.domain.faults import ExecutionFailure
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.domain.faults import classify_failure
from agent_loop.domain.work_item import is_schedulable
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import RuntimeIdentity
from agent_loop.ports.telemetry import TelemetryEventName
from agent_loop.telemetry.recorder import TelemetryFacts
from agent_loop.telemetry.recorder import TelemetryRecorder
from agent_loop.telemetry.recorder import aggregate_usage
from agent_loop.telemetry.recorder import usage_from_result

if TYPE_CHECKING:
    from agent_loop.application.execution_routing import RoutingDecision
    from agent_loop.application.execution_routing import RuntimeReadinessPolicy
    from agent_loop.domain.capabilities import RuntimeInventory
    from agent_loop.domain.compatible_candidates import CompatibleModelCandidate
    from agent_loop.domain.faults import FailureClassification
    from agent_loop.domain.model_profile_catalog import ModelProfileCatalog
    from agent_loop.domain.model_profile_catalog import RuntimeModelProfileCatalog
    from agent_loop.domain.task_assessment import TaskAssessment
    from agent_loop.domain.work_item import WorkItem
    from agent_loop.ports.agent_runtime import AgentRuntime
    from agent_loop.ports.agent_runtime import ExecutionStrategy
    from agent_loop.ports.agent_runtime import MutationPolicy
    from agent_loop.ports.agent_runtime import PermissionPolicy
    from agent_loop.ports.telemetry import TelemetrySink


class ExecutionRunStatus(StrEnum):
    """Terminal control-plane outcome for one WorkItem execution run."""

    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    TERMINAL = "terminal"
    HUMAN_INTERVENTION = "human_intervention"


@dataclass(frozen=True, slots=True)
class RoutingInputs:
    """S13 inputs supplied by the composition root."""

    inventories: tuple[RuntimeInventory, ...]
    profiles: ModelProfileCatalog
    bindings: RuntimeModelProfileCatalog
    readiness: tuple[RuntimeReadinessPolicy, ...]
    strategy: ExecutionStrategy


@dataclass(frozen=True, slots=True)
class ExecutionRunOptions:
    """Port-side and gating inputs for one control-plane run."""

    working_directory: str
    mutation_policy: MutationPolicy
    permission_policy: PermissionPolicy
    completed_ids: frozenset[str] = frozenset()
    structured_output_schema: str | None = None
    session_ref: str | None = None
    limits: RecoveryLimits = field(default_factory=RecoveryLimits)
    telemetry: TelemetrySink | None = None
    execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    """Immutable record of one routed execution attempt."""

    number: int
    routing: RoutingDecision
    request: AgentExecutionRequest | None
    result: AgentExecutionResult | None
    classification: FailureClassification | None
    recovery: RecoveryDecision | None


@dataclass(frozen=True, slots=True)
class ExecutionRunOutcome:
    """Terminal run outcome preserving WorkItem, routing, evidence, and attempts."""

    work_item: WorkItem
    status: ExecutionRunStatus
    attempts: tuple[ExecutionAttempt, ...]
    final_reason: str

    @property
    def work_item_id(self) -> str:
        """Return the preserved WorkItem identity."""
        return self.work_item.work_item_id

    @property
    def final_result(self) -> AgentExecutionResult | None:
        """Return the last runtime result, if any attempt reached a runtime."""
        for attempt in reversed(self.attempts):
            if attempt.result is not None:
                return attempt.result
        return None


@dataclass(frozen=True, slots=True)
class _RunContext:
    """Immutable inputs shared by every attempt of one control-plane run."""

    work_item: WorkItem
    assessment: TaskAssessment
    options: ExecutionRunOptions
    routing: RoutingInputs
    registry: dict[str, AgentRuntime]
    recorder: TelemetryRecorder | None
    started: float


def execute_work_item_with_recovery(
    work_item: WorkItem,
    assessment: TaskAssessment,
    *,
    options: ExecutionRunOptions,
    routing: RoutingInputs,
    runtimes: tuple[AgentRuntime, ...],
) -> ExecutionRunOutcome:
    """Execute one WorkItem with bounded retry/escalation, or stop explicitly."""
    started = monotonic()
    recorder = _telemetry_recorder(options)
    _record_work_item_received(recorder, work_item)
    if not is_schedulable(work_item, completed_ids=options.completed_ids):
        return _close_run(_blocked_outcome(work_item, "work_item_not_schedulable"), recorder, started)
    decision = _route(
        assessment,
        routing,
        RoutingConstraints(strategy=routing.strategy, readiness=routing.readiness),
    )
    _record_routing(recorder, decision)
    if decision.selected is None:
        return _close_run(
            _blocked_outcome(work_item, "routing_terminal:no_operationally_eligible_candidate"),
            recorder,
            started,
        )
    context = _RunContext(
        work_item=work_item,
        assessment=assessment,
        options=options,
        routing=routing,
        registry=_runtime_registry(runtimes),
        recorder=recorder,
        started=started,
    )
    attempts: list[ExecutionAttempt] = []
    attempts_per_combination: dict[tuple[str, str], int] = {}
    attempted_combinations: set[tuple[str, str]] = set()
    while True:
        if len(attempts) >= options.limits.max_total_attempts:
            return _close_run(_exhausted_outcome(work_item, attempts, options.limits), recorder, started)
        transition = _perform_attempt(context, decision, attempts, attempts_per_combination, attempted_combinations)
        if isinstance(transition, ExecutionRunOutcome):
            return transition
        decision = transition


def _perform_attempt(
    context: _RunContext,
    decision: RoutingDecision,
    attempts: list[ExecutionAttempt],
    attempts_per_combination: dict[tuple[str, str], int],
    attempted_combinations: set[tuple[str, str]],
) -> ExecutionRunOutcome | RoutingDecision:
    """Execute one routed attempt and return the next route or a terminal outcome."""
    selected = cast("CompatibleModelCandidate", decision.selected)
    key = selected.identity_key()
    attempts_per_combination[key] = attempts_per_combination.get(key, 0) + 1
    attempted_combinations.add(key)
    request = _build_request(
        context.work_item,
        context.assessment,
        decision=decision,
        options=context.options,
        attempt_number=len(attempts) + 1,
    )
    attempt_number = len(attempts) + 1
    _record_execution_started(context.recorder, attempt_number, selected, request)
    attempt_started = monotonic()
    result = _execute(context.registry, selected.runtime_id, request)
    _record_execution_result(context.recorder, attempt_number, request, result, attempt_started)
    terminal = _terminal_result(context.work_item, attempts, decision, request, result)
    if terminal is not None:
        return _close_run(terminal, context.recorder, context.started)
    failure = _result_failure(result)
    classification = classify_failure(
        failure,
        idempotent=not context.options.mutation_policy.allow_workspace_mutation,
    )
    same_route, other_route = _escalation_routes(
        context.assessment,
        context.routing,
        attempted=attempted_combinations,
        current_runtime_id=selected.runtime_id,
    )
    recovery = decide_recovery(
        classification,
        context=RecoveryContext(
            attempts_for_combination=attempts_per_combination[key],
            total_attempts=len(attempts) + 1,
            alternatives=_availability(same_route, other_route),
        ),
        limits=context.options.limits,
    )
    attempts.append(
        ExecutionAttempt(
            len(attempts) + 1,
            decision,
            request,
            result,
            classification,
            recovery,
        ),
    )
    next_decision = _next_decision(recovery.action, decision, same_route, other_route)
    if next_decision is None:
        return _close_run(_recovery_outcome(context.work_item, attempts, recovery), context.recorder, context.started)
    _record_transition(context.recorder, len(attempts) + 1, decision, next_decision, recovery)
    return next_decision


def _terminal_result(
    work_item: WorkItem,
    attempts: list[ExecutionAttempt],
    decision: RoutingDecision,
    request: AgentExecutionRequest,
    result: AgentExecutionResult,
) -> ExecutionRunOutcome | None:
    if result.status is AgentExecutionStatus.SUCCEEDED:
        attempts.append(ExecutionAttempt(len(attempts) + 1, decision, request, result, None, None))
        return ExecutionRunOutcome(
            work_item,
            ExecutionRunStatus.SUCCEEDED,
            tuple(attempts),
            "succeeded",
        )
    if result.status is AgentExecutionStatus.CANCELLED:
        attempts.append(ExecutionAttempt(len(attempts) + 1, decision, request, result, None, None))
        return ExecutionRunOutcome(
            work_item,
            ExecutionRunStatus.CANCELLED,
            tuple(attempts),
            "cancelled",
        )
    return None


def _blocked_outcome(work_item: WorkItem, reason: str) -> ExecutionRunOutcome:
    return ExecutionRunOutcome(
        work_item=work_item,
        status=ExecutionRunStatus.BLOCKED,
        attempts=(),
        final_reason=reason,
    )


def _runtime_registry(runtimes: tuple[AgentRuntime, ...]) -> dict[str, AgentRuntime]:
    registry: dict[str, AgentRuntime] = {}
    for runtime in runtimes:
        runtime_id = runtime.identity.runtime_id
        if runtime_id in registry:
            message = f"duplicate AgentRuntime identity: {runtime_id}"
            raise ValueError(message)
        registry[runtime_id] = runtime
    return registry


def _telemetry_recorder(options: ExecutionRunOptions) -> TelemetryRecorder | None:
    if options.telemetry is None:
        return None
    execution_id = (options.execution_id or "").strip() or uuid4().hex
    return TelemetryRecorder(
        options.telemetry,
        execution_id=execution_id,
        target_repository=options.working_directory,
    )


def _record_work_item_received(recorder: TelemetryRecorder | None, work_item: WorkItem) -> None:
    if recorder is None:
        return
    recorder.record(
        TelemetryEventName.WORK_ITEM_RECEIVED,
        TelemetryFacts(
            work_item_id=work_item.work_item_id,
            status=work_item.status.value,
            attributes={
                "dependency_count": str(len(work_item.depends_on)),
                "context_ref_count": str(len(work_item.context_refs)),
            },
        ),
    )


def _record_routing(recorder: TelemetryRecorder | None, decision: RoutingDecision) -> None:
    if recorder is None:
        return
    selected = decision.selected
    recorder.record(
        TelemetryEventName.ROUTING_COMPLETED,
        TelemetryFacts(
            runtime_id=None if selected is None else selected.runtime_id,
            model_id=None if selected is None else selected.native_model_ref,
            status="selected" if selected is not None else "no_selection",
            attributes={
                "strategy": decision.strategy.value,
                "eligible_count": str(len(decision.eligible)),
                "eligible_runtimes": ",".join(candidate.runtime_id for candidate in decision.eligible),
                "evidence_count": str(len(decision.evidence)),
                "reason_count": str(len(decision.reasons)),
            },
        ),
    )


def _record_execution_started(
    recorder: TelemetryRecorder | None,
    attempt: int,
    selected: CompatibleModelCandidate,
    request: AgentExecutionRequest,
) -> None:
    if recorder is None:
        return
    recorder.record(
        TelemetryEventName.EXECUTION_STARTED,
        TelemetryFacts(
            attempt=attempt,
            runtime_id=selected.runtime_id,
            model_id=request.model,
            session_ref=request.session_ref,
            status="started",
            attributes={
                "strategy": request.strategy.value,
                "required_capability_count": str(len(request.required_capabilities)),
                "workspace_mutation": str(request.mutation_policy.allow_workspace_mutation).lower(),
            },
        ),
    )


def _record_execution_result(
    recorder: TelemetryRecorder | None,
    attempt: int,
    request: AgentExecutionRequest,
    result: AgentExecutionResult,
    started: float,
) -> None:
    if recorder is None:
        return
    failed = result.status is AgentExecutionStatus.FAILED
    attributes = {
        "evidence_count": str(len(result.evidence_refs)),
        "observed_failure_count": str(len(result.observed_failures)),
        "structured_output_present": str(result.structured_output is not None).lower(),
    }
    if request.model is not None:
        attributes["requested_model"] = request.model
    recorder.record(
        TelemetryEventName.EXECUTION_FAILED if failed else TelemetryEventName.EXECUTION_COMPLETED,
        TelemetryFacts(
            attempt=attempt,
            runtime_id=result.runtime_identity.runtime_id,
            model_id=result.model_id,
            session_ref=result.session_ref,
            status=result.status.value,
            duration_ms=_elapsed_ms(started),
            failure=_result_failure(result) if failed else None,
            usage=usage_from_result(result),
            attributes=attributes,
        ),
    )


def _record_transition(
    recorder: TelemetryRecorder | None,
    attempt: int,
    current: RoutingDecision,
    next_decision: RoutingDecision,
    recovery: RecoveryDecision,
) -> None:
    if recorder is None:
        return
    current_selected = cast("CompatibleModelCandidate", current.selected)
    next_selected = cast("CompatibleModelCandidate", next_decision.selected)
    retrying = recovery.action is RecoveryAction.RETRY_SAME_COMBINATION
    attributes = {
        "recovery_action": recovery.action.value,
        "reason": recovery.reason,
        "from_runtime": current_selected.runtime_id,
        "from_model": current_selected.native_model_ref,
    }
    recorder.record(
        TelemetryEventName.RETRY_STARTED if retrying else TelemetryEventName.ESCALATION_STARTED,
        TelemetryFacts(
            attempt=attempt,
            runtime_id=next_selected.runtime_id,
            model_id=next_selected.native_model_ref,
            status="scheduled",
            attributes=attributes,
        ),
    )


def _close_run(
    outcome: ExecutionRunOutcome,
    recorder: TelemetryRecorder | None,
    started: float,
) -> ExecutionRunOutcome:
    if recorder is None:
        return outcome
    usage = aggregate_usage(
        None if attempt.result is None else usage_from_result(attempt.result) for attempt in outcome.attempts
    )
    recorder.record(
        TelemetryEventName.WORK_ITEM_CLOSED,
        TelemetryFacts(
            work_item_id=outcome.work_item_id,
            status=outcome.status.value,
            duration_ms=_elapsed_ms(started),
            usage=usage,
            attributes={
                "reason": outcome.final_reason,
                "attempt_count": str(len(outcome.attempts)),
            },
        ),
    )
    return outcome


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))


def _route(
    assessment: TaskAssessment,
    routing: RoutingInputs,
    constraints: RoutingConstraints,
) -> RoutingDecision:
    return select_routing(
        assessment,
        inventories=routing.inventories,
        profiles=routing.profiles,
        bindings=routing.bindings,
        constraints=constraints,
    )


def _build_request(
    work_item: WorkItem,
    assessment: TaskAssessment,
    *,
    decision: RoutingDecision,
    options: ExecutionRunOptions,
    attempt_number: int,
) -> AgentExecutionRequest:
    selected = cast("CompatibleModelCandidate", decision.selected)
    return build_execution_request(
        work_item,
        completed_ids=options.completed_ids,
        options=ExecutionRequestOptions(
            strategy=decision.strategy,
            working_directory=options.working_directory,
            mutation_policy=options.mutation_policy,
            permission_policy=options.permission_policy,
            required_capabilities=assessment.required_capabilities,
            model=selected.native_model_ref,
            structured_output_schema=options.structured_output_schema,
            session_ref=options.session_ref if attempt_number == 1 else None,
        ),
    )


def _execute(
    registry: dict[str, AgentRuntime],
    runtime_id: str,
    request: AgentExecutionRequest,
) -> AgentExecutionResult:
    runtime = registry.get(runtime_id)
    if runtime is None:
        return AgentExecutionResult(
            status=AgentExecutionStatus.FAILED,
            runtime_identity=RuntimeIdentity(runtime_id=runtime_id),
            failure=ExecutionFailure(
                category=FailureCategory.RUNTIME_UNAVAILABLE,
                execution_state=ExecutionState.NOT_STARTED,
                detail=f"no AgentRuntime registered for {runtime_id!r}",
            ),
        )
    return runtime.execute(request)


def _result_failure(result: AgentExecutionResult) -> ExecutionFailure:
    return result.failure or _unknown_failure(result)


def _unknown_failure(result: AgentExecutionResult) -> ExecutionFailure:
    detail = "; ".join(result.observed_failures) or "runtime failed without neutral failure detail"
    return ExecutionFailure(
        category=FailureCategory.UNKNOWN,
        execution_state=ExecutionState.UNKNOWN,
        evidence=result.evidence_refs,
        detail=detail,
    )


def _escalation_routes(
    assessment: TaskAssessment,
    routing: RoutingInputs,
    *,
    attempted: set[tuple[str, str]],
    current_runtime_id: str,
) -> tuple[RoutingDecision, RoutingDecision]:
    same_route = _route(
        assessment,
        routing,
        RoutingConstraints(
            strategy=routing.strategy,
            readiness=routing.readiness,
            excluded_combinations=frozenset(attempted),
            required_runtime_id=current_runtime_id,
        ),
    )
    other_route = _route(
        assessment,
        routing,
        RoutingConstraints(
            strategy=routing.strategy,
            readiness=routing.readiness,
            excluded_combinations=frozenset(attempted),
            forbidden_runtime_ids=frozenset({current_runtime_id}),
        ),
    )
    return same_route, other_route


def _availability(same_route: RoutingDecision, other_route: RoutingDecision) -> EscalationAvailability:
    return EscalationAvailability(
        same_runtime_other_model=same_route.selected is not None,
        other_runtime=other_route.selected is not None,
    )


def _next_decision(
    action: RecoveryAction,
    current: RoutingDecision,
    same_route: RoutingDecision,
    other_route: RoutingDecision,
) -> RoutingDecision | None:
    if action is RecoveryAction.RETRY_SAME_COMBINATION:
        return current
    if action is RecoveryAction.ESCALATE_MODEL:
        return _route_or_stop(same_route)
    if action is RecoveryAction.ESCALATE_RUNTIME:
        return _route_or_stop(other_route)
    return None


def _route_or_stop(decision: RoutingDecision) -> RoutingDecision | None:
    return decision if decision.selected is not None else None


def _recovery_outcome(
    work_item: WorkItem,
    attempts: list[ExecutionAttempt],
    recovery: RecoveryDecision,
) -> ExecutionRunOutcome:
    status = (
        ExecutionRunStatus.HUMAN_INTERVENTION
        if recovery.action is RecoveryAction.HUMAN_INTERVENTION
        else ExecutionRunStatus.TERMINAL
    )
    return ExecutionRunOutcome(work_item, status, tuple(attempts), recovery.reason)


def _exhausted_outcome(
    work_item: WorkItem,
    attempts: list[ExecutionAttempt],
    limits: RecoveryLimits,
) -> ExecutionRunOutcome:
    status = ExecutionRunStatus.HUMAN_INTERVENTION if limits.allow_human_intervention else ExecutionRunStatus.TERMINAL
    reason = f"retry_exhausted:total_attempts={len(attempts)}"
    return ExecutionRunOutcome(work_item, status, tuple(attempts), reason)
