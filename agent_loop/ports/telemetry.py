"""Outbound telemetry contract for Agent Loop observability (S23).

The control plane and other bounded execution paths publish immutable facts to
an injected ``TelemetrySink``. The contract is provider-neutral: it lives in
ports, imports no infrastructure, and never asks the domain to know about an
observability backend. Concrete sinks stay under ``agent_loop.adapters.telemetry``.

Events carry an execution correlation id and a monotonic per-execution sequence.
Optional facts are represented explicitly (``None``) instead of being invented:
an unknown cost is never encoded as zero, and inferred values must declare
``TelemetryProvenance.INFERRED``. No prompt text, repository content, tool
output, credential, token, or free-form provider message belongs in an event.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from typing import Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from agent_loop.domain.faults import ExecutionState
    from agent_loop.domain.faults import FailureCategory


class TelemetryEventName(StrEnum):
    """Stable event vocabulary emitted by Agent Loop (S23)."""

    WORK_ITEM_RECEIVED = "work_item_received"
    ROUTING_COMPLETED = "routing_completed"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    RETRY_STARTED = "retry_started"
    ESCALATION_STARTED = "escalation_started"
    TOOL_COMPLETED = "tool_completed"
    QUALITY_COMPLETED = "quality_completed"
    CHANGES_DETECTED = "changes_detected"
    WORK_ITEM_CLOSED = "work_item_closed"


class TelemetryProvenance(StrEnum):
    """Whether an event records an observed fact or an explicitly inferred one."""

    OBSERVED = "observed"
    INFERRED = "inferred"


class TelemetryCostSource(StrEnum):
    """Origin of a cost value, or explicit absence of one.

    ``DECLARED`` means the runtime/provider reported the cost. ``CALCULATED``
    is reserved for a caller applying an explicit pricing model. ``UNKNOWN``
    means no trustworthy cost exists and must never be confused with zero.
    """

    DECLARED = "declared"
    CALCULATED = "calculated"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TelemetryUsage:
    """Optional token/cost observation attached to one execution event.

    Every metric is independently optional. ``cost_source`` is the source of
    truth for cost: a known source requires ``cost_micros`` and ``UNKNOWN``
    requires it to be absent. Missing token metrics stay ``None``; they are
    never replaced with zero.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_micros: int | None = None
    cost_source: TelemetryCostSource = TelemetryCostSource.UNKNOWN

    def __post_init__(self) -> None:
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("reasoning_tokens", self.reasoning_tokens),
            ("cost_micros", self.cost_micros),
        ):
            if value is not None and value < 0:
                message = f"TelemetryUsage.{field_name} must be >= 0 when present"
                raise ValueError(message)
        if self.cost_source is TelemetryCostSource.UNKNOWN and self.cost_micros is not None:
            message = "TelemetryUsage.UNKNOWN cost source requires cost_micros=None"
            raise ValueError(message)
        if self.cost_source is not TelemetryCostSource.UNKNOWN and self.cost_micros is None:
            message = "TelemetryUsage known cost source requires cost_micros"
            raise ValueError(message)

    @property
    def cost_known(self) -> bool:
        """Return whether a cost value was observed (zero is a valid known cost)."""
        return self.cost_source is not TelemetryCostSource.UNKNOWN


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """One immutable, correlateable Agent Loop observation.

    ``occurred_at`` is the emission timestamp supplied by the recorder's clock.
    ``event_id`` is derived from the execution id and sequence, so event order
    and identity remain stable for a given execution. ``attributes`` contains
    bounded scalar facts only, already redacted by the recorder.
    """

    event_id: str
    execution_id: str
    sequence: int
    name: TelemetryEventName
    occurred_at: datetime
    provenance: TelemetryProvenance = TelemetryProvenance.OBSERVED
    target_repository: str | None = None
    work_item_id: str | None = None
    attempt: int | None = None
    runtime_id: str | None = None
    model_id: str | None = None
    session_ref: str | None = None
    status: str | None = None
    duration_ms: int | None = None
    failure_category: FailureCategory | None = None
    execution_state: ExecutionState | None = None
    usage: TelemetryUsage | None = None
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.event_id or not self.event_id.strip():
            message = "TelemetryEvent.event_id must be a non-empty string"
            raise ValueError(message)
        if not self.execution_id or not self.execution_id.strip():
            message = "TelemetryEvent.execution_id must be a non-empty string"
            raise ValueError(message)
        if self.sequence < 0:
            message = "TelemetryEvent.sequence must be >= 0"
            raise ValueError(message)
        if self.duration_ms is not None and self.duration_ms < 0:
            message = "TelemetryEvent.duration_ms must be >= 0 when present"
            raise ValueError(message)
        if self.occurred_at.tzinfo is None:
            message = "TelemetryEvent.occurred_at must be timezone-aware"
            raise ValueError(message)
        if self.attempt is not None and self.attempt < 1:
            message = "TelemetryEvent.attempt must be >= 1 when present"
            raise ValueError(message)


class TelemetrySink(Protocol):
    """Outbound port that accepts immutable telemetry events.

    Implementations must be best-effort: a failing sink must not change the
    outcome of the operation being observed. The canonical recorder also guards
    calls, so third-party sinks cannot break execution.
    """

    def emit(self, event: TelemetryEvent) -> None:
        """Accept one event without mutating it."""
