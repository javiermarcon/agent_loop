"""Contract tests for neutral failure classification (S14)."""

from __future__ import annotations

import pytest

from agent_loop.domain.faults import ExecutionFailure
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.domain.faults import FailureRecoverability
from agent_loop.domain.faults import classify_failure

_RECOVERABLE = (
    FailureCategory.RUNTIME_UNAVAILABLE,
    FailureCategory.RUNTIME_UNHEALTHY,
    FailureCategory.AUTHORIZATION_FAILURE,
    FailureCategory.MODEL_UNAVAILABLE,
    FailureCategory.EXECUTION_FAILURE,
    FailureCategory.TIMEOUT,
    FailureCategory.INVALID_RESULT,
    FailureCategory.QUALITY_FAILURE,
)
_NON_RECOVERABLE = (
    FailureCategory.INVALID_REQUEST,
    FailureCategory.CANCELLED,
    FailureCategory.RETRY_EXHAUSTED,
)


def _failure(
    category: FailureCategory,
    state: ExecutionState = ExecutionState.NOT_STARTED,
) -> ExecutionFailure:
    return ExecutionFailure(
        category=category,
        execution_state=state,
        evidence=("evidence://fixture",),
        detail="neutral fixture detail",
    )


@pytest.mark.parametrize("category", _RECOVERABLE)
def test_recoverable_categories_allow_automatic_recovery(category: FailureCategory) -> None:
    """Recoverable categories keep their neutral classification."""
    classification = classify_failure(_failure(category), idempotent=True)
    assert classification.recoverability is FailureRecoverability.RECOVERABLE
    assert classification.escalation_allowed is True


@pytest.mark.parametrize("category", _NON_RECOVERABLE)
def test_non_recoverable_categories_never_escalate(category: FailureCategory) -> None:
    """Terminal categories cannot be retried or escalated."""
    classification = classify_failure(_failure(category), idempotent=True)
    assert classification.recoverability is FailureRecoverability.NON_RECOVERABLE
    assert classification.escalation_allowed is False
    assert classification.retry_safe is False


def test_unknown_category_is_never_recoverable() -> None:
    """Unknown failures are not optimistically retried."""
    classification = classify_failure(_failure(FailureCategory.UNKNOWN), idempotent=True)
    assert classification.recoverability is FailureRecoverability.UNKNOWN
    assert classification.escalation_allowed is False


def test_not_started_execution_is_retry_safe_for_non_idempotent_operation() -> None:
    """No started execution means no effects were possible."""
    classification = classify_failure(
        _failure(FailureCategory.RUNTIME_UNAVAILABLE, ExecutionState.NOT_STARTED),
        idempotent=False,
    )
    assert classification.retry_safe is True
    assert classification.effects_possible is False


@pytest.mark.parametrize("state", [ExecutionState.STARTED, ExecutionState.COMPLETED])
def test_non_idempotent_started_execution_blocks_retry(state: ExecutionState) -> None:
    """Started non-idempotent work may have produced effects."""
    classification = classify_failure(_failure(FailureCategory.TIMEOUT, state), idempotent=False)
    assert classification.retry_safe is False
    assert classification.effects_possible is True
    assert "possible_effects_block_automatic_retry" in classification.reasons


def test_unknown_state_is_never_retry_safe_even_when_idempotent() -> None:
    """Unknown execution state is explicitly not recoverable."""
    classification = classify_failure(
        _failure(FailureCategory.TIMEOUT, ExecutionState.UNKNOWN),
        idempotent=True,
    )
    assert classification.retry_safe is False
    assert "unknown_execution_state_is_not_recoverable" in classification.reasons


def test_idempotent_completed_execution_can_be_retried() -> None:
    """Read-only completed work can be repeated safely."""
    classification = classify_failure(
        _failure(FailureCategory.QUALITY_FAILURE, ExecutionState.COMPLETED),
        idempotent=True,
    )
    assert classification.retry_safe is True
    assert classification.effects_possible is False


def test_authorization_failure_is_not_safe_for_same_combination() -> None:
    """Authorization failures need a different runtime, never a blind retry."""
    classification = classify_failure(
        _failure(FailureCategory.AUTHORIZATION_FAILURE, ExecutionState.NOT_STARTED),
        idempotent=False,
    )
    assert classification.retry_safe is False
    assert classification.escalation_allowed is True


def test_classification_preserves_evidence_and_detail() -> None:
    """Evidence never disappears during classification."""
    failure = ExecutionFailure(
        category=FailureCategory.MODEL_UNAVAILABLE,
        execution_state=ExecutionState.NOT_STARTED,
        evidence=("evidence://model",),
        detail="model missing",
    )
    classification = classify_failure(failure, idempotent=False)
    assert classification.evidence == ("evidence://model",)
    assert classification.detail == "model missing"
    assert classification.category is FailureCategory.MODEL_UNAVAILABLE
