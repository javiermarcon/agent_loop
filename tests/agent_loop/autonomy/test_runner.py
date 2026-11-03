"""Validation runner aggregation tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.tools import ToolDiscovery
from agent_loop.autonomy.discovery.tools import ToolInventory
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.discovery.tools import ToolState
from agent_loop.autonomy.execution.runner import RetryDecision
from agent_loop.autonomy.execution.runner import ValidationReport
from agent_loop.autonomy.execution.runner import ValidationRunner
from agent_loop.autonomy.tools.base import FailureKind
from agent_loop.autonomy.tools.base import ToolContext
from agent_loop.autonomy.tools.base import ToolResult
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.registry import ToolRegistry
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_context
from tests.agent_loop.autonomy.support import make_project

if TYPE_CHECKING:
    from pathlib import Path

    from agent_loop.autonomy.tools.command import CommandRunner


class _PassAdapter:
    kind = ToolKind.RUFF

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        return ToolResult(tool=self.kind, status=ToolStatus.PASS, message="ok")


def _context_with_state(tmp_path: Path, state: ToolState) -> ToolContext:
    context = make_context(make_project(tmp_path / "repo"))
    inventory = ToolInventory(entries=(ToolDiscovery(kind=ToolKind.RUFF, state=state, reason="test"),))
    return ToolContext(
        workspace=context.workspace,
        project=context.project,
        environment=context.environment,
        inventory=inventory,
        changed_files=context.changed_files,
        full=context.full,
        config=context.config,
    )


def test_runner_executes_available_adapter(tmp_path: Path) -> None:
    """A configured and available tool reaches its adapter."""
    context = _context_with_state(tmp_path, ToolState.CONFIGURED_AVAILABLE)
    registry = ToolRegistry()
    registry.register(_PassAdapter())
    report = ValidationRunner(registry, RecordingRunner()).run(context, (ToolKind.RUFF,))
    assert report.results[0].status is ToolStatus.PASS
    assert report.status is ToolStatus.PASS
    assert report.successful is True


def test_runner_does_not_execute_missing_tools(tmp_path: Path) -> None:
    """Not-configured and unavailable tools are reported without execution."""
    registry = ToolRegistry()
    registry.register(_PassAdapter())
    not_configured = _context_with_state(tmp_path / "a", ToolState.NOT_CONFIGURED)
    not_required = _context_with_state(tmp_path / "c", ToolState.NOT_REQUIRED)
    unavailable = _context_with_state(tmp_path / "b", ToolState.CONFIGURED_UNAVAILABLE)
    runner = ValidationRunner(registry, RecordingRunner())
    assert runner.run(not_configured, (ToolKind.RUFF,)).status is ToolStatus.SKIPPED
    assert runner.run(not_required, (ToolKind.RUFF,)).status is ToolStatus.SKIPPED
    assert runner.run(unavailable, (ToolKind.RUFF,)).status is ToolStatus.UNAVAILABLE


def test_validation_report_aggregation_is_deterministic() -> None:
    """Fail dominates unknown, which dominates unavailable; skips are neutral."""
    fail = ToolResult(
        tool=ToolKind.RUFF,
        status=ToolStatus.FAIL,
        message="fail",
        failure_kind=FailureKind.IMPLEMENTATION,
    )
    usage_failure = ToolResult(
        tool=ToolKind.PYTEST,
        status=ToolStatus.FAIL,
        message="usage",
        failure_kind=FailureKind.USAGE,
    )
    unknown = ToolResult(tool=ToolKind.MYPY, status=ToolStatus.UNKNOWN, message="unknown")
    unavailable = ToolResult(tool=ToolKind.PYLINT, status=ToolStatus.UNAVAILABLE, message="unavailable")
    skipped = ToolResult(tool=ToolKind.BEHAVE, status=ToolStatus.SKIPPED, message="skipped")
    passed = ToolResult(tool=ToolKind.PYTEST, status=ToolStatus.PASS, message="pass")
    assert ValidationReport(()).status is ToolStatus.UNKNOWN
    assert ValidationReport(()).successful is False
    assert ValidationReport((fail, unknown)).status is ToolStatus.FAIL
    assert ValidationReport((unknown, unavailable)).status is ToolStatus.UNKNOWN
    assert ValidationReport((unavailable,)).status is ToolStatus.UNAVAILABLE
    assert ValidationReport((skipped,)).status is ToolStatus.SKIPPED
    assert ValidationReport((skipped,)).successful is False
    assert ValidationReport((passed, skipped)).status is ToolStatus.PASS
    assert ValidationReport((passed, skipped)).successful is True
    assert ValidationReport((passed, unknown)).status is ToolStatus.UNKNOWN
    assert ValidationReport((passed, unknown)).successful is False
    assert ValidationReport((passed, unavailable)).status is ToolStatus.UNAVAILABLE
    assert ValidationReport((passed, fail)).status is ToolStatus.FAIL
    assert ValidationReport((passed, fail)).successful is False
    assert ValidationReport((passed, fail)).retryable is True
    assert ValidationReport((passed, fail)).retry_decision is RetryDecision.RETRY_FAILURE
    assert ValidationReport((passed, usage_failure)).retryable is False
    assert ValidationReport((passed, usage_failure)).retry_decision is RetryDecision.STOP_NON_RETRYABLE
    assert ValidationReport((passed, usage_failure)).non_actionable_results == (usage_failure,)
    assert ValidationReport((passed, unknown)).retryable is False
    assert ValidationReport((passed, unknown)).retry_decision is RetryDecision.STOP_UNKNOWN
    assert ValidationReport((passed, skipped)).retry_decision is RetryDecision.STOP_SUCCESS
    assert ValidationReport((skipped,)).retry_decision is RetryDecision.STOP_SKIPPED
    assert ValidationReport((unavailable,)).retry_decision is RetryDecision.STOP_UNAVAILABLE
    assert ValidationReport(()).retry_decision is RetryDecision.STOP_UNKNOWN
