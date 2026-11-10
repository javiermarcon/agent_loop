"""Telemetry event construction, privacy redaction, and usage mapping (S23).

This package is the neutral observability layer between bounded execution paths
and an injected ``TelemetrySink``. It owns the deterministic event recorder,
defensive redaction, and conservative usage aggregation. It never executes
work, never selects runtimes/models, and never applies retry or escalation
policy.
"""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING

from agent_loop.ports.telemetry import TelemetryCostSource
from agent_loop.ports.telemetry import TelemetryEvent
from agent_loop.ports.telemetry import TelemetryEventName
from agent_loop.ports.telemetry import TelemetryProvenance
from agent_loop.ports.telemetry import TelemetryUsage

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable
    from collections.abc import Mapping

    from agent_loop.domain.faults import ExecutionFailure
    from agent_loop.ports.agent_runtime import AgentExecutionResult
    from agent_loop.ports.telemetry import TelemetrySink

_DEFAULT_MAX_EVENTS = 10_000
_DEFAULT_MAX_ATTRIBUTE_CHARS = 200
_MAX_ATTRIBUTES = 32
_REDACTED = "<redacted>"
_ELLIPSIS = "..."
_SECRET_PATTERNS = (
    (r"(?i)(https?://)[^/@\s]+@", rf"\1{_REDACTED}@"),
    (r"(?i)(authorization:\s*(?:bearer|basic)\s+)(\S+)", rf"\1{_REDACTED}"),
    (r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{8,})", rf"\1{_REDACTED}"),
    (
        r"(?i)((?:password|passwd|secret|token|api[_-]?key|access[_-]?key|credential)\s*[=:]\s*)(\S+)",
        rf"\1{_REDACTED}",
    ),
)


@dataclass(frozen=True, slots=True)
class TelemetryLimits:
    """Explicit bounds for one recorder's event volume and attribute size."""

    max_events: int = _DEFAULT_MAX_EVENTS
    max_attribute_chars: int = _DEFAULT_MAX_ATTRIBUTE_CHARS

    def __post_init__(self) -> None:
        if self.max_events < 0:
            message = "TelemetryLimits.max_events must be >= 0"
            raise ValueError(message)
        if self.max_attribute_chars < 1:
            message = "TelemetryLimits.max_attribute_chars must be >= 1"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class TelemetryFacts:
    """Optional facts supplied by a bounded execution path for one event."""

    provenance: TelemetryProvenance = TelemetryProvenance.OBSERVED
    work_item_id: str | None = None
    attempt: int | None = None
    runtime_id: str | None = None
    model_id: str | None = None
    session_ref: str | None = None
    status: str | None = None
    duration_ms: int | None = None
    failure: ExecutionFailure | None = None
    usage: TelemetryUsage | None = None
    attributes: Mapping[str, str] | None = None


def redact_sensitive(value: str) -> str:
    """Return ``value`` with common credential forms redacted.

    This is defense in depth: telemetry never records prompts, tool output, or
    free-form provider messages, but sinks may still receive caller-supplied
    attributes that accidentally contain a secret.
    """
    redacted = value
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = re.sub(pattern, replacement, redacted)
    return redacted


def bounded_text(value: str, limit: int) -> str:
    """Return a single-line, redacted and length-bounded scalar value."""
    collapsed = " ".join(value.splitlines())
    redacted = redact_sensitive(collapsed)
    if len(redacted) <= limit:
        return redacted
    if limit <= len(_ELLIPSIS):
        return redacted[:limit]
    return f"{redacted[: limit - len(_ELLIPSIS)]}{_ELLIPSIS}"


class TelemetryRecorder:
    """Build correlateable, bounded events and forward them best-effort.

    One recorder represents one execution identity. Event sequence numbers are
    monotonic per recorder and event ids derive from ``execution_id`` plus the
    sequence, so repeated runs never collide. A sink exception is swallowed:
    observability failures must not alter the observed operation. A recorder is
    synchronous and not thread-safe; use one recorder per execution.
    """

    def __init__(
        self,
        sink: TelemetrySink,
        *,
        execution_id: str,
        target_repository: str | None = None,
        clock: Callable[[], datetime] | None = None,
        limits: TelemetryLimits | None = None,
    ) -> None:
        if not execution_id or not execution_id.strip():
            message = "TelemetryRecorder.execution_id must be a non-empty string"
            raise ValueError(message)
        resolved_limits = limits or TelemetryLimits()
        self._sink = sink
        self._execution_id = execution_id
        self._target_repository = target_repository
        self._clock = clock or _utc_now
        self._max_events = resolved_limits.max_events
        self._max_attribute_chars = resolved_limits.max_attribute_chars
        self._sequence = 0

    @property
    def execution_id(self) -> str:
        """Return the correlation id shared by every event of this run."""
        return self._execution_id

    def record(
        self,
        name: TelemetryEventName,
        facts: TelemetryFacts | None = None,
    ) -> TelemetryEvent | None:
        """Emit one event, or return ``None`` when the event budget is exhausted.

        ``facts.failure`` contributes only its structured category and observed
        state. Its free-form ``detail`` and evidence are intentionally not
        recorded.
        """
        if self._sequence >= self._max_events:
            return None
        resolved = facts or TelemetryFacts()
        sequence = self._sequence
        self._sequence += 1
        event = TelemetryEvent(
            event_id=f"{self._execution_id}:{sequence}",
            execution_id=self._execution_id,
            sequence=sequence,
            name=name,
            occurred_at=self._clock(),
            provenance=resolved.provenance,
            target_repository=self._target_repository,
            work_item_id=resolved.work_item_id,
            attempt=resolved.attempt,
            runtime_id=resolved.runtime_id,
            model_id=resolved.model_id,
            session_ref=resolved.session_ref,
            status=resolved.status,
            duration_ms=resolved.duration_ms,
            failure_category=None if resolved.failure is None else resolved.failure.category,
            execution_state=None if resolved.failure is None else resolved.failure.execution_state,
            usage=resolved.usage,
            attributes=self._bounded_attributes(resolved.attributes),
        )
        with suppress(Exception):
            self._sink.emit(event)
        return event

    def _bounded_attributes(self, attributes: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
        if not attributes:
            return ()
        sanitized: dict[str, str] = {}
        for raw_key, raw_value in attributes.items():
            key = bounded_text(str(raw_key), self._max_attribute_chars)
            if not key:
                continue
            sanitized[key] = bounded_text(str(raw_value), self._max_attribute_chars)
            if len(sanitized) >= _MAX_ATTRIBUTES:
                break
        return tuple(sanitized.items())


def usage_from_result(result: AgentExecutionResult) -> TelemetryUsage | None:
    """Map one normalized runtime result to usage without inventing values.

    ``None`` means the runtime did not report usage at all. A present usage
    object preserves each optional metric separately. Reasoning tokens are read
    only when the adapter published the documented ``reasoning_tokens``
    harness-metadata key; otherwise they stay unknown.
    """
    if result.usage is None:
        return None
    cost = result.usage.cost_micros
    return TelemetryUsage(
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        reasoning_tokens=_metadata_int(result.harness_metadata, "reasoning_tokens"),
        cost_micros=cost,
        cost_source=TelemetryCostSource.DECLARED if cost is not None else TelemetryCostSource.UNKNOWN,
    )


def aggregate_usage(usages: Iterable[TelemetryUsage | None]) -> TelemetryUsage | None:
    """Sum usage only when every attempt reported that metric.

    A partial sum would look like a complete cost, so any attempt without a
    usage row makes the aggregate unknown. Metrics missing from some attempts
    stay ``None``. Cost source becomes ``UNKNOWN`` unless every attempt has a
    known cost.
    """
    items = tuple(usages)
    if not items or any(item is None for item in items):
        return None
    reported = [item for item in items if item is not None]
    all_costs_known = all(item.cost_known for item in reported)
    return TelemetryUsage(
        input_tokens=_sum_when_complete(item.input_tokens for item in reported),
        output_tokens=_sum_when_complete(item.output_tokens for item in reported),
        reasoning_tokens=_sum_when_complete(item.reasoning_tokens for item in reported),
        cost_micros=_sum_when_complete(item.cost_micros for item in reported) if all_costs_known else None,
        cost_source=_aggregate_cost_source(reported) if all_costs_known else TelemetryCostSource.UNKNOWN,
    )


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _metadata_int(pairs: tuple[tuple[str, str], ...], key: str) -> int | None:
    raw = dict(pairs).get(key)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _sum_when_complete(values: Iterable[int | None]) -> int | None:
    total = 0
    for item in values:
        if item is None:
            return None
        total += item
    return total


def _aggregate_cost_source(usages: list[TelemetryUsage]) -> TelemetryCostSource:
    if all(item.cost_source is TelemetryCostSource.DECLARED for item in usages):
        return TelemetryCostSource.DECLARED
    return TelemetryCostSource.CALCULATED
