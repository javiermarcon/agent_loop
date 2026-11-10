"""Neutral control-plane failure vocabulary and deterministic classification (S14).

The runtime adapters report a neutral ``ExecutionFailure`` on
``AgentExecutionResult``. This module explains what that failure means for
recovery without depending on provider messages or adapter-specific error
types. Retry/escalation policy lives in application; adapter packages must not
implement their own retry loops.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureCategory(StrEnum):
    """Stable failure classes shared by every AgentRuntime adapter."""

    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    RUNTIME_UNHEALTHY = "runtime_unhealthy"
    AUTHORIZATION_FAILURE = "authorization_failure"
    MODEL_UNAVAILABLE = "model_unavailable"
    EXECUTION_FAILURE = "execution_failure"
    TIMEOUT = "timeout"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESULT = "invalid_result"
    QUALITY_FAILURE = "quality_failure"
    CANCELLED = "cancelled"
    RETRY_EXHAUSTED = "retry_exhausted"
    UNKNOWN = "unknown"


class ExecutionState(StrEnum):
    """Known state of the execution that produced a failure."""

    NOT_STARTED = "not_started"
    STARTED = "started"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class FailureRecoverability(StrEnum):
    """Whether control-plane policy may attempt an automatic recovery."""

    RECOVERABLE = "recoverable"
    NON_RECOVERABLE = "non_recoverable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ExecutionFailure:
    """Neutral failure detail exposed by a runtime adapter.

    ``execution_state`` must reflect evidence the adapter actually observed.
    ``UNKNOWN`` is never treated as safe by ``classify_failure``.
    """

    category: FailureCategory
    execution_state: ExecutionState
    evidence: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class FailureClassification:
    """Deterministic interpretation of one failure for recovery policy."""

    category: FailureCategory
    execution_state: ExecutionState
    recoverability: FailureRecoverability
    escalation_allowed: bool
    retry_safe: bool
    effects_possible: bool
    idempotent: bool
    evidence: tuple[str, ...]
    detail: str
    reasons: tuple[str, ...]


_RECOVERABLE_CATEGORIES = frozenset(
    {
        FailureCategory.RUNTIME_UNAVAILABLE,
        FailureCategory.RUNTIME_UNHEALTHY,
        FailureCategory.AUTHORIZATION_FAILURE,
        FailureCategory.MODEL_UNAVAILABLE,
        FailureCategory.EXECUTION_FAILURE,
        FailureCategory.TIMEOUT,
        FailureCategory.INVALID_RESULT,
        FailureCategory.QUALITY_FAILURE,
    },
)
_NON_RECOVERABLE_CATEGORIES = frozenset(
    {
        FailureCategory.INVALID_REQUEST,
        FailureCategory.CANCELLED,
        FailureCategory.RETRY_EXHAUSTED,
    },
)
_ESCALATION_CATEGORIES = frozenset(
    {
        FailureCategory.RUNTIME_UNAVAILABLE,
        FailureCategory.RUNTIME_UNHEALTHY,
        FailureCategory.AUTHORIZATION_FAILURE,
        FailureCategory.MODEL_UNAVAILABLE,
        FailureCategory.EXECUTION_FAILURE,
        FailureCategory.TIMEOUT,
        FailureCategory.INVALID_RESULT,
        FailureCategory.QUALITY_FAILURE,
    },
)
_SAME_COMBINATION_UNSAFE_CATEGORIES = frozenset(
    {
        FailureCategory.AUTHORIZATION_FAILURE,
        FailureCategory.INVALID_REQUEST,
        FailureCategory.CANCELLED,
        FailureCategory.RETRY_EXHAUSTED,
    },
)


def classify_failure(
    failure: ExecutionFailure,
    *,
    idempotent: bool,
) -> FailureClassification:
    """Classify ``failure`` without inspecting provider-specific messages.

    Automatic re-execution is only safe when the prior execution demonstrably
    did not produce effects (``NOT_STARTED``) or the operation is idempotent.
    An unknown execution state is never treated as recoverable.
    """
    category = failure.category
    state = failure.execution_state
    recoverability = _recoverability_for(category)
    effects_possible = not idempotent and state is not ExecutionState.NOT_STARTED
    retry_safe = _retry_safe(category, state, idempotent=idempotent)
    reasons = [
        f"category={category.value}",
        f"execution_state={state.value}",
        f"recoverability={recoverability.value}",
        f"idempotent={idempotent}",
        f"effects_possible={effects_possible}",
        f"retry_safe={retry_safe}",
    ]
    if state is ExecutionState.UNKNOWN:
        reasons.append("unknown_execution_state_is_not_recoverable")
    if effects_possible:
        reasons.append("possible_effects_block_automatic_retry")
    return FailureClassification(
        category=category,
        execution_state=state,
        recoverability=recoverability,
        escalation_allowed=category in _ESCALATION_CATEGORIES,
        retry_safe=retry_safe,
        effects_possible=effects_possible,
        idempotent=idempotent,
        evidence=failure.evidence,
        detail=failure.detail,
        reasons=tuple(reasons),
    )


def _recoverability_for(category: FailureCategory) -> FailureRecoverability:
    if category in _RECOVERABLE_CATEGORIES:
        return FailureRecoverability.RECOVERABLE
    if category in _NON_RECOVERABLE_CATEGORIES:
        return FailureRecoverability.NON_RECOVERABLE
    return FailureRecoverability.UNKNOWN


def _retry_safe(category: FailureCategory, state: ExecutionState, *, idempotent: bool) -> bool:
    if category in _SAME_COMBINATION_UNSAFE_CATEGORIES:
        return False
    if state is ExecutionState.UNKNOWN:
        return False
    return idempotent or state is ExecutionState.NOT_STARTED
