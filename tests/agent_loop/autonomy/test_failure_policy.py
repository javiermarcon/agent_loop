"""Retry policy tests for code, usage, configuration and infrastructure failures."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.execution.agent import AgentRunResult
from agent_loop.autonomy.execution.loop import AutonomyLoop
from agent_loop.autonomy.execution.loop import AutonomyRequest
from agent_loop.autonomy.execution.runner import RetryDecision
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.tools.base import FailureKind
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import CommandOutcome
from agent_loop.autonomy.tools.failure import classify_exit_code
from tests.agent_loop.autonomy.support import FakeProbe
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_project

if TYPE_CHECKING:
    from pathlib import Path

    from agent_loop.autonomy.report import AutonomyResult
    from agent_loop.autonomy.tools.base import ToolContext

_TWO_ITERATIONS = 2
_ALL_MODULES = dict.fromkeys(("ruff", "mypy", "pylint", "bandit", "pytest", "behave", "pre_commit"), True)


@dataclass
class _FakeAgent:
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str, context: ToolContext) -> AgentRunResult:
        del context
        self.prompts.append(prompt)
        return AgentRunResult(
            status=ToolStatus.PASS,
            command=("fake-agent",),
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            message="fake agent pass",
        )


def _request(tmp_path: Path, *, venv: bool = True) -> AutonomyRequest:
    root = make_project(tmp_path / "repo", venv=venv)
    return AutonomyRequest(
        workspace=TargetWorkspace.open(root),
        requirement="Fix the failing test",
        config=AutonomyConfig(agent_command=("fake-agent",), max_iterations=2),
        tool_kinds=(ToolKind.PYTEST,),
    )


def _run(request: AutonomyRequest, outcomes: list[CommandOutcome]) -> tuple[AutonomyResult, _FakeAgent]:
    agent = _FakeAgent()
    loop = AutonomyLoop(
        request.config,
        command_runner=RecordingRunner(outcomes=outcomes),
        agent_invoker=agent,
        probe=FakeProbe(_ALL_MODULES),
    )
    return loop.run(request), agent


def _failure(exit_code: int, *, stdout: str = "", stderr: str = "", timed_out: bool = False) -> CommandOutcome:
    return CommandOutcome(
        command=("pytest",),
        cwd=".",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.0,
        timed_out=timed_out,
        error="command timed out" if timed_out else None,
    )


def test_assertion_failure_is_retryable() -> None:
    """A real test failure is an implementation defect."""
    assert classify_exit_code(ToolKind.PYTEST, 1) is FailureKind.IMPLEMENTATION


def test_collection_error_is_retryable() -> None:
    """A collection error can be fixed in the code."""
    kind = classify_exit_code(ToolKind.PYTEST, 2, "ERROR collecting tests/test_bad.py")
    assert kind is FailureKind.IMPLEMENTATION


def test_usage_error_is_not_retryable() -> None:
    """pytest exit 4 indicates invocation/config usage, not a code defect."""
    kind = classify_exit_code(ToolKind.PYTEST, 4, "unrecognized arguments: --bad-option")
    assert kind is FailureKind.USAGE
    assert kind.retryable is False


def test_test_not_found_is_usage_error() -> None:
    """A missing test target is an invocation problem."""
    kind = classify_exit_code(ToolKind.PYTEST, 4, "file or directory not found: missing")
    assert kind is FailureKind.USAGE


def test_interrupted_and_internal_errors_are_infrastructure() -> None:
    """Interrupts and pytest internal errors are not code defects."""
    assert classify_exit_code(ToolKind.PYTEST, 2, "Interrupted") is FailureKind.INFRASTRUCTURE
    assert classify_exit_code(ToolKind.PYTEST, 3) is FailureKind.INFRASTRUCTURE


def test_other_tool_usage_and_config_errors_are_not_retryable() -> None:
    """Ruff, Mypy, Pylint and Bandit distinguish usage/config exit codes."""
    assert classify_exit_code(ToolKind.RUFF, 1) is FailureKind.IMPLEMENTATION
    assert classify_exit_code(ToolKind.RUFF, 2) is FailureKind.USAGE
    assert classify_exit_code(ToolKind.MYPY, 2) is FailureKind.CONFIGURATION
    assert classify_exit_code(ToolKind.PYLINT, 32) is FailureKind.USAGE
    assert classify_exit_code(ToolKind.BANDIT, 2) is FailureKind.USAGE


def test_usage_failure_stops_after_one_iteration(tmp_path: Path) -> None:
    """A pytest usage error does not trigger a correction prompt."""
    request = _request(tmp_path)
    result, agent = _run(request, [_failure(4, stderr="error: unrecognized arguments: --bad")])
    assert result.status is ToolStatus.FAIL
    assert result.validation_reports[0].retry_decision is RetryDecision.STOP_NON_RETRYABLE
    assert len(agent.prompts) == 1
    assert "correction iteration" not in agent.prompts[0]


def test_assertion_failure_triggers_one_bounded_retry(tmp_path: Path) -> None:
    """A test assertion failure produces exactly one correction prompt."""
    request = _request(tmp_path)
    result, agent = _run(request, [_failure(1, stdout="1 failed", stderr="assert 1 == 2")])
    assert result.status is ToolStatus.PASS
    assert len(agent.prompts) == _TWO_ITERATIONS
    assert "correction iteration 2" in agent.prompts[1]
    assert "assert 1 == 2" in agent.prompts[1]


def test_collection_error_triggers_correction(tmp_path: Path) -> None:
    """A collection error is actionable and retried once."""
    request = _request(tmp_path)
    result, agent = _run(request, [_failure(2, stdout="ERROR collecting tests/test_bad.py")])
    assert result.status is ToolStatus.PASS
    assert len(agent.prompts) == _TWO_ITERATIONS


def test_infrastructure_failure_is_not_retried(tmp_path: Path) -> None:
    """A pytest internal error is reported without a correction prompt."""
    request = _request(tmp_path)
    result, agent = _run(request, [_failure(3, stderr="internal error")])
    assert result.status is ToolStatus.FAIL
    assert result.validation_reports[0].retry_decision is RetryDecision.STOP_NON_RETRYABLE
    assert len(agent.prompts) == 1


def test_timeout_is_unknown_and_not_retried(tmp_path: Path) -> None:
    """A timeout is indeterminate evidence and stops the loop."""
    request = _request(tmp_path)
    result, agent = _run(request, [_failure(1, timed_out=True)])
    assert result.status is ToolStatus.UNKNOWN
    assert result.validation_reports[0].retry_decision is RetryDecision.STOP_UNKNOWN
    assert len(agent.prompts) == 1


def test_missing_interpreter_is_unavailable_and_not_retried(tmp_path: Path) -> None:
    """A missing target interpreter is an environment problem."""
    request = _request(tmp_path, venv=False)
    result, agent = _run(request, [])
    assert result.status is ToolStatus.UNAVAILABLE
    assert result.validation_reports[0].retry_decision is RetryDecision.STOP_UNAVAILABLE
    assert len(agent.prompts) == 1
