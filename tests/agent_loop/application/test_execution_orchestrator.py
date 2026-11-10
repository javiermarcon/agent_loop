"""Integration tests for the S13/S14/S16 control-plane execution loop."""

from __future__ import annotations

import pytest

from agent_loop.application.execution_orchestrator import ExecutionRunStatus
from agent_loop.application.execution_orchestrator import RoutingInputs
from agent_loop.application.execution_orchestrator import execute_work_item_with_recovery
from agent_loop.application.recovery_policy import RecoveryAction
from agent_loop.application.recovery_policy import RecoveryDecision
from agent_loop.application.recovery_policy import RecoveryLimits
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import InventoryModelRef
from agent_loop.domain.capabilities import RuntimeInventory
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import RuntimeIdentity
from tests.agent_loop.application.orchestrator_support import EXPECTED_RETRY_ATTEMPTS
from tests.agent_loop.application.orchestrator_support import READ
from tests.agent_loop.application.orchestrator_support import ScriptedRuntime
from tests.agent_loop.application.orchestrator_support import assessment as build_assessment
from tests.agent_loop.application.orchestrator_support import failure_result
from tests.agent_loop.application.orchestrator_support import now
from tests.agent_loop.application.orchestrator_support import options as run_options
from tests.agent_loop.application.orchestrator_support import profile as make_profile
from tests.agent_loop.application.orchestrator_support import routing as build_routing
from tests.agent_loop.application.orchestrator_support import snapshot
from tests.agent_loop.application.orchestrator_support import success_result
from tests.agent_loop.application.orchestrator_support import work_item as build_work_item


def testsuccess_resultful_run_uses_s16_builder_and_s13_selection() -> None:
    """The request carries routing model, assessment capabilities, and WorkItem refs."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime("runtime-a", (success_result("runtime-a"),))
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert outcome.work_item_id == "wi-1"
    assert outcome.work_item.objective == "Implement the fixture task"
    assert len(outcome.attempts) == 1
    request = runtime.requests[0]
    assert request.model == "provider/model-a"
    assert request.strategy is ExecutionStrategy.BUILD
    assert request.context_refs == ("context://fixture",)
    assert request.required_capabilities == build_assessment().required_capabilities


def test_blockedwork_item_is_never_executed() -> None:
    """S16 readiness gates the whole loop before routing."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime("runtime-a", ())
    outcome = execute_work_item_with_recovery(
        build_work_item(status=WorkItemStatus.BLOCKED),
        build_assessment(),
        options=run_options(),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.BLOCKED
    assert outcome.final_result is None
    assert runtime.requests == []


def test_unavailable_runtime_never_routes() -> None:
    """Configured but unavailable inventory produces a blocking outcome."""
    profile = make_profile("alpha", "model-a")
    routing = build_routing(("runtime-a", profile))
    unavailable = RuntimeInventory(
        runtime_identity="runtime-a",
        runtime_version="1.0.0",
        adapter_version="1",
        discovered_at=now(),
        capabilities=(
            snapshot(READ),
            snapshot("runtime.runtime-a", available=CapabilityState.NO),
        ),
        models=(InventoryModelRef(model_id="provider/model-a"),),
    )
    routing = RoutingInputs(
        inventories=(unavailable,),
        profiles=routing.profiles,
        bindings=routing.bindings,
        readiness=routing.readiness,
        strategy=routing.strategy,
    )
    runtime = ScriptedRuntime("runtime-a", ())
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(),
        routing=routing,
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.BLOCKED
    assert "routing_terminal" in outcome.final_reason
    assert runtime.requests == []


def test_safefailure_result_retries_same_combination_then_succeeds() -> None:
    """A pre-execution failure is retried within the explicit budget."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime(
        "runtime-a",
        (
            failure_result("runtime-a", FailureCategory.RUNTIME_UNAVAILABLE, ExecutionState.NOT_STARTED),
            success_result("runtime-a"),
        ),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(limits=RecoveryLimits(max_attempts_per_combination=2)),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert len(outcome.attempts) == EXPECTED_RETRY_ATTEMPTS
    assert outcome.attempts[0].recovery is not None
    assert outcome.attempts[0].recovery.action is RecoveryAction.RETRY_SAME_COMBINATION
    assert runtime.requests[0].model == runtime.requests[1].model


def test_escalates_to_another_model_after_combination_exhaustion() -> None:
    """S13 selects a different model while preserving task constraints."""
    alpha = make_profile("alpha", "model-a")
    beta = make_profile("beta", "model-b")
    runtime = ScriptedRuntime(
        "runtime-a",
        (
            failure_result("runtime-a", FailureCategory.QUALITY_FAILURE, ExecutionState.NOT_STARTED),
            success_result("runtime-a", text="second"),
        ),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(limits=RecoveryLimits(max_attempts_per_combination=1, max_total_attempts=3)),
        routing=build_routing(("runtime-a", alpha), ("runtime-a", beta)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert len(outcome.attempts) == EXPECTED_RETRY_ATTEMPTS
    assert outcome.attempts[0].recovery is not None
    assert outcome.attempts[0].recovery.action is RecoveryAction.ESCALATE_MODEL
    assert runtime.requests[0].model == "provider/model-a"
    assert runtime.requests[1].model == "provider/model-b"


def test_escalates_to_another_runtime() -> None:
    """S13 selects another runtime when no model alternative exists."""
    alpha = make_profile("alpha", "model-a")
    beta = make_profile("beta", "model-b")
    first = ScriptedRuntime(
        "runtime-a",
        (failure_result("runtime-a", FailureCategory.RUNTIME_UNAVAILABLE, ExecutionState.NOT_STARTED),),
    )
    second = ScriptedRuntime("runtime-b", (success_result("runtime-b"),))
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(limits=RecoveryLimits(max_attempts_per_combination=1, max_total_attempts=3)),
        routing=build_routing(("runtime-a", alpha), ("runtime-b", beta)),
        runtimes=(first, second),
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert outcome.attempts[0].recovery is not None
    assert outcome.attempts[0].recovery.action is RecoveryAction.ESCALATE_RUNTIME
    assert outcome.attempts[1].routing.selected is not None
    assert outcome.attempts[1].routing.selected.runtime_id == "runtime-b"
    assert len(first.requests) == 1
    assert len(second.requests) == 1


def test_missing_runtime_registration_escalates_to_a_real_runtime() -> None:
    """A routed runtime without an implementation is unavailable, not ignored."""
    alpha = make_profile("alpha", "model-a")
    beta = make_profile("beta", "model-b")
    second = ScriptedRuntime("runtime-b", (success_result("runtime-b"),))
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(limits=RecoveryLimits(max_attempts_per_combination=1, max_total_attempts=3)),
        routing=build_routing(("runtime-a", alpha), ("runtime-b", beta)),
        runtimes=(second,),
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert outcome.attempts[0].classification is not None
    assert outcome.attempts[0].classification.category is FailureCategory.RUNTIME_UNAVAILABLE
    assert outcome.attempts[1].routing.selected is not None
    assert outcome.attempts[1].routing.selected.runtime_id == "runtime-b"


def test_non_idempotent_startedfailure_result_requires_human_intervention() -> None:
    """Possible effects block every automatic re-execution."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime(
        "runtime-a",
        (failure_result("runtime-a", FailureCategory.TIMEOUT, ExecutionState.STARTED),),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(
            limits=RecoveryLimits(max_attempts_per_combination=3, max_total_attempts=5),
            mutation=True,
        ),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.HUMAN_INTERVENTION
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].classification is not None
    assert outcome.attempts[0].classification.effects_possible is True


def test_timeout_with_unknown_state_requires_human_intervention() -> None:
    """Unknown execution state is never retried."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime(
        "runtime-a",
        (failure_result("runtime-a", FailureCategory.TIMEOUT, ExecutionState.UNKNOWN),),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(limits=RecoveryLimits(max_attempts_per_combination=3)),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.HUMAN_INTERVENTION
    assert len(outcome.attempts) == 1


def test_total_retry_exhaustion_is_explicit() -> None:
    """The global attempt budget stops retrying deterministically."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime(
        "runtime-a",
        (
            failure_result("runtime-a", FailureCategory.RUNTIME_UNAVAILABLE, ExecutionState.NOT_STARTED),
            failure_result("runtime-a", FailureCategory.RUNTIME_UNAVAILABLE, ExecutionState.NOT_STARTED),
        ),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(
            limits=RecoveryLimits(max_attempts_per_combination=5, max_total_attempts=2),
        ),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.HUMAN_INTERVENTION
    assert len(outcome.attempts) == EXPECTED_RETRY_ATTEMPTS
    assert outcome.attempts[-1].recovery is not None
    assert outcome.attempts[-1].recovery.retry_exhausted is True


def test_non_recoverablefailure_result_closes_terminally_without_alternatives() -> None:
    """Invalid requests are terminal even when alternatives exist."""
    alpha = make_profile("alpha", "model-a")
    beta = make_profile("beta", "model-b")
    runtime = ScriptedRuntime(
        "runtime-a",
        (failure_result("runtime-a", FailureCategory.INVALID_REQUEST, ExecutionState.NOT_STARTED),),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(),
        routing=build_routing(("runtime-a", alpha), ("runtime-b", beta)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.TERMINAL
    assert len(outcome.attempts) == 1


def test_recoverablefailure_result_without_alternatives_requires_human_intervention() -> None:
    """A safe failure with no alternative is surfaced, not silently closed."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime(
        "runtime-a",
        (failure_result("runtime-a", FailureCategory.RUNTIME_UNAVAILABLE, ExecutionState.NOT_STARTED),),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(limits=RecoveryLimits(max_attempts_per_combination=1, max_total_attempts=3)),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.HUMAN_INTERVENTION
    assert len(outcome.attempts) == 1
    assert "no_valid_alternative" in outcome.final_reason


def test_cancelled_result_is_terminal() -> None:
    """Cancellation is never converted into a retry."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime(
        "runtime-a",
        (
            AgentExecutionResult(
                status=AgentExecutionStatus.CANCELLED,
                runtime_identity=RuntimeIdentity("runtime-a"),
                evidence_refs=("evidence://cancelled",),
            ),
        ),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.CANCELLED
    assert outcome.final_result is not None


def test_context_and_evidence_are_preserved_across_attempts() -> None:
    """Routing evidence, failure evidence, and the final result survive the retry."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime(
        "runtime-a",
        (
            failure_result(
                "runtime-a",
                FailureCategory.RUNTIME_UNAVAILABLE,
                ExecutionState.NOT_STARTED,
                evidence=("evidence://first-failure",),
            ),
            success_result("runtime-a", evidence=("evidence://final",)),
        ),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(limits=RecoveryLimits(max_attempts_per_combination=2)),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert outcome.attempts[0].classification is not None
    assert outcome.attempts[0].classification.evidence == ("evidence://first-failure",)
    assert outcome.attempts[0].routing.evidence
    assert outcome.final_result is not None
    assert outcome.final_result.evidence_refs == ("evidence://final",)


def test_failed_result_without_neutralfailure_result_is_unknown() -> None:
    """Legacy adapters without failure detail are never auto-retried."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime(
        "runtime-a",
        (
            AgentExecutionResult(
                status=AgentExecutionStatus.FAILED,
                runtime_identity=RuntimeIdentity("runtime-a"),
            ),
        ),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.HUMAN_INTERVENTION
    assert outcome.attempts[0].classification is not None
    assert outcome.attempts[0].classification.category is FailureCategory.UNKNOWN
    assert "without neutral failure detail" in outcome.attempts[0].classification.detail


def test_duplicate_runtime_registration_is_rejected() -> None:
    """Two adapters claiming one identity fail closed."""
    profile = make_profile("alpha", "model-a")
    with pytest.raises(ValueError, match="duplicate AgentRuntime identity"):
        execute_work_item_with_recovery(
            build_work_item(),
            build_assessment(),
            options=run_options(),
            routing=build_routing(("runtime-a", profile)),
            runtimes=(ScriptedRuntime("runtime-a", ()), ScriptedRuntime("runtime-a", ())),
        )


def test_loop_bound_stops_even_if_policy_requests_more(monkeypatch) -> None:
    """The orchestrator's own bound prevents an infinite retry cycle."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime(
        "runtime-a",
        (
            failure_result("runtime-a", FailureCategory.RUNTIME_UNAVAILABLE, ExecutionState.NOT_STARTED),
            failure_result("runtime-a", FailureCategory.RUNTIME_UNAVAILABLE, ExecutionState.NOT_STARTED),
        ),
    )

    def _always_retry(*args, **kwargs) -> RecoveryDecision:
        del args, kwargs
        return RecoveryDecision(RecoveryAction.RETRY_SAME_COMBINATION, "forced")

    monkeypatch.setattr(
        "agent_loop.application.execution_orchestrator.decide_recovery",
        _always_retry,
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(limits=RecoveryLimits(max_total_attempts=2)),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.HUMAN_INTERVENTION
    assert len(outcome.attempts) == EXPECTED_RETRY_ATTEMPTS
    assert outcome.final_reason == "retry_exhausted:total_attempts=2"


def test_qualityfailure_result_is_not_masked_assuccess_result() -> None:
    """Quality failures are classified and never become successful runs."""
    profile = make_profile("alpha", "model-a")
    runtime = ScriptedRuntime(
        "runtime-a",
        (failure_result("runtime-a", FailureCategory.QUALITY_FAILURE, ExecutionState.COMPLETED),),
    )
    outcome = execute_work_item_with_recovery(
        build_work_item(),
        build_assessment(),
        options=run_options(limits=RecoveryLimits(max_attempts_per_combination=1)),
        routing=build_routing(("runtime-a", profile)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.HUMAN_INTERVENTION
    assert outcome.attempts[0].classification is not None
    assert outcome.attempts[0].classification.category is FailureCategory.QUALITY_FAILURE
