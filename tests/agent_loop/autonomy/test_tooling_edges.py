"""Edge contracts for tooling discovery, adapters, loop and CLI reporting."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from agent_loop.autonomy.cli import _exit_code
from agent_loop.autonomy.cli import _json_dump
from agent_loop.autonomy.cli import _parse_environment
from agent_loop.autonomy.cli import main
from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.discovery.git import ChangeAttribution
from agent_loop.autonomy.discovery.git import GitAudit
from agent_loop.autonomy.discovery.git import GitBaseline
from agent_loop.autonomy.discovery.git import GitChange
from agent_loop.autonomy.discovery.git import GitState
from agent_loop.autonomy.discovery.tool_commands import DocumentedCommand
from agent_loop.autonomy.discovery.tool_config import _hook_name
from agent_loop.autonomy.discovery.tool_config import _load_pre_commit_data
from agent_loop.autonomy.discovery.tool_config import _pre_commit_hooks
from agent_loop.autonomy.discovery.tool_config import _read_text
from agent_loop.autonomy.discovery.tool_config import _repository_hooks
from agent_loop.autonomy.discovery.tool_config import _workspace_dir
from agent_loop.autonomy.discovery.tool_config import _workspace_file
from agent_loop.autonomy.discovery.tool_config import is_tool_not_required
from agent_loop.autonomy.discovery.tool_models import ToolDiscovery
from agent_loop.autonomy.discovery.tool_models import ToolInventory
from agent_loop.autonomy.discovery.tool_models import ToolKind
from agent_loop.autonomy.discovery.tool_models import ToolState
from agent_loop.autonomy.discovery.tools import SubprocessContainerProbe
from agent_loop.autonomy.discovery.tools import _container_context
from agent_loop.autonomy.discovery.tools import _container_python_tool
from agent_loop.autonomy.discovery.tools import _detect_compose_project
from agent_loop.autonomy.discovery.tools import _external_tool
from agent_loop.autonomy.discovery.tools import _probe_python
from agent_loop.autonomy.discovery.tools import _venv_executable
from agent_loop.autonomy.execution.loop import AutonomyRequest
from agent_loop.autonomy.execution.loop import _apply_git_audit_guard
from agent_loop.autonomy.execution.loop import _normalize_target_paths
from agent_loop.autonomy.execution.loop import _overall_status
from agent_loop.autonomy.execution.loop import _render_execution_environment
from agent_loop.autonomy.execution.loop import _short
from agent_loop.autonomy.execution.loop import _validation_message
from agent_loop.autonomy.execution.runner import ValidationReport
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.security import git_environment
from agent_loop.autonomy.tools.adapters import BanditAdapter
from agent_loop.autonomy.tools.adapters import BehaveAdapter
from agent_loop.autonomy.tools.adapters import DockerComposeAdapter
from agent_loop.autonomy.tools.adapters import MypyAdapter
from agent_loop.autonomy.tools.adapters import PreCommitAdapter
from agent_loop.autonomy.tools.adapters import PylintAdapter
from agent_loop.autonomy.tools.adapters import PytestAdapter
from agent_loop.autonomy.tools.adapters import RuffAdapter
from agent_loop.autonomy.tools.adapters import _container_tool_command
from agent_loop.autonomy.tools.adapters import _local_tool_command
from agent_loop.autonomy.tools.adapters import _matches_under
from agent_loop.autonomy.tools.adapters import _normalize_test_root
from agent_loop.autonomy.tools.adapters import _pending_only_as_skipped
from agent_loop.autonomy.tools.adapters import _safe_argument
from agent_loop.autonomy.tools.adapters import _tool_command
from agent_loop.autonomy.tools.base import FailureKind
from agent_loop.autonomy.tools.base import ToolContext
from agent_loop.autonomy.tools.base import ToolResult
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import CommandOutcome
from agent_loop.autonomy.tools.command import SubprocessCommandRunner
from agent_loop.autonomy.tools.command import _as_text
from agent_loop.autonomy.tools.command import _truncate
from agent_loop.autonomy.tools.failure import classify_exit_code
from agent_loop.autonomy.tools.failure import classify_outcome
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import git_executable
from tests.agent_loop.autonomy.support import make_adapter_context
from tests.agent_loop.autonomy.support import make_context
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write


def _inventory(context: ToolContext, entries: dict[ToolKind, ToolDiscovery]) -> ToolContext:
    current = {entry.kind: entry for entry in context.inventory.entries}
    current.update(entries)
    return replace(context, inventory=ToolInventory(entries=tuple(current.values())))


def _discovery(kind: ToolKind, **kwargs: object) -> ToolDiscovery:
    return ToolDiscovery(kind=kind, **kwargs)  # type: ignore[arg-type]


def test_tool_adapters_skip_when_no_focused_files(tmp_path: Path) -> None:
    """Focused adapters do not execute without changed Python files."""
    context = make_adapter_context(tmp_path, changed_files=("README.md",))
    runner = RecordingRunner()
    assert PreCommitAdapter().run(context, runner).status is ToolStatus.SKIPPED
    assert PylintAdapter().run(context, runner).status is ToolStatus.SKIPPED
    assert BanditAdapter().run(context, runner).status is ToolStatus.SKIPPED
    assert runner.calls == []


def test_tool_adapters_report_unavailable_target_interpreter(tmp_path: Path) -> None:
    """Configured tools without a usable target command are unavailable."""
    context = make_adapter_context(tmp_path)
    unavailable = _discovery(
        ToolKind.RUFF,
        state=ToolState.CONFIGURED_UNAVAILABLE,
        reason="missing",
        config_paths=("pyproject.toml",),
    )
    context = _inventory(context, {ToolKind.RUFF: unavailable})
    assert RuffAdapter().run(context, RecordingRunner()).status is ToolStatus.UNAVAILABLE
    test_context = make_adapter_context(tmp_path / "mypy")
    for kind, adapter in ((ToolKind.MYPY, MypyAdapter()), (ToolKind.PYLINT, PylintAdapter())):
        entry = _discovery(
            kind,
            state=ToolState.CONFIGURED_UNAVAILABLE,
            reason="missing",
            config_paths=("pyproject.toml",),
        )
        adapter_context = _inventory(test_context, {kind: entry})
        assert adapter.run(adapter_context, RecordingRunner()).status is ToolStatus.UNAVAILABLE
    bandit_context = make_adapter_context(tmp_path / "bandit", full=True)
    bandit_entry = _discovery(
        ToolKind.BANDIT,
        state=ToolState.CONFIGURED_UNAVAILABLE,
        reason="missing",
        config_paths=(".bandit",),
    )
    bandit_context = _inventory(bandit_context, {ToolKind.BANDIT: bandit_entry})
    assert BanditAdapter().run(bandit_context, RecordingRunner()).status is ToolStatus.UNAVAILABLE
    pytest_context = make_adapter_context(tmp_path / "pytest")
    pytest_entry = _discovery(
        ToolKind.PYTEST,
        state=ToolState.CONFIGURED_UNAVAILABLE,
        reason="missing",
        config_paths=("pyproject.toml",),
    )
    pytest_context = _inventory(pytest_context, {ToolKind.PYTEST: pytest_entry})
    assert PytestAdapter().run(pytest_context, RecordingRunner()).status is ToolStatus.UNAVAILABLE


def test_mypy_full_and_bandit_full_commands(tmp_path: Path) -> None:
    """Full mode analyzes the project root for Mypy and Bandit."""
    context = make_adapter_context(tmp_path, full=True)
    runner = RecordingRunner()
    MypyAdapter().run(context, runner)
    assert runner.calls[0][0][-1] == "."
    bandit_root = make_project(tmp_path / "bandit-repo")
    write(bandit_root / ".bandit", "skips: []\n")
    bandit_context = make_context(bandit_root, full=True)
    bandit_runner = RecordingRunner()
    BanditAdapter().run(bandit_context, bandit_runner)
    assert "-c" in bandit_runner.calls[0][0]
    assert "." in bandit_runner.calls[0][0]


def test_bandit_security_error_on_config_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An escaping .bandit path is ignored instead of aborting validation."""
    context = make_adapter_context(tmp_path, full=True)
    write(context.workspace.root / ".bandit", "skips: []\n")
    context = _inventory(
        context,
        {
            ToolKind.BANDIT: _discovery(
                ToolKind.BANDIT,
                state=ToolState.CONFIGURED_AVAILABLE,
                config_paths=(".bandit",),
            ),
        },
    )
    original = TargetWorkspace.resolve

    def _resolve(self: TargetWorkspace, candidate: object) -> Path:
        if str(candidate) == ".bandit":
            msg = "escape"
            raise __import__("agent_loop.autonomy.errors", fromlist=["SecurityError"]).SecurityError(msg)
        return original(self, candidate)  # type: ignore[arg-type]

    monkeypatch.setattr(TargetWorkspace, "resolve", _resolve)
    runner = RecordingRunner()
    BanditAdapter().run(context, runner)
    assert "-c" not in runner.calls[0][0]


def test_precommit_container_git_unavailable_is_skipped(tmp_path: Path) -> None:
    """Container pre-commit without Git metadata is skipped explicitly."""
    context = make_adapter_context(tmp_path)
    entry = _discovery(
        ToolKind.PRE_COMMIT,
        state=ToolState.CONFIGURED_AVAILABLE,
        config_paths=(".pre-commit-config.yaml",),
        command_prefix=("docker", "compose", "exec", "web"),
        container_service="web",
    )
    context = _inventory(context, {ToolKind.PRE_COMMIT: entry})
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("git",),
                cwd=str(context.workspace.root),
                exit_code=1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            ),
        ],
    )
    assert PreCommitAdapter().run(context, runner).status is ToolStatus.SKIPPED


def test_behave_skip_unavailable_pending_and_selection(tmp_path: Path) -> None:
    """Behave distinguishes absent features, unavailable interpreter and pending scenarios."""
    no_features = make_adapter_context(tmp_path / "nofeatures")
    entry = _discovery(
        ToolKind.BEHAVE,
        state=ToolState.CONFIGURED_AVAILABLE,
        config_paths=("behave.ini",),
        executable=str(no_features.environment.python_executable),
    )
    context = _inventory(no_features, {ToolKind.BEHAVE: entry})
    assert BehaveAdapter().run(context, RecordingRunner()).status is ToolStatus.SKIPPED

    features = make_adapter_context(tmp_path / "features", features=True)
    write(features.workspace.root / "features" / "x.feature", "Feature: x\n")
    unavailable = _discovery(
        ToolKind.BEHAVE,
        state=ToolState.CONFIGURED_UNAVAILABLE,
        config_paths=("behave.ini",),
        reason="missing",
    )
    context = _inventory(features, {ToolKind.BEHAVE: unavailable})
    assert BehaveAdapter().run(context, RecordingRunner()).status is ToolStatus.UNAVAILABLE

    pending = ToolResult(
        tool=ToolKind.BEHAVE,
        status=ToolStatus.FAIL,
        message="fail",
        exit_code=1,
        stdout="| 1 | x | NOT IMPLEMENTED |\n",
    )
    assert _pending_only_as_skipped(pending).status is ToolStatus.SKIPPED
    real_failure = replace(pending, stdout="1 failed\n| 1 | x | NOT IMPLEMENTED |\n")
    assert _pending_only_as_skipped(real_failure).status is ToolStatus.FAIL
    assert _pending_only_as_skipped(replace(pending, status=ToolStatus.PASS)).status is ToolStatus.PASS


def test_compose_adapter_error_and_missing_paths(tmp_path: Path) -> None:
    """Compose validation fails closed for configuration errors and missing binaries."""
    context = make_adapter_context(tmp_path, compose=True)
    project = replace(context.project, compose_error="broken")
    assert (
        DockerComposeAdapter().run(replace(context, project=project), RecordingRunner()).status
        is ToolStatus.UNAVAILABLE
    )
    project = replace(context.project, compose_error=None, compose_project_files=(), compose_files=())
    assert (
        DockerComposeAdapter().run(replace(context, project=project), RecordingRunner()).status is ToolStatus.SKIPPED
    )
    missing = _inventory(
        context,
        {
            ToolKind.DOCKER_COMPOSE: _discovery(
                ToolKind.DOCKER_COMPOSE,
                state=ToolState.NOT_CONFIGURED,
                reason="none",
            ),
        },
    )
    assert DockerComposeAdapter().run(missing, RecordingRunner()).status is ToolStatus.UNAVAILABLE
    entry = _discovery(
        ToolKind.DOCKER_COMPOSE,
        state=ToolState.CONFIGURED_AVAILABLE,
        config_paths=("docker-compose.yml",),
        command_prefix=("relative-docker",),
    )
    relative = _inventory(context, {ToolKind.DOCKER_COMPOSE: entry})
    assert DockerComposeAdapter().run(relative, RecordingRunner()).status is ToolStatus.UNAVAILABLE


def test_test_root_normalization_and_safe_argument(tmp_path: Path) -> None:
    """Absolute/escaping test roots are bounded and NUL argv entries rejected."""
    context = make_adapter_context(tmp_path)
    assert _normalize_test_root(context, str(context.workspace.root / "tests")) == Path("tests")
    assert _normalize_test_root(context, str(tmp_path / "outside")) is None
    assert _normalize_test_root(context, "../tests") is None
    assert _safe_argument("-q") == "./-q"
    with pytest.raises(Exception, match="NUL"):
        _safe_argument("\0bad")


def test_matches_under_security_error_returns_partial_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An escaping tests.py lookup keeps already discovered matches."""
    context = make_adapter_context(tmp_path)
    write(context.workspace.root / "tests" / "test_models.py", "def test_x():\n    pass\n")
    original = TargetWorkspace.resolve

    def _resolve(self: TargetWorkspace, candidate: object) -> Path:
        if str(candidate).endswith("tests.py"):
            msg = "escape"
            raise __import__("agent_loop.autonomy.errors", fromlist=["SecurityError"]).SecurityError(msg)
        return original(self, candidate)  # type: ignore[arg-type]

    monkeypatch.setattr(TargetWorkspace, "resolve", _resolve)
    assert _matches_under(context, Path("tests"), "models") == {"tests/test_models.py"}


def test_tool_command_local_and_container_branches(tmp_path: Path) -> None:
    """Command construction handles module/executable/container variants."""
    context = make_adapter_context(tmp_path)
    discovery = _discovery(
        ToolKind.RUFF,
        state=ToolState.CONFIGURED_AVAILABLE,
        config_paths=("pyproject.toml",),
        executable=None,
    )
    assert _local_tool_command(context, discovery, "ruff", ("check",)) == (
        str(context.environment.python_executable),
        "-m",
        "ruff",
        "check",
    )
    no_python = replace(context, environment=replace(context.environment, python_executable=None))
    assert _local_tool_command(no_python, discovery, "ruff", ()) is None
    container = _discovery(
        ToolKind.MYPY,
        state=ToolState.CONFIGURED_AVAILABLE,
        config_paths=("pyproject.toml",),
        command_prefix=("docker", "compose", "exec", "web"),
    )
    documented = DocumentedCommand(tool="mypy", args=("mypy", "src"), source="README.md")
    project = replace(context.project, documented_commands=(documented,))
    assert _container_tool_command(
        replace(context, project=project),
        container,
        ToolKind.MYPY,
        "mypy",
        (),
    ) == ("docker", "compose", "exec", "web", "mypy", "src")
    documented_ruff = DocumentedCommand(tool="ruff", args=("ruff", "check"), source="README.md")
    project = replace(context.project, documented_commands=(documented_ruff,))
    assert _container_tool_command(
        replace(context, project=project),
        _discovery(
            ToolKind.RUFF,
            state=ToolState.CONFIGURED_AVAILABLE,
            config_paths=("pyproject.toml",),
            command_prefix=("docker", "compose", "exec", "web"),
        ),
        ToolKind.RUFF,
        "ruff",
        ("--fix",),
    ) == ("docker", "compose", "exec", "web", "ruff", "check", "--fix")
    fallback = _discovery(
        ToolKind.RUFF,
        state=ToolState.CONFIGURED_AVAILABLE,
        config_paths=("pyproject.toml",),
        command_prefix=("docker", "compose", "exec", "web"),
        executable="custom-ruff",
    )
    assert _container_tool_command(context, fallback, ToolKind.RUFF, "ruff", ()) == (
        "docker",
        "compose",
        "exec",
        "web",
        "custom-ruff",
    )


def test_tool_command_available_but_unresolved_is_none(tmp_path: Path) -> None:
    """A configured tool with neither runnable executable nor interpreter is unavailable."""
    context = make_adapter_context(tmp_path)
    entry = _discovery(
        ToolKind.RUFF,
        state=ToolState.CONFIGURED_AVAILABLE,
        config_paths=("pyproject.toml",),
        executable=str(context.workspace.root / "missing"),
    )
    context = _inventory(context, {ToolKind.RUFF: entry})
    assert _tool_command(context, ToolKind.RUFF, "ruff", ("check",)) is None


def test_probe_helpers_and_compose_detection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Probe failures, compose project parsing and container context are explicit."""
    probe = SubprocessContainerProbe(timeout_seconds=1)
    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.tools._probe_python",
        lambda command, timeout: command[-1] == "script",
    )
    assert probe.is_available(("docker",), "ruff") is False
    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.tools.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("x")),
    )
    assert _probe_python(("python",), 1) is None
    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.tools.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=7, stdout="", stderr=""),
    )
    assert _probe_python(("python",), 1) is None
    monkeypatch.undo()

    workspace = TargetWorkspace.open(make_project(tmp_path / "repo", compose=True))
    project = SimpleNamespace(compose_error=None, compose_project_files=("docker-compose.yml",), compose_service="web")
    unavailable = _discovery(
        ToolKind.DOCKER_COMPOSE,
        state=ToolState.CONFIGURED_UNAVAILABLE,
        config_paths=("docker-compose.yml",),
    )
    assert _container_context(workspace, cast(Any, project), unavailable) is None  # noqa: TC006
    assert (
        _container_context(
            workspace,
            cast(Any, SimpleNamespace(compose_error="x", compose_project_files=(), compose_service=None)),  # noqa: TC006
            unavailable,
        )
        is None
    )

    for payload, expected in (
        (SimpleNamespace(returncode=1, stdout="", stderr=""), None),
        (SimpleNamespace(returncode=0, stdout="not-json", stderr=""), None),
        (SimpleNamespace(returncode=0, stdout="{}", stderr=""), None),
        (
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        "bad",
                        {"Name": 1, "ConfigFiles": 2},
                        {"Name": "p", "ConfigFiles": str(workspace.root / "docker-compose.yml")},
                    ],
                ),
                stderr="",
            ),
            "p",
        ),
    ):
        monkeypatch.setattr(
            "agent_loop.autonomy.discovery.tools.subprocess.run",
            lambda *args, _payload=payload, **kwargs: _payload,
        )
        assert _detect_compose_project("/bin/docker", workspace, ("docker-compose.yml",)) == expected


def test_container_python_tool_requires_probe() -> None:
    """Container execution without a probe context is a programming error."""
    request = SimpleNamespace(
        workspace=SimpleNamespace(),
        project=SimpleNamespace(),
        kind=ToolKind.RUFF,
        python_executable=None,
        probe=SimpleNamespace(),
        container=None,
        container_probe=None,
    )
    config = SimpleNamespace(paths=("pyproject.toml",), details=())
    with pytest.raises(RuntimeError, match="without a probe context"):
        _container_python_tool(request, config)  # type: ignore[arg-type]


def test_external_tool_not_configured_unavailable_and_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """External tool discovery distinguishes absent config, binary and probe failure."""
    workspace = TargetWorkspace.open(make_project(tmp_path / "repo"))
    config = SimpleNamespace(paths=(), details=())
    initial = _external_tool(workspace, ToolKind.GIT, cast(Any, config), "git", ("--version",))  # noqa: TC006
    assert initial.state is ToolState.NOT_CONFIGURED
    config = SimpleNamespace(paths=(".git",), details=())
    monkeypatch.setattr("agent_loop.autonomy.discovery.tools.find_system_executable", lambda *args, **kwargs: None)
    assert (
        _external_tool(workspace, ToolKind.GIT, cast(Any, config), "git", ("--version",)).state  # noqa: TC006
        is ToolState.CONFIGURED_UNAVAILABLE
    )
    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.tools.find_system_executable",
        lambda *args, **kwargs: Path("/bin/git"),
    )
    monkeypatch.setattr("agent_loop.autonomy.discovery.tools._probe_external", lambda *args: False)
    assert (
        _external_tool(workspace, ToolKind.GIT, cast(Any, config), "git", ("--version",)).state  # noqa: TC006
        is ToolState.CONFIGURED_UNAVAILABLE
    )
    assert _venv_executable(workspace, workspace.root / ".venv" / "bin", ToolKind.GIT) is None


def test_tool_config_precommit_edge_shapes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pre-commit detail parsing tolerates absent/invalid YAML and shapes."""
    workspace = TargetWorkspace.open(make_project(tmp_path / "repo", pre_commit=False))
    assert _pre_commit_hooks(workspace) == ()
    write(workspace.root / ".pre-commit-config.yaml", "repos: invalid\n")
    assert _pre_commit_hooks(workspace) == ()
    assert _repository_hooks("bad") == ()
    assert _repository_hooks({"repo": "local", "hooks": "bad"}) == ()
    assert _hook_name("local", "bad") is None
    assert _hook_name("local", {"id": 3}) is None
    assert _hook_name("local", {"id": "ruff"}) == "local:ruff"
    assert _hook_name(None, {"id": "ruff"}) == "ruff"
    assert _read_text(None) == ""
    assert _workspace_file(workspace, "missing") is None
    assert _workspace_dir(workspace, "missing") is None

    monkeypatch.setitem(__import__("sys").modules, "yaml", None)
    assert _load_pre_commit_data(workspace) is None
    monkeypatch.undo()
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: (_ for _ in ()).throw(OSError("x")))
    assert _load_pre_commit_data(workspace) is None


def test_tool_not_required_short_circuits_on_documented_command(tmp_path: Path) -> None:
    """A documented invocation makes a configured tool required."""
    workspace = TargetWorkspace.open(make_project(tmp_path / "repo", pre_commit=False))
    project = __import__(
        "agent_loop.autonomy.discovery.django_project",
        fromlist=["discover_django_project"],
    ).discover_django_project(workspace)
    documented = SimpleNamespace(tool=ToolKind.RUFF.value)
    project = replace(project, documented_commands=(documented,))
    assert is_tool_not_required(workspace, project, ToolKind.RUFF) is False


def test_loop_request_validation_and_target_normalization(tmp_path: Path) -> None:
    """Empty/mismatched requirements and empty target paths are rejected or removed."""
    workspace = TargetWorkspace.open(make_project(tmp_path / "repo"))
    config = AutonomyConfig()
    with pytest.raises(Exception, match="requirement must be non-empty"):
        AutonomyRequest(workspace=workspace, requirement=" ", config=config)
    source = __import__("agent_loop.autonomy.requirement", fromlist=["RequirementSource"]).RequirementSource.inline(
        "x",
    )
    with pytest.raises(Exception, match="does not match"):
        AutonomyRequest(workspace=workspace, requirement="y", config=config, requirement_source=source)
    request = AutonomyRequest(
        workspace=workspace,
        requirement="x",
        config=config,
        target_paths=("", ".", "apps/sample", "apps/sample/"),
    )
    assert request.target_paths == ("apps/sample",)
    assert _normalize_target_paths(workspace, ("", ".", "apps/sample")) == ("apps/sample",)


def test_loop_execution_environment_rendering_and_short() -> None:
    """Prompt context renders virtualenv, compose and documented commands."""
    project = SimpleNamespace(
        virtualenv=".venv",
        python_executable=".venv/bin/python",
        compose_service="web",
        compose_project_files=("compose.yaml",),
        documented_commands=(
            SimpleNamespace(tool="pytest", args=("python", "-m", "pytest", "tests with space"), source="README.md"),
        ),
    )
    rendered = _render_execution_environment(cast(Any, project))  # noqa: TC006
    assert "Local virtualenv" in rendered
    assert "docker compose -f compose.yaml exec -T web" in rendered
    assert '"tests with space"' in rendered
    empty = _render_execution_environment(
        cast(
            "Any",
            SimpleNamespace(virtualenv=None, compose_service=None, compose_project_files=(), documented_commands=()),
        ),
    )
    assert "No validated target execution environment" in empty
    assert _short("x" * 10, 5).endswith("[truncated]")


def test_loop_status_helpers_and_git_guard() -> None:
    """Aggregate status, messages and Git guard preserve uncertainty."""
    run = SimpleNamespace(status=ToolStatus.FAIL)
    assert _overall_status((run,), ()) is ToolStatus.FAIL  # type: ignore[arg-type]
    assert _overall_status((SimpleNamespace(status=ToolStatus.UNKNOWN),), ()) is ToolStatus.UNKNOWN  # type: ignore[arg-type]
    assert _overall_status((SimpleNamespace(status=ToolStatus.UNAVAILABLE),), ()) is ToolStatus.UNAVAILABLE  # type: ignore[arg-type]
    assert _overall_status((), ()) is ToolStatus.UNKNOWN
    report = ValidationReport(results=())
    assert _validation_message(report) == "validation status: unknown"
    skipped = ValidationReport(results=(ToolResult(tool=ToolKind.RUFF, status=ToolStatus.SKIPPED, message="skip"),))
    assert _validation_message(skipped) == "validation skipped; no effective evidence"
    passed = ValidationReport(results=(ToolResult(tool=ToolKind.RUFF, status=ToolStatus.PASS, message="ok"),))
    assert _validation_message(passed) == "validation passed"

    baseline = GitBaseline(state=GitState(True, error="baseline broken"), file_hashes=())  # noqa: FBT003
    audit = GitAudit(
        baseline=baseline,
        final_state=GitState(True, error="final broken"),  # noqa: FBT003
        changes=(GitChange("a", "modified", ChangeAttribution.UNDETERMINED),),
        unexpected_paths=("a",),
        head_changed=True,
        warnings=("warning",),
    )
    status, message = _apply_git_audit_guard(ToolStatus.PASS, audit)
    assert status is ToolStatus.UNKNOWN
    assert "baseline Git audit failed" in str(message)
    assert "final Git audit failed" in str(message)
    assert "execution changed paths" in str(message)
    clean = GitAudit(
        baseline=GitBaseline(state=GitState(True), file_hashes=()),  # noqa: FBT003
        final_state=GitState(True),  # noqa: FBT003
        changes=(),
        unexpected_paths=(),
    )
    assert _apply_git_audit_guard(ToolStatus.FAIL, clean) == (ToolStatus.FAIL, None)


def test_cli_error_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI maps quoting, discovery, OS and output errors to stable exit codes."""
    root = make_project(tmp_path / "repo")
    assert (
        main(["--repo", str(root), "--requirement", "x", "--agent-command", "'unterminated", "--discover-only"]) == 2
    )
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main(["--repo", str(plain), "--requirement", "x", "--discover-only"]) == 2
    non_django = tmp_path / "non-django"
    non_django.mkdir()
    import subprocess  # noqa: PLC0415

    subprocess.run(  # noqa: S603
        (git_executable(), "init", "-q"),
        cwd=non_django,
        check=True,
        env=git_environment(),
    )
    assert main(["--repo", str(non_django), "--requirement", "x", "--dry-run"]) == 3
    assert _parse_environment(["A=1", "B="]) == (("A", "1"), ("B", ""))
    assert _json_dump({"b": 1, "a": 2}).startswith("{\n")
    assert _exit_code(SimpleNamespace(status=ToolStatus.FAIL, validation_status=None)) == 4  # type: ignore[arg-type]

    output = tmp_path / "report.md"
    output.mkdir()
    result = __import__("agent_loop.autonomy.report", fromlist=["AutonomyResult"])
    SimpleNamespace(
        status=ToolStatus.PASS,
        validation_status=None,
        to_dict=dict,
    )
    # _emit requires a real report for rendering; use the discovery-only path through main instead.
    assert main(["--repo", str(root), "--requirement", "x", "--discover-only", "--output", str(output)]) == 2
    assert result is not None


def test_cli_oserror_during_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unexpected OS error is a usage failure, never a partial success."""
    root = make_project(tmp_path / "repo")
    monkeypatch.setattr(
        "agent_loop.autonomy.cli.AutonomyLoop.run",
        lambda self, request: (_ for _ in ()).throw(OSError("disk")),
    )
    assert main(["--repo", str(root), "--requirement", "x", "--dry-run"]) == 2
    assert "cannot complete the run" in capsys.readouterr().err


def test_command_runner_error_and_text_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Subprocess OS errors are structured; text helpers handle bytes and truncation."""
    runner = SubprocessCommandRunner(max_output_chars=10)
    monkeypatch.setattr(
        "agent_loop.autonomy.tools.command.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    outcome = runner.run(("probe",), cwd=tmp_path, env={})
    assert outcome.error == "denied"
    assert _as_text(b"text") == "text"
    assert _as_text(None) == ""
    assert _truncate("abcdefghij", 4).startswith("ab")


def test_failure_classification_edges() -> None:
    """Failure classification never retries unknown infrastructure errors."""
    assert (
        classify_outcome(
            ToolKind.RUFF,
            CommandOutcome(command=(), cwd="", exit_code=None, stdout="", stderr="", duration_seconds=0.0),
        )
        is FailureKind.INFRASTRUCTURE
    )
    assert classify_exit_code(ToolKind.PYTEST, 2, "ERROR collecting tests") is FailureKind.IMPLEMENTATION
    assert classify_exit_code(ToolKind.PYTEST, 2, "interrupted") is FailureKind.INFRASTRUCTURE
    assert classify_exit_code(ToolKind.PYTEST, 3) is FailureKind.INFRASTRUCTURE
    assert classify_exit_code(ToolKind.PRE_COMMIT, 2) is FailureKind.INFRASTRUCTURE
    assert classify_exit_code(ToolKind.RUFF, 2) is FailureKind.USAGE
    assert classify_exit_code(ToolKind.BEHAVE, 2) is FailureKind.CONFIGURATION
    assert classify_exit_code(ToolKind.PYLINT, 32) is FailureKind.USAGE
    assert classify_exit_code(ToolKind.GIT, 1) is FailureKind.INFRASTRUCTURE
