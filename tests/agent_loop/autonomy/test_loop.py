"""Autonomous loop orchestration tests."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

import pytest

from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.errors import DiscoveryError
from agent_loop.autonomy.errors import SecurityError
from agent_loop.autonomy.execution.agent import AgentInvoker
from agent_loop.autonomy.execution.agent import AgentRunResult
from agent_loop.autonomy.execution.loop import AutonomyLoop
from agent_loop.autonomy.execution.loop import AutonomyRequest
from agent_loop.autonomy.execution.loop import build_correction_prompt
from agent_loop.autonomy.execution.runner import ValidationReport
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.security import git_environment
from agent_loop.autonomy.tools.base import ToolContext
from agent_loop.autonomy.tools.base import ToolResult
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import CommandOutcome
from tests.agent_loop.autonomy.support import FakeProbe
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import git_executable
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Sequence
    from pathlib import Path

_ALL_MODULES = ("ruff", "mypy", "pylint", "bandit", "pytest", "behave", "pre_commit")
_ALL_MODULES_TRUE = dict.fromkeys(_ALL_MODULES, True)
_TWO_ITERATIONS = 2


@dataclass
class FakeAgent:
    """Deterministic agent invoker for loop tests."""

    statuses: list[ToolStatus] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str, context: ToolContext) -> AgentRunResult:
        """Record the prompt and return the next canned status."""
        self.prompts.append(prompt)
        status = self.statuses.pop(0) if self.statuses else ToolStatus.PASS
        return AgentRunResult(
            status=status,
            command=("fake-agent",),
            exit_code=0 if status is ToolStatus.PASS else 1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            message=f"fake agent {status.value}",
        )


def _request(  # noqa: PLR0913 - explicit test knobs are clearer than a config object
    tmp_path: Path,
    *,
    config: AutonomyConfig | None = None,
    tool_kinds: tuple[ToolKind, ...] | None = None,
    dry_run: bool = False,
    discover_only: bool = False,
    validate_only: bool = False,
    full_validation: bool = False,
    venv: bool = True,
    target_paths: tuple[str, ...] = (),
) -> tuple[AutonomyRequest, Path]:
    root = make_project(tmp_path / "repo", venv=venv)
    workspace = TargetWorkspace.open(root)
    return (
        AutonomyRequest(
            workspace=workspace,
            requirement="Add a health check endpoint",
            config=config or AutonomyConfig(agent_command=("fake-agent",)),
            tool_kinds=tool_kinds,
            dry_run=dry_run,
            discover_only=discover_only,
            validate_only=validate_only,
            full_validation=full_validation,
            target_paths=target_paths,
        ),
        root,
    )


def _commit_all(root: Path) -> None:
    environment = git_environment()
    subprocess.run(  # noqa: S603
        (git_executable(), "add", "-A"),
        cwd=root,
        check=True,
        env=environment,
    )
    subprocess.run(  # noqa: S603
        (git_executable(), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init"),
        cwd=root,
        check=True,
        env=environment,
    )


def _loop(
    config: AutonomyConfig,
    agent: AgentInvoker,
    runner: RecordingRunner,
    probe: FakeProbe | None = None,
) -> AutonomyLoop:
    return AutonomyLoop(
        config,
        command_runner=runner,
        agent_invoker=agent,
        probe=probe or FakeProbe(_ALL_MODULES_TRUE),
    )


def test_discover_only_returns_report_without_agent(tmp_path: Path) -> None:
    """Discovery-only mode never invokes the agent."""
    request, _ = _request(tmp_path, discover_only=True)
    agent = FakeAgent()
    result = _loop(request.config, agent, RecordingRunner()).run(request)
    assert result.status is ToolStatus.PASS
    assert result.validation_status is None
    assert result.message == "discovery complete"
    assert result.discovery.project.is_django is True
    assert agent.prompts == []


def test_dry_run_builds_prompt_with_requirement_and_instructions(tmp_path: Path) -> None:
    """Dry run renders applicable instructions and does not execute."""
    request, root = _request(tmp_path, dry_run=True)
    write(root / "AGENTS.md", "root project rules\n")
    agent = FakeAgent()
    result = _loop(request.config, agent, RecordingRunner()).run(request)
    assert result.status is ToolStatus.PASS
    assert result.validation_status is None
    assert "Add a health check endpoint" in result.prompt
    assert "root project rules" in result.prompt
    assert agent.prompts == []


def test_validate_only_runs_tools_without_agent(tmp_path: Path) -> None:
    """Validate-only mode runs the selected tool and skips the agent."""
    request, _ = _request(tmp_path, validate_only=True, tool_kinds=(ToolKind.PYTEST,))
    agent = FakeAgent()
    runner = RecordingRunner()
    result = _loop(request.config, agent, runner).run(request)
    assert result.status is ToolStatus.PASS
    assert result.validation_status is ToolStatus.PASS
    assert agent.prompts == []
    assert len(result.validation_reports) == 1
    assert result.validation_reports[0].results[0].tool is ToolKind.PYTEST
    assert runner.calls


def test_missing_agent_command_is_unavailable(tmp_path: Path) -> None:
    """Without an explicit agent command, normal mode fails closed."""
    request, _ = _request(tmp_path, config=AutonomyConfig())
    result = _loop(request.config, FakeAgent(), RecordingRunner()).run(request)
    assert result.status is ToolStatus.UNAVAILABLE
    assert "no agent command" in result.message


def test_agent_success_and_validation_pass(tmp_path: Path) -> None:
    """A successful agent and validation complete in one iteration."""
    request, _ = _request(tmp_path, tool_kinds=(ToolKind.PYTEST,))
    agent = FakeAgent([ToolStatus.PASS])
    runner = RecordingRunner()
    result = _loop(request.config, agent, runner).run(request)
    assert result.status is ToolStatus.PASS
    assert result.validation_status is ToolStatus.PASS
    assert result.message == "validation passed in 1 iteration(s)"
    assert len(agent.prompts) == 1
    assert "Add a health check endpoint" in agent.prompts[0]
    assert len(result.validation_reports) == 1


def test_validate_only_with_no_tools_is_unknown(tmp_path: Path) -> None:
    """Zero selected validations never produce PASS."""
    request, _ = _request(tmp_path, validate_only=True, tool_kinds=())
    result = _loop(request.config, FakeAgent(), RecordingRunner()).run(request)
    assert result.status is ToolStatus.UNKNOWN
    assert result.validation_status is ToolStatus.UNKNOWN
    assert result.validation_reports[0].status is ToolStatus.UNKNOWN
    assert result.validation_reports[0].successful is False


def test_all_skipped_validation_stops_without_retry(tmp_path: Path) -> None:
    """A skipped validation is not retried and is not treated as success."""
    request, _ = _request(tmp_path, tool_kinds=(ToolKind.BANDIT,))
    agent = FakeAgent([ToolStatus.PASS, ToolStatus.PASS])
    result = _loop(request.config, agent, RecordingRunner()).run(request)
    assert result.status is ToolStatus.SKIPPED
    assert result.validation_status is ToolStatus.SKIPPED
    assert len(agent.prompts) == 1
    assert len(result.validation_reports) == 1
    assert "skipped" in result.message


def test_unavailable_validation_stops_without_retry(tmp_path: Path) -> None:
    """An unavailable dependency is reported as unavailable, not as a retryable failure."""
    request, root = _request(tmp_path, tool_kinds=(ToolKind.BANDIT,), venv=False)
    write(root / ".bandit", "skips: []\n")
    agent = FakeAgent([ToolStatus.PASS, ToolStatus.PASS])
    result = _loop(request.config, agent, RecordingRunner()).run(request)
    assert result.status is ToolStatus.UNAVAILABLE
    assert result.validation_status is ToolStatus.UNAVAILABLE
    assert len(agent.prompts) == 1
    assert len(result.validation_reports) == 1


def test_unknown_validation_stops_without_retry(tmp_path: Path) -> None:
    """Uncertainty is preserved and does not consume the retry budget."""
    request, _ = _request(tmp_path, tool_kinds=(ToolKind.PYTEST,))
    agent = FakeAgent([ToolStatus.PASS, ToolStatus.PASS])
    probe = FakeProbe({**_ALL_MODULES_TRUE, "pytest": None})
    result = _loop(request.config, agent, RecordingRunner(), probe=probe).run(request)
    assert result.status is ToolStatus.UNKNOWN
    assert result.validation_status is ToolStatus.UNKNOWN
    assert len(agent.prompts) == 1
    assert len(result.validation_reports) == 1


def test_correction_prompt_only_asks_to_fix_proven_failures(tmp_path: Path) -> None:
    """Correction prompts separate actionable failures from non-actionable states."""
    request, _ = _request(tmp_path)
    report = ValidationReport(
        (
            ToolResult(tool=ToolKind.PYTEST, status=ToolStatus.FAIL, message="boom", stderr="boom"),
            ToolResult(tool=ToolKind.MYPY, status=ToolStatus.UNKNOWN, message="unknown"),
            ToolResult(tool=ToolKind.BEHAVE, status=ToolStatus.SKIPPED, message="skipped"),
        ),
    )
    prompt = build_correction_prompt(request, report, 2)
    assert "boom" in prompt
    assert "Non-actionable results" in prompt
    assert "unknown" in prompt
    assert "skipped" in prompt
    assert "Do not treat skipped, unavailable or unknown results as implementation failures." in prompt


def test_validation_failure_triggers_bounded_correction(tmp_path: Path) -> None:
    """A failed validation produces a correction prompt and a second attempt."""
    request, _ = _request(
        tmp_path,
        config=AutonomyConfig(agent_command=("fake-agent",), max_iterations=2),
        tool_kinds=(ToolKind.PYTEST,),
    )
    agent = FakeAgent([ToolStatus.PASS, ToolStatus.PASS])
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("pytest",),
                cwd=str(tmp_path),
                exit_code=1,
                stdout="",
                stderr="boom",
                duration_seconds=0.0,
            ),
        ],
    )
    result = _loop(request.config, agent, runner).run(request)
    assert result.status is ToolStatus.PASS
    assert len(agent.prompts) == _TWO_ITERATIONS
    assert "correction iteration 2" in agent.prompts[1]
    assert "boom" in agent.prompts[1]
    assert len(result.validation_reports) == _TWO_ITERATIONS


def test_agent_failure_stops_validation(tmp_path: Path) -> None:
    """An agent invocation failure stops the loop before validation."""
    request, _ = _request(tmp_path, tool_kinds=(ToolKind.PYTEST,))
    agent = FakeAgent([ToolStatus.FAIL])
    runner = RecordingRunner()
    result = _loop(request.config, agent, runner).run(request)
    assert result.status is ToolStatus.FAIL
    assert result.validation_status is None
    assert len(agent.prompts) == 1
    assert result.validation_reports == ()
    assert runner.calls == []


def test_non_django_repository_raises_discovery_error(tmp_path: Path) -> None:
    """Normal mode rejects a repository that is not a Django project."""
    root = make_project(tmp_path / "plain")
    for name in ("manage.py", "requirements.txt", "pyproject.toml"):
        (root / name).unlink()
    (root / "config" / "settings.py").unlink()
    workspace = TargetWorkspace.open(root)
    request = AutonomyRequest(
        workspace=workspace,
        requirement="Do work",
        config=AutonomyConfig(agent_command=("fake-agent",)),
    )
    with pytest.raises(DiscoveryError, match="Django"):
        _loop(request.config, FakeAgent(), RecordingRunner()).run(request)


def test_log_dir_persists_prompt_and_result(tmp_path: Path) -> None:
    """Explicit log directories receive prompt and result artifacts."""
    log_dir = tmp_path / "logs"
    request, _ = _request(
        tmp_path,
        config=AutonomyConfig(agent_command=("fake-agent",), log_dir=log_dir),
        discover_only=True,
    )
    _loop(request.config, FakeAgent(), RecordingRunner()).run(request)
    runs = list(log_dir.iterdir())
    assert len(runs) == 1
    assert (runs[0] / "prompt.md").is_file()
    assert (runs[0] / "result.json").is_file()


@dataclass
class MutatingAgent:
    """Agent double that writes one file inside the target workspace."""

    path: str = "agent_output.py"
    content: str = "agent = 1\n"
    contents: list[str] = field(default_factory=list)
    status: ToolStatus = ToolStatus.PASS
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str, context: ToolContext) -> AgentRunResult:
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self.contents) - 1)
        content = self.contents[index] if self.contents else self.content
        target = context.workspace.root / self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return AgentRunResult(
            status=self.status,
            command=("fake-agent",),
            exit_code=0 if self.status is ToolStatus.PASS else 1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            message=f"fake agent {self.status.value}",
        )


class BranchSwitchingAgent:
    """Agent double that changes the Git branch without changing files."""

    def invoke(self, prompt: str, context: ToolContext) -> AgentRunResult:
        del prompt
        subprocess.run(  # noqa: S603
            (git_executable(), "checkout", "-q", "-b", "agent-branch"),
            cwd=context.workspace.root,
            check=True,
            env=git_environment(),
        )
        return AgentRunResult(
            status=ToolStatus.PASS,
            command=("fake-agent",),
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            message="fake agent pass",
        )


class ValidatorMutatingRunner(RecordingRunner):
    """Runner double that simulates a formatter modifying a workspace file."""

    def __init__(
        self,
        path: str,
        content: str,
        *,
        outcomes: list[CommandOutcome] | None = None,
    ) -> None:
        super().__init__(outcomes=outcomes or [])
        self._path = path
        self._content = content

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int | None = None,
        stdin_text: str | None = None,
    ) -> CommandOutcome:
        outcome = super().run(
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            stdin_text=stdin_text,
        )
        target = cwd / self._path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._content, encoding="utf-8")
        return outcome


def test_agent_cycle_records_execution_changes_in_git_audit(tmp_path: Path) -> None:
    """The final report distinguishes agent changes from preexisting state."""
    request, root = _request(tmp_path, tool_kinds=(ToolKind.PYTEST,))
    _commit_all(root)
    write(root / "preexisting.py", "preexisting = 1\n")
    agent = MutatingAgent()
    result = _loop(request.config, agent, RecordingRunner()).run(request)
    assert result.status is ToolStatus.PASS
    assert result.git_audit is not None
    assert {change.path for change in result.git_audit.preexisting_changes} == {"preexisting.py"}
    assert {change.path for change in result.git_audit.execution_changes} == {"agent_output.py"}


def test_agent_cycle_reports_changes_outside_authorized_scope(tmp_path: Path) -> None:
    """Unexpected execution changes outside target paths are surfaced."""
    request, _ = _request(
        tmp_path,
        tool_kinds=(ToolKind.PYTEST,),
        target_paths=("apps/allowed",),
    )
    agent = MutatingAgent(path="secrets/outside.py")
    result = _loop(request.config, agent, RecordingRunner()).run(request)
    assert result.git_audit is not None
    assert result.git_audit.unexpected_paths == ("secrets/outside.py",)


def test_iteration_audits_preserve_the_same_baseline(tmp_path: Path) -> None:
    """Each agent execution is audited against one immutable baseline."""
    request, _ = _request(
        tmp_path,
        config=AutonomyConfig(agent_command=("fake-agent",), max_iterations=2),
        tool_kinds=(ToolKind.PYTEST,),
    )
    agent = MutatingAgent(contents=["version = 1\n", "version = 2\n"])
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("pytest",),
                cwd=str(tmp_path),
                exit_code=1,
                stdout="",
                stderr="boom",
                duration_seconds=0.0,
            ),
        ],
    )
    result = _loop(request.config, agent, runner).run(request)
    assert result.status is ToolStatus.PASS
    assert len(result.iteration_audits) == _TWO_ITERATIONS
    assert result.iteration_audits[0].baseline.state.head == result.iteration_audits[1].baseline.state.head
    assert result.git_audit is not None
    assert result.git_audit.baseline.state.head == result.iteration_audits[0].baseline.state.head
    assert "agent_output.py" in {change.path for change in result.iteration_audits[0].execution_changes}
    assert "agent_output.py" in {change.path for change in result.iteration_audits[1].execution_changes}


def test_failed_agent_execution_is_still_audited(tmp_path: Path) -> None:
    """A failed agent run can still have modified the workspace."""
    request, _ = _request(tmp_path, tool_kinds=(ToolKind.PYTEST,))
    agent = MutatingAgent(status=ToolStatus.FAIL)
    result = _loop(request.config, agent, RecordingRunner()).run(request)
    assert result.status is ToolStatus.FAIL
    assert result.validation_status is None
    assert len(result.iteration_audits) == 1
    assert "agent_output.py" in {change.path for change in result.iteration_audits[0].execution_changes}


def test_validate_only_attributes_changes_to_the_validator_window(tmp_path: Path) -> None:
    """Validate-only mode still records what the validator changed."""
    request, _ = _request(tmp_path, validate_only=True, tool_kinds=(ToolKind.PYTEST,))
    runner = ValidatorMutatingRunner("validator_output.py", "formatted = 1\n")
    result = _loop(request.config, FakeAgent(), runner).run(request)
    assert result.git_audit is not None
    assert {change.path for change in result.git_audit.validation_changes} == {"validator_output.py"}
    assert result.git_audit.execution_changes == ()
    assert len(result.validation_audits) == 1


def test_branch_change_prevents_a_false_clean_pass(tmp_path: Path) -> None:
    """A branch switch is surfaced even when validation itself passes."""
    request, root = _request(tmp_path, tool_kinds=(ToolKind.PYTEST,))
    _commit_all(root)
    result = _loop(request.config, BranchSwitchingAgent(), RecordingRunner()).run(request)
    assert result.status is ToolStatus.UNKNOWN
    assert result.validation_status is ToolStatus.PASS
    assert result.git_audit is not None
    assert result.git_audit.branch_changed is True
    assert "git attribution requires review" in result.message


def test_validator_changes_are_attributed_separately_from_the_agent(tmp_path: Path) -> None:
    """A formatter window is distinguishable from the agent window."""
    request, _ = _request(tmp_path, tool_kinds=(ToolKind.PYTEST,))
    agent = MutatingAgent()
    runner = ValidatorMutatingRunner("validator_output.py", "formatted = 1\n")
    result = _loop(request.config, agent, runner).run(request)
    assert result.git_audit is not None
    assert {change.path for change in result.git_audit.execution_changes} == {"agent_output.py"}
    assert {change.path for change in result.git_audit.validation_changes} == {"validator_output.py"}
    assert result.validation_audits
    assert {change.path for change in result.validation_audits[0].execution_changes} == {"validator_output.py"}


def test_failing_validator_changes_are_still_attributed(tmp_path: Path) -> None:
    """A validator that fails after modifying files still exposes its window."""
    request, _ = _request(
        tmp_path,
        config=AutonomyConfig(agent_command=("fake-agent",), max_iterations=1),
        tool_kinds=(ToolKind.PYTEST,),
    )
    failure = CommandOutcome(
        command=("pytest",),
        cwd=str(tmp_path),
        exit_code=1,
        stdout="",
        stderr="boom",
        duration_seconds=0.0,
    )
    agent = MutatingAgent()
    runner = ValidatorMutatingRunner("validator_output.py", "formatted = 1\n", outcomes=[failure])
    result = _loop(request.config, agent, runner).run(request)
    assert result.status is ToolStatus.FAIL
    assert result.git_audit is not None
    assert {change.path for change in result.git_audit.validation_changes} == {"validator_output.py"}


def test_validator_without_changes_has_an_empty_window(tmp_path: Path) -> None:
    """A non-mutating validator does not fabricate attribution evidence."""
    request, _ = _request(tmp_path, tool_kinds=(ToolKind.PYTEST,))
    result = _loop(request.config, MutatingAgent(), RecordingRunner()).run(request)
    assert result.validation_audits
    assert result.validation_audits[0].execution_changes == ()


def test_agent_and_validator_touching_the_same_path_is_undetermined(tmp_path: Path) -> None:
    """Overlapping windows cannot be reduced to a single owner."""
    request, _ = _request(tmp_path, tool_kinds=(ToolKind.PYTEST,))
    agent = MutatingAgent(contents=["agent = 1\n"])
    runner = ValidatorMutatingRunner("agent_output.py", "formatted = 2\n")
    result = _loop(request.config, agent, runner).run(request)
    assert result.git_audit is not None
    change = next(change for change in result.git_audit.changes if change.path == "agent_output.py")
    assert change.attribution.value == "undetermined"
    assert result.git_audit.execution_changes == ()
    assert result.git_audit.validation_changes == ()


def test_request_normalizes_equivalent_target_paths(tmp_path: Path) -> None:
    """All equivalent in-workspace target paths collapse to one POSIX form."""
    root = make_project(tmp_path / "repo")
    workspace = TargetWorkspace.open(root)
    request = AutonomyRequest(
        workspace=workspace,
        requirement="Normalize scope",
        config=AutonomyConfig(agent_command=("fake-agent",)),
        target_paths=(
            "apps/sample",
            "./apps/sample",
            "apps/sample/",
            str(root / "apps" / "sample"),
        ),
    )
    assert request.target_paths == ("apps/sample",)
    with pytest.raises(SecurityError, match="escapes"):
        AutonomyRequest(
            workspace=workspace,
            requirement="Escape scope",
            config=AutonomyConfig(agent_command=("fake-agent",)),
            target_paths=("../outside",),
        )
    with pytest.raises(SecurityError, match="escapes"):
        AutonomyRequest(
            workspace=workspace,
            requirement="Absolute escape",
            config=AutonomyConfig(agent_command=("fake-agent",)),
            target_paths=(str(tmp_path / "outside"),),
        )
