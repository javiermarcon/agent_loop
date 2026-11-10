"""Deterministic recovery and escalation policy for control-plane failures (S14).

This module consumes a neutral ``FailureClassification`` and decides the next
architectural action. It never invokes runtimes, selects models, or implements
adapter retries. Alternative selection remains an S13 responsibility; callers
pass only availability facts derived from the router.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from typing import TYPE_CHECKING

from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.domain.faults import FailureRecoverability

if TYPE_CHECKING:
    from agent_loop.domain.faults import FailureClassification


class RecoveryAction(StrEnum):
    """Next action allowed by the control-plane recovery policy."""

    RETRY_SAME_COMBINATION = "retry_same_combination"
    ESCALATE_MODEL = "escalate_model"
    ESCALATE_RUNTIME = "escalate_runtime"
    HUMAN_INTERVENTION = "human_intervention"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class RecoveryLimits:
    """Explicit deterministic bounds for automatic recovery."""

    max_attempts_per_combination: int = 1
    max_total_attempts: int = 3
    allow_model_escalation: bool = True
    allow_runtime_escalation: bool = True
    allow_human_intervention: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts_per_combination < 1:
            message = "RecoveryLimits.max_attempts_per_combination must be >= 1"
            raise ValueError(message)
        if self.max_total_attempts < 1:
            message = "RecoveryLimits.max_total_attempts must be >= 1"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class EscalationAvailability:
    """Alternative candidates already proven available by S13."""

    same_runtime_other_model: bool = False
    other_runtime: bool = False

    @property
    def has_any(self) -> bool:
        """Return whether any alternative combination exists."""
        return self.same_runtime_other_model or self.other_runtime


@dataclass(frozen=True, slots=True)
class RecoveryContext:
    """Attempt counters and alternative availability for one decision."""

    attempts_for_combination: int
    total_attempts: int
    alternatives: EscalationAvailability = field(default_factory=EscalationAvailability)

    def __post_init__(self) -> None:
        if self.attempts_for_combination < 1:
            message = "RecoveryContext.attempts_for_combination must be >= 1"
            raise ValueError(message)
        if self.total_attempts < 1:
            message = "RecoveryContext.total_attempts must be >= 1"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    """Explainable policy outcome for one classified failure."""

    action: RecoveryAction
    reason: str
    retry_exhausted: bool = False


def decide_recovery(
    classification: FailureClassification,
    *,
    context: RecoveryContext,
    limits: RecoveryLimits,
) -> RecoveryDecision:
    """Decide the next action for ``classification`` within ``limits``.

    Order of evaluation is stable:

    1. total attempt budget exhaustion;
    2. unknown recoverability (never retried);
    3. non-recoverable failures (terminal);
    4. possible side effects or unknown execution state (human);
    5. safe same-combination retry while its budget remains;
    6. model/runtime escalation from S13-provided availability;
    7. human intervention when no valid alternative remains.
    """
    stop = _early_stop(classification, context, limits)
    if stop is not None:
        return stop
    if classification.retry_safe and context.attempts_for_combination < limits.max_attempts_per_combination:
        return RecoveryDecision(
            action=RecoveryAction.RETRY_SAME_COMBINATION,
            reason=f"safe_retry:{classification.category.value}",
        )
    return _escalation_decision(classification.category, context, limits) or _stop(
        limits,
        reason=(
            "no_valid_alternative:"
            f"category={classification.category.value}:"
            f"alternatives={context.alternatives.has_any}"
        ),
    )


def _early_stop(
    classification: FailureClassification,
    context: RecoveryContext,
    limits: RecoveryLimits,
) -> RecoveryDecision | None:
    if context.total_attempts >= limits.max_total_attempts:
        return _stop(
            limits,
            reason=f"retry_exhausted:total_attempts={context.total_attempts}",
            exhausted=True,
        )
    if classification.recoverability is FailureRecoverability.UNKNOWN:
        return _stop(limits, reason=f"unknown_recoverability:{classification.category.value}")
    if classification.recoverability is FailureRecoverability.NON_RECOVERABLE:
        return RecoveryDecision(
            action=RecoveryAction.TERMINAL,
            reason=f"non_recoverable:{classification.category.value}",
        )
    if classification.effects_possible or classification.execution_state is ExecutionState.UNKNOWN:
        return _stop(
            limits,
            reason=(
                "automatic_recovery_unsafe:"
                f"state={classification.execution_state.value}:retry_safe={classification.retry_safe}"
            ),
        )
    return None


_RUNTIME_LEVEL_CATEGORIES = frozenset(
    {
        FailureCategory.RUNTIME_UNAVAILABLE,
        FailureCategory.RUNTIME_UNHEALTHY,
        FailureCategory.AUTHORIZATION_FAILURE,
    },
)


def _escalation_decision(
    category: FailureCategory,
    context: RecoveryContext,
    limits: RecoveryLimits,
) -> RecoveryDecision | None:
    runtime_first = category in _RUNTIME_LEVEL_CATEGORIES
    if runtime_first and context.alternatives.other_runtime and limits.allow_runtime_escalation:
        return RecoveryDecision(
            action=RecoveryAction.ESCALATE_RUNTIME,
            reason=f"escalate_runtime:{category.value}",
        )
    if context.alternatives.same_runtime_other_model and limits.allow_model_escalation:
        return RecoveryDecision(
            action=RecoveryAction.ESCALATE_MODEL,
            reason=f"escalate_model:{category.value}",
        )
    if context.alternatives.other_runtime and limits.allow_runtime_escalation:
        return RecoveryDecision(
            action=RecoveryAction.ESCALATE_RUNTIME,
            reason=f"escalate_runtime:{category.value}",
        )
    return None


def _stop(limits: RecoveryLimits, *, reason: str, exhausted: bool = False) -> RecoveryDecision:
    action = RecoveryAction.HUMAN_INTERVENTION if limits.allow_human_intervention else RecoveryAction.TERMINAL
    return RecoveryDecision(action=action, reason=reason, retry_exhausted=exhausted)
