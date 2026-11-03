"""Run selected validation tools and aggregate their structured outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.discovery.tools import ToolState
from agent_loop.autonomy.tools.base import ToolContext
from agent_loop.autonomy.tools.base import ToolResult
from agent_loop.autonomy.tools.base import ToolStatus

if TYPE_CHECKING:
    from agent_loop.autonomy.tools.command import CommandRunner
    from agent_loop.autonomy.tools.registry import ToolRegistry


class RetryDecision(StrEnum):
    """Explicit retry policy for one validation report."""

    RETRY_FAILURE = "retry_failure"
    STOP_SUCCESS = "stop_success"
    STOP_SKIPPED = "stop_skipped"
    STOP_UNAVAILABLE = "stop_unavailable"
    STOP_UNKNOWN = "stop_unknown"
    STOP_NON_RETRYABLE = "stop_non_retryable"


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Aggregated result of one validation pass."""

    results: tuple[ToolResult, ...]

    @property
    def failed(self) -> bool:
        """Return whether any executed tool failed."""
        return any(result.status is ToolStatus.FAIL for result in self.results)

    @property
    def effective_results(self) -> tuple[ToolResult, ...]:
        """Return results that actually represent an executed validation."""
        return tuple(result for result in self.results if result.status is not ToolStatus.SKIPPED)

    @property
    def actionable_failures(self) -> tuple[ToolResult, ...]:
        """Return proven failures that a correction pass can address."""
        return tuple(result for result in self.results if result.status is ToolStatus.FAIL and result.retryable)

    @property
    def non_retryable_failures(self) -> tuple[ToolResult, ...]:
        """Return failures caused by usage, configuration or infrastructure."""
        return tuple(result for result in self.results if result.status is ToolStatus.FAIL and not result.retryable)

    @property
    def non_actionable_results(self) -> tuple[ToolResult, ...]:
        """Return results that a correction prompt must not present as code defects."""
        return tuple(
            result
            for result in self.results
            if result.status in {ToolStatus.SKIPPED, ToolStatus.UNAVAILABLE, ToolStatus.UNKNOWN}
            or (result.status is ToolStatus.FAIL and not result.retryable)
        )

    @property
    def successful(self) -> bool:
        """Return whether at least one effective validation passed and none failed.

        Skips are neutral only when at least one real validation passed. An
        empty report or an all-skipped report is never successful.
        """
        effective = self.effective_results
        return bool(effective) and all(result.status is ToolStatus.PASS for result in effective)

    @property
    def retry_decision(self) -> RetryDecision:
        """Return the explicit action for the orchestration loop."""
        status = self.status
        if status is ToolStatus.FAIL:
            return self._failure_retry_decision()
        if status is ToolStatus.PASS:
            return RetryDecision.STOP_SUCCESS
        if status is ToolStatus.SKIPPED:
            return RetryDecision.STOP_SKIPPED
        if status is ToolStatus.UNAVAILABLE:
            return RetryDecision.STOP_UNAVAILABLE
        return RetryDecision.STOP_UNKNOWN

    def _failure_retry_decision(self) -> RetryDecision:
        if self.non_retryable_failures:
            return RetryDecision.STOP_NON_RETRYABLE
        if self.actionable_failures:
            return RetryDecision.RETRY_FAILURE
        return RetryDecision.STOP_NON_RETRYABLE

    @property
    def retryable(self) -> bool:
        """Return whether the report contains a recoverable implementation failure."""
        return self.retry_decision is RetryDecision.RETRY_FAILURE

    @property
    def status(self) -> ToolStatus:
        """Return a deterministic aggregate status that preserves uncertainty."""
        statuses = {result.status for result in self.results}
        if not statuses:
            return ToolStatus.UNKNOWN
        if ToolStatus.FAIL in statuses:
            return ToolStatus.FAIL
        if ToolStatus.UNKNOWN in statuses:
            return ToolStatus.UNKNOWN
        if ToolStatus.UNAVAILABLE in statuses:
            return ToolStatus.UNAVAILABLE
        if statuses <= {ToolStatus.SKIPPED}:
            return ToolStatus.SKIPPED
        return ToolStatus.PASS

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "status": self.status.value,
            "successful": self.successful,
            "retryable": self.retryable,
            "retry_decision": self.retry_decision.value,
            "effective_count": len(self.effective_results),
            "actionable_failure_count": len(self.actionable_failures),
            "non_retryable_failure_count": len(self.non_retryable_failures),
            "non_actionable_count": len(self.non_actionable_results),
            "results": [result.to_dict() for result in self.results],
        }


_STATE_RESULTS: dict[ToolState, tuple[ToolStatus, str]] = {
    ToolState.NOT_CONFIGURED: (ToolStatus.SKIPPED, "tool is not configured"),
    ToolState.NOT_REQUIRED: (ToolStatus.SKIPPED, "tool is not required by the target project"),
    ToolState.CONFIGURED_UNAVAILABLE: (ToolStatus.UNAVAILABLE, "tool is configured but unavailable"),
    ToolState.UNKNOWN: (ToolStatus.UNKNOWN, "tool availability is unknown"),
}


class ValidationRunner:
    """Execute registered adapters against the target context."""

    def __init__(self, registry: ToolRegistry, runner: CommandRunner) -> None:
        self._registry = registry
        self._runner = runner

    def run(
        self,
        context: ToolContext,
        kinds: tuple[ToolKind, ...] | None = None,
    ) -> ValidationReport:
        """Run selected tools, preserving unavailable/unknown distinctions."""
        selected = kinds if kinds is not None else self._default_validation_kinds()
        results = tuple(self._run_one(context, kind) for kind in selected)
        return ValidationReport(results=results)

    def _default_validation_kinds(self) -> tuple[ToolKind, ...]:
        return tuple(kind for kind in self._registry.kinds() if kind is not ToolKind.GIT)

    def _run_one(self, context: ToolContext, kind: ToolKind) -> ToolResult:
        discovery = context.inventory.get(kind)
        if discovery is None:
            return ToolResult(
                tool=kind,
                status=ToolStatus.UNKNOWN,
                message="tool discovery is missing",
            )
        state_result = _STATE_RESULTS.get(discovery.state)
        if state_result is not None:
            status, default_message = state_result
            return ToolResult(
                tool=kind,
                status=status,
                message=discovery.reason or default_message,
            )
        adapter = self._registry.get(kind)
        if adapter is None:
            return ToolResult(
                tool=kind,
                status=ToolStatus.UNKNOWN,
                message="no adapter registered for tool",
            )
        return adapter.run(context, self._runner)
