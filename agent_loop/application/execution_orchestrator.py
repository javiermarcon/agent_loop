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
from typing import TYPE_CHECKING
from typing import cast

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
class _PreparedRun:
    """Validated routing and runtime registry for a schedulable WorkItem."""

    registry: dict[str, AgentRuntime]
    decision: RoutingDecision


def execute_work_item_with_recovery(
    work_item: WorkItem,
    assessment: TaskAssessment,
    *,
    options: ExecutionRunOptions,
    routing: RoutingInputs,
    runtimes: tuple[AgentRuntime, ...],
) -> ExecutionRunOutcome:
    """Execute one WorkItem with bounded retry/escalation, or stop explicitly."""
    prepared = _prepare_run(work_item, assessment, options=options, routing=routing, runtimes=runtimes)
    if isinstance(prepared, ExecutionRunOutcome):
        return prepared
    registry = prepared.registry
    decision = prepared.decision
    attempts: list[ExecutionAttempt] = []
    attempts_per_combination: dict[tuple[str, str], int] = {}
    attempted_combinations: set[tuple[str, str]] = set()
    while True:
        if len(attempts) >= options.limits.max_total_attempts:
            return _exhausted_outcome(work_item, attempts, options.limits)
        selected = cast("CompatibleModelCandidate", decision.selected)
        key = selected.identity_key()
        attempts_per_combination[key] = attempts_per_combination.get(key, 0) + 1
        attempted_combinations.add(key)
        request = _build_request(
            work_item,
            assessment,
            decision=decision,
            options=options,
            attempt_number=len(attempts) + 1,
        )
        result = _execute(registry, selected.runtime_id, request)
        terminal = _terminal_result(work_item, attempts, decision, request, result)
        if terminal is not None:
            return terminal
        failure = result.failure or _unknown_failure(result)
        classification = classify_failure(
            failure,
            idempotent=not options.mutation_policy.allow_workspace_mutation,
        )
        same_route, other_route = _escalation_routes(
            assessment,
            routing,
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
            limits=options.limits,
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
            return _recovery_outcome(work_item, attempts, recovery)
        decision = next_decision


def _prepare_run(
    work_item: WorkItem,
    assessment: TaskAssessment,
    *,
    options: ExecutionRunOptions,
    routing: RoutingInputs,
    runtimes: tuple[AgentRuntime, ...],
) -> _PreparedRun | ExecutionRunOutcome:
    if not is_schedulable(work_item, completed_ids=options.completed_ids):
        return _blocked_outcome(work_item, "work_item_not_schedulable")
    decision = _route(
        assessment,
        routing,
        RoutingConstraints(strategy=routing.strategy, readiness=routing.readiness),
    )
    if decision.selected is None:
        return _blocked_outcome(work_item, "routing_terminal:no_operationally_eligible_candidate")
    return _PreparedRun(registry=_runtime_registry(runtimes), decision=decision)


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
