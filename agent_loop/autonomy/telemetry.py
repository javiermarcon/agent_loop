"""Telemetry mapping for the autonomous target-repository loop (S23).

The autonomy path already owns discovery, agent invocation, tool execution, and
Git attribution. This module translates those observed facts into the neutral
event schema without re-running any logic and without recording prompts, tool
output, command lines, repository file paths, or free-form messages.
"""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING
from uuid import uuid4

from agent_loop.ports.telemetry import TelemetryEventName
from agent_loop.telemetry.recorder import TelemetryFacts
from agent_loop.telemetry.recorder import TelemetryLimits
from agent_loop.telemetry.recorder import TelemetryRecorder

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from agent_loop.autonomy.discovery.git import GitAudit
    from agent_loop.autonomy.execution.agent import AgentRunResult
    from agent_loop.autonomy.execution.runner import ValidationReport
    from agent_loop.autonomy.report import AutonomyResult
    from agent_loop.autonomy.tools.base import ToolResult
    from agent_loop.ports.telemetry import TelemetrySink


class AutonomyTelemetry:
    """Emit correlated S23 events for one autonomous run."""

    def __init__(
        self,
        sink: TelemetrySink,
        *,
        target_repository: str,
        execution_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
        max_events: int = 10_000,
    ) -> None:
        self._recorder = TelemetryRecorder(
            sink,
            execution_id=(execution_id or "").strip() or uuid4().hex,
            target_repository=target_repository,
            clock=clock,
            limits=TelemetryLimits(max_events=max_events),
        )
        self._started = monotonic()

    @property
    def execution_id(self) -> str:
        """Return the correlation id shared by every event of this run."""
        return self._recorder.execution_id

    def run_received(self, requirement_origin: str) -> None:
        """Record that the autonomous run was accepted."""
        self._recorder.record(
            TelemetryEventName.WORK_ITEM_RECEIVED,
            TelemetryFacts(
                status="received",
                attributes={"requirement_origin": requirement_origin, "execution_kind": "autonomy"},
            ),
        )

    def agent_started(self, attempt: int) -> None:
        """Record the start of one external agent invocation."""
        self._recorder.record(
            TelemetryEventName.EXECUTION_STARTED,
            TelemetryFacts(
                attempt=attempt,
                runtime_id="agent-command",
                status="started",
                attributes={"execution_kind": "agent_command"},
            ),
        )

    def agent_finished(self, attempt: int, run: AgentRunResult) -> None:
        """Record the terminal state of one external agent invocation."""
        failed = not run.succeeded
        self._recorder.record(
            TelemetryEventName.EXECUTION_FAILED if failed else TelemetryEventName.EXECUTION_COMPLETED,
            TelemetryFacts(
                attempt=attempt,
                runtime_id="agent-command",
                status=run.status.value,
                duration_ms=_duration_ms(run.duration_seconds),
                attributes={
                    "execution_kind": "agent_command",
                    "exit_code": _optional_int(run.exit_code),
                    "prompt_bytes": str(run.prompt_bytes),
                    "prompt_transport": run.prompt_transport or "unknown",
                },
            ),
        )

    def tool_finished(self, attempt: int, result: ToolResult) -> None:
        """Record one observed validation-tool result, including non-executed states."""
        self._recorder.record(
            TelemetryEventName.TOOL_COMPLETED,
            TelemetryFacts(
                attempt=attempt,
                runtime_id="tool",
                status=result.status.value,
                duration_ms=_duration_ms(result.duration_seconds),
                attributes={
                    "tool": result.tool.value,
                    "exit_code": _optional_int(result.exit_code),
                    "failure_kind": result.failure_kind.value,
                    "invocation_present": str(result.invocation is not None).lower(),
                },
            ),
        )

    def quality_finished(self, attempt: int, report: ValidationReport) -> None:
        """Record one aggregated validation report without tool output."""
        self._recorder.record(
            TelemetryEventName.QUALITY_COMPLETED,
            TelemetryFacts(
                attempt=attempt,
                status=report.status.value,
                attributes={
                    "successful": str(report.successful).lower(),
                    "retryable": str(report.retryable).lower(),
                    "retry_decision": report.retry_decision.value,
                    "effective_count": str(len(report.effective_results)),
                    "actionable_failure_count": str(len(report.actionable_failures)),
                    "non_retryable_failure_count": str(len(report.non_retryable_failures)),
                    "non_actionable_count": str(len(report.non_actionable_results)),
                },
            ),
        )

    def changes_detected(self, audit: GitAudit) -> None:
        """Record attribution counts without repository file paths or warnings text."""
        self._recorder.record(
            TelemetryEventName.CHANGES_DETECTED,
            TelemetryFacts(
                status="recorded",
                attributes={
                    "changed_count": str(len(audit.changes)),
                    "execution_count": str(len(audit.execution_changes)),
                    "validation_count": str(len(audit.validation_changes)),
                    "preexisting_count": str(len(audit.preexisting_changes)),
                    "undetermined_count": str(len(audit.undetermined_changes)),
                    "unexpected_count": str(len(audit.unexpected_paths)),
                    "committed_count": str(len(audit.commit_paths)),
                    "head_changed": str(audit.head_changed).lower(),
                    "branch_changed": str(audit.branch_changed).lower(),
                    "warning_count": str(len(audit.warnings)),
                },
            ),
        )

    def run_closed(self, result: AutonomyResult) -> None:
        """Record the final observed autonomy outcome and wall-clock duration."""
        self._recorder.record(
            TelemetryEventName.WORK_ITEM_CLOSED,
            TelemetryFacts(
                status=result.status.value,
                duration_ms=_elapsed_ms(self._started),
                attributes={
                    "execution_kind": "autonomy",
                    "validation_status": (
                        "not_run" if result.validation_status is None else result.validation_status.value
                    ),
                    "agent_run_count": str(len(result.agent_runs)),
                    "validation_report_count": str(len(result.validation_reports)),
                    "changed_file_count": str(len(result.final_git.changed_files)),
                    "git_repository": str(result.final_git.is_repository).lower(),
                    "branch": result.final_git.branch or "unknown",
                    "head": result.final_git.head or "unknown",
                },
            ),
        )


def _duration_ms(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))


def _optional_int(value: int | None) -> str:
    return "unknown" if value is None else str(value)
