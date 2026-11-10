"""Policy tests for deterministic recovery/escalation decisions (S14)."""

from __future__ import annotations

import pytest

from agent_loop.application.recovery_policy import EscalationAvailability
from agent_loop.application.recovery_policy import RecoveryAction
from agent_loop.application.recovery_policy import RecoveryContext
from agent_loop.application.recovery_policy import RecoveryLimits
from agent_loop.application.recovery_policy import decide_recovery
from agent_loop.domain.faults import ExecutionFailure
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.domain.faults import classify_failure


def _classification(
    category: FailureCategory = FailureCategory.TIMEOUT,
    state: ExecutionState = ExecutionState.NOT_STARTED,
    *,
    idempotent: bool = False,
):
    return classify_failure(
        ExecutionFailure(category=category, execution_state=state),
        idempotent=idempotent,
    )


def _context(
    *,
    attempts_for_combination: int = 1,
    total_attempts: int = 1,
    same_model: bool = False,
    other_runtime: bool = False,
) -> RecoveryContext:
    return RecoveryContext(
        attempts_for_combination=attempts_for_combination,
        total_attempts=total_attempts,
        alternatives=EscalationAvailability(
            same_runtime_other_model=same_model,
            other_runtime=other_runtime,
        ),
    )


def test_retry_same_combination_while_attempt_budget_remains() -> None:
    """A safe failure retries the same combination before escalating."""
    decision = decide_recovery(
        _classification(),
        context=_context(same_model=True),
        limits=RecoveryLimits(max_attempts_per_combination=2),
    )
    assert decision.action is RecoveryAction.RETRY_SAME_COMBINATION
    assert decision.retry_exhausted is False


def test_escalates_to_another_model_when_same_combination_is_exhausted() -> None:
    """Model escalation is chosen when another model is available."""
    decision = decide_recovery(
        _classification(),
        context=_context(attempts_for_combination=2, total_attempts=2, same_model=True),
        limits=RecoveryLimits(max_attempts_per_combination=2, max_total_attempts=4),
    )
    assert decision.action is RecoveryAction.ESCALATE_MODEL


def test_escalates_to_another_runtime_when_no_model_alternative_exists() -> None:
    """Runtime escalation is chosen when only another runtime is available."""
    decision = decide_recovery(
        _classification(),
        context=_context(attempts_for_combination=2, total_attempts=2, other_runtime=True),
        limits=RecoveryLimits(max_attempts_per_combination=2, max_total_attempts=4),
    )
    assert decision.action is RecoveryAction.ESCALATE_RUNTIME


def test_authorization_failure_escalates_to_another_runtime_without_same_retry() -> None:
    """Authorization failures skip same-combination retry and move runtimes."""
    decision = decide_recovery(
        _classification(FailureCategory.AUTHORIZATION_FAILURE, ExecutionState.NOT_STARTED),
        context=_context(same_model=True, other_runtime=True),
        limits=RecoveryLimits(max_attempts_per_combination=3, max_total_attempts=5),
    )
    assert decision.action is RecoveryAction.ESCALATE_RUNTIME


def test_runtime_unavailable_prefers_runtime_escalation_over_model() -> None:
    """A dead runtime cannot be fixed by another model in the same runtime."""
    decision = decide_recovery(
        _classification(FailureCategory.RUNTIME_UNAVAILABLE, ExecutionState.NOT_STARTED),
        context=_context(same_model=True, other_runtime=True),
        limits=RecoveryLimits(max_attempts_per_combination=1, max_total_attempts=4),
    )
    assert decision.action is RecoveryAction.ESCALATE_RUNTIME


def test_model_escalation_can_be_disabled_in_favor_of_runtime_escalation() -> None:
    """Disabled model escalation does not block a valid runtime alternative."""
    decision = decide_recovery(
        _classification(),
        context=_context(attempts_for_combination=2, total_attempts=2, same_model=True, other_runtime=True),
        limits=RecoveryLimits(
            max_attempts_per_combination=2,
            max_total_attempts=4,
            allow_model_escalation=False,
        ),
    )
    assert decision.action is RecoveryAction.ESCALATE_RUNTIME


def test_no_alternative_requires_human_intervention() -> None:
    """Missing alternatives never become a silent terminal fallback."""
    decision = decide_recovery(
        _classification(),
        context=_context(attempts_for_combination=2, total_attempts=2),
        limits=RecoveryLimits(max_attempts_per_combination=2, max_total_attempts=4),
    )
    assert decision.action is RecoveryAction.HUMAN_INTERVENTION
    assert "no_valid_alternative" in decision.reason


def test_human_intervention_can_be_disabled() -> None:
    """When human intervention is disallowed the run closes terminally."""
    decision = decide_recovery(
        _classification(FailureCategory.INVALID_REQUEST),
        context=_context(),
        limits=RecoveryLimits(allow_human_intervention=False),
    )
    assert decision.action is RecoveryAction.TERMINAL


def test_unknown_recoverability_requires_human_intervention() -> None:
    """Unknown failures are never retried automatically."""
    decision = decide_recovery(_classification(FailureCategory.UNKNOWN), context=_context(), limits=RecoveryLimits())
    assert decision.action is RecoveryAction.HUMAN_INTERVENTION
    assert "unknown_recoverability" in decision.reason


@pytest.mark.parametrize("state", [ExecutionState.STARTED, ExecutionState.COMPLETED])
def test_possible_effects_block_every_automatic_action(state: ExecutionState) -> None:
    """Non-idempotent started execution may only be resumed by a human."""
    decision = decide_recovery(
        _classification(FailureCategory.TIMEOUT, state),
        context=_context(same_model=True, other_runtime=True),
        limits=RecoveryLimits(max_attempts_per_combination=3, max_total_attempts=5),
    )
    assert decision.action is RecoveryAction.HUMAN_INTERVENTION
    assert "automatic_recovery_unsafe" in decision.reason


def test_unknown_execution_state_blocks_automatic_recovery() -> None:
    """Unknown state is not recoverable even for idempotent work."""
    decision = decide_recovery(
        _classification(FailureCategory.TIMEOUT, ExecutionState.UNKNOWN, idempotent=True),
        context=_context(),
        limits=RecoveryLimits(),
    )
    assert decision.action is RecoveryAction.HUMAN_INTERVENTION


def test_non_recoverable_failure_closes_terminally() -> None:
    """Invalid requests and cancellations cannot escalate."""
    decision = decide_recovery(
        _classification(FailureCategory.INVALID_REQUEST),
        context=_context(same_model=True, other_runtime=True),
        limits=RecoveryLimits(),
    )
    assert decision.action is RecoveryAction.TERMINAL
    assert decision.reason == "non_recoverable:invalid_request"


def test_total_attempt_budget_is_exhausted_before_other_rules() -> None:
    """A global retry budget stops even otherwise safe failures."""
    decision = decide_recovery(
        _classification(),
        context=_context(total_attempts=3, same_model=True, other_runtime=True),
        limits=RecoveryLimits(max_total_attempts=3),
    )
    assert decision.action is RecoveryAction.HUMAN_INTERVENTION
    assert decision.retry_exhausted is True
    assert "retry_exhausted" in decision.reason


def test_recovery_limits_reject_non_positive_per_combination_budget() -> None:
    """A zero per-combination budget is rejected instead of looping forever."""
    with pytest.raises(ValueError, match="max_attempts_per_combination"):
        RecoveryLimits(max_attempts_per_combination=0)


def test_recovery_limits_reject_non_positive_total_budget() -> None:
    """A zero total budget is rejected instead of looping forever."""
    with pytest.raises(ValueError, match="max_total_attempts"):
        RecoveryLimits(max_total_attempts=0)


@pytest.mark.parametrize(
    ("attempts_for_combination", "total_attempts", "message"),
    [
        (0, 1, "attempts_for_combination"),
        (1, 0, "total_attempts"),
    ],
)
def test_recovery_context_rejects_non_positive_attempts(
    attempts_for_combination: int,
    total_attempts: int,
    message: str,
) -> None:
    """Attempt counters must describe at least one real attempt."""
    with pytest.raises(ValueError, match=message):
        RecoveryContext(
            attempts_for_combination=attempts_for_combination,
            total_attempts=total_attempts,
        )


def test_escalation_availability_reports_any_alternative() -> None:
    """Both alternative channels compose into one availability fact."""
    assert EscalationAvailability().has_any is False
    assert EscalationAvailability(same_runtime_other_model=True).has_any is True
    assert EscalationAvailability(other_runtime=True).has_any is True


def test_escalation_is_disabled_when_both_channels_are_disallowed() -> None:
    """Explicitly disabled escalation requires human intervention."""
    decision = decide_recovery(
        _classification(),
        context=_context(attempts_for_combination=2, total_attempts=2, same_model=True, other_runtime=True),
        limits=RecoveryLimits(
            max_attempts_per_combination=2,
            max_total_attempts=4,
            allow_model_escalation=False,
            allow_runtime_escalation=False,
        ),
    )
    assert decision.action is RecoveryAction.HUMAN_INTERVENTION
