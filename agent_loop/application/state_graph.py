"""Declarative transition graphs for durable agent-loop state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StateEntity(StrEnum):
    """Identify which entity a transition specification governs."""

    UNIT = "unit"
    RUN = "run"


class TransitionEvent(StrEnum):
    """Semantic events that legally drive state transitions."""

    UNIT_CREATED = "unit_created"
    UNIT_STARTED = "unit_started"
    AGENT_SUCCEEDED = "agent_succeeded"
    SCOPE_EVALUATED = "scope_evaluated"
    GATES_STARTED = "gates_started"
    GATES_PASSED = "gates_passed"
    UNIT_GREEN = "unit_green"
    REVIEW_REQUESTED = "review_requested"
    COMMIT_CREATED = "commit_created"
    CONTINUED_WITHOUT_COMMIT = "continued_without_commit"
    ACT_PASSED = "act_passed"
    ACT_FAILED = "act_failed"
    ACT_REPAIR_ACCEPTED = "act_repair_accepted"
    UNIT_FAILED = "unit_failed"
    UNIT_PAUSED = "unit_paused"
    UNIT_BLOCKED = "unit_blocked"
    UNIT_REJECTED = "unit_rejected"
    UNIT_SKIPPED = "unit_skipped"
    RETRY_REQUESTED = "retry_requested"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNIT_ADVANCED = "unit_advanced"
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_PAUSED = "run_paused"
    GATE_INVALIDATED = "gate_invalidated"


@dataclass(frozen=True)
class TransitionSpec:
    """Describe one legal transition edge."""

    entity: StateEntity
    event: TransitionEvent
    allowed_from: frozenset[str]
    target_status: str
    required_fields: frozenset[str] = frozenset()
    forbidden_fields: frozenset[str] = frozenset()
    terminal: bool = False


UNIT_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"ready", "skipped"}),
    "ready": frozenset({"running"}),
    "running": frozenset({"agent_completed", "gates_running", "paused", "failed", "rejected"}),
    "agent_completed": frozenset({"scope_review"}),
    "scope_review": frozenset({"gates_running", "awaiting_review", "blocked", "paused", "rejected"}),
    "gates_running": frozenset({"running", "green", "scope_review", "paused", "blocked", "failed"}),
    "green": frozenset({"awaiting_review"}),
    "awaiting_review": frozenset({"committed", "continued_without_commit", "paused"}),
    "committed": frozenset({"completed", "running"}),
    "paused": frozenset({"running", "scope_review", "gates_running", "awaiting_review"}),
    "failed": frozenset({"ready", "running", "gates_running", "paused", "budget_exhausted", "awaiting_review"}),
    "budget_exhausted": frozenset({"ready", "running", "paused"}),
    "blocked": frozenset({"ready", "running", "scope_review", "paused"}),
    "completed": frozenset(),
    "continued_without_commit": frozenset({"paused", "completed"}),
    "skipped": frozenset(),
    "rejected": frozenset(),
}

RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset(
        {
            "running",
            "cancelled",
            "paused",
            "awaiting_review",
            "completed",
            "failed",
            "gate_failed",
            "budget_exhausted",
            "waiting_user",
            "blocked",
            "aborted",
            "commit_pending",
        },
    ),
    "running": frozenset(
        {
            "awaiting_review",
            "paused",
            "budget_exhausted",
            "gate_failed",
            "failed",
            "completed",
            "aborted",
            "waiting_user",
            "recovering",
            "commit_pending",
            "blocked",
        },
    ),
    "awaiting_review": frozenset({"running", "paused", "aborted", "failed", "completed", "commit_pending"}),
    "paused": frozenset({"running", "awaiting_review", "aborted", "failed", "completed"}),
    "budget_exhausted": frozenset({"running", "awaiting_review", "paused", "aborted"}),
    "gate_failed": frozenset({"running", "recovering", "paused", "failed"}),
    "recovering": frozenset({"running", "waiting_user", "failed", "paused"}),
    "waiting_user": frozenset({"running", "paused", "aborted"}),
    "failed": frozenset({"recovering", "running", "paused", "completed"}),
    "completed": frozenset(),
    "aborted": frozenset({"running"}),
    "cancelled": frozenset(),
    "archived": frozenset(),
    "superseded": frozenset(),
    "corrupt": frozenset(),
    "rejected": frozenset({"running"}),
    "blocked": frozenset({"running", "paused"}),
    "configuration_failure": frozenset({"running", "failed"}),
    "retry_pending": frozenset({"running"}),
    "interrupted": frozenset({"running", "recovering"}),
    "abandoned": frozenset(),
    "commit_pending": frozenset({"running", "failed", "awaiting_review", "completed"}),
    "final_review_pending": frozenset({"completed", "running"}),
}

UNIT_TERMINAL_STATUSES = frozenset({"completed", "continued_without_commit", "skipped", "rejected"})
UNIT_PRETERMINAL_STATUSES = frozenset({"committed"})


def is_unit_transition_allowed(origin: str, target: str) -> bool:
    """Return whether a unit transition is declared."""
    return target in UNIT_TRANSITIONS.get(origin, frozenset())


def is_run_transition_allowed(origin: str, target: str) -> bool:
    """Return whether a run transition is declared."""
    return target in RUN_TRANSITIONS.get(origin, frozenset())


def assert_unit_transition_allowed(origin: str, target: str, *, unit_id: str) -> None:
    """Raise ``ValueError`` when a unit transition is undeclared."""
    if is_unit_transition_allowed(origin, target):
        return
    message = f"Transición inválida {origin} -> {target} para {unit_id}"
    raise ValueError(message)
