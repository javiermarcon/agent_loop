"""Container-backed adapter command construction tests."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.tool_commands import DocumentedCommand
from agent_loop.autonomy.discovery.tools import ToolDiscovery
from agent_loop.autonomy.discovery.tools import ToolInventory
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.discovery.tools import ToolState
from agent_loop.autonomy.tools.adapters import BehaveAdapter
from agent_loop.autonomy.tools.adapters import MypyAdapter
from agent_loop.autonomy.tools.adapters import PreCommitAdapter
from agent_loop.autonomy.tools.adapters import PytestAdapter
from agent_loop.autonomy.tools.adapters import RuffAdapter
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import CommandOutcome
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_adapter_context

if TYPE_CHECKING:
    from pathlib import Path

_PREFIX = ("/usr/bin/docker", "compose", "-p", "fixture", "-f", "docker-compose.local.yml", "exec", "-T", "django")


def _container(kind: ToolKind) -> ToolDiscovery:
    return ToolDiscovery(
        kind=kind,
        state=ToolState.CONFIGURED_AVAILABLE,
        executable="python",
        command_prefix=_PREFIX,
        container_service="django",
    )


def _context(
    tmp_path: Path,
    *kinds: ToolKind,
    commands: tuple[DocumentedCommand, ...] = (),
    features: bool = False,
):
    context = make_adapter_context(tmp_path, features=features)
    inventory = ToolInventory(entries=tuple(_container(kind) for kind in kinds))
    project = replace(context.project, documented_commands=commands)
    return replace(context, inventory=inventory, project=project)


def test_ruff_runs_inside_the_discovered_service(tmp_path: Path) -> None:
    """Generic container tools use the service prefix plus the module invocation."""
    context = _context(tmp_path, ToolKind.RUFF)
    runner = RecordingRunner()
    RuffAdapter().run(context, runner)
    assert runner.calls[0][0] == (*_PREFIX, "python", "-m", "ruff", "check", "apps/sample/models.py")


def test_pre_commit_runs_inside_the_discovered_service(tmp_path: Path) -> None:
    """Pre-commit keeps focused file selection inside the container."""
    context = _context(tmp_path, ToolKind.PRE_COMMIT)
    runner = RecordingRunner()
    result = PreCommitAdapter().run(context, runner)
    assert runner.calls[0][0] == (*_PREFIX, "git", "rev-parse", "--is-inside-work-tree")
    assert runner.calls[1][0] == (
        *_PREFIX,
        "python",
        "-m",
        "pre_commit",
        "run",
        "--files",
        "apps/sample/models.py",
    )
    assert result.status is ToolStatus.PASS


def test_pre_commit_is_skipped_when_container_has_no_git_metadata(tmp_path: Path) -> None:
    """Pre-commit is not executable when the container cannot see the worktree Git metadata."""
    context = _context(tmp_path, ToolKind.PRE_COMMIT)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("git",),
                cwd=str(context.workspace.root),
                exit_code=128,
                stdout="",
                stderr="not a git repository",
                duration_seconds=0.0,
            ),
        ],
    )
    result = PreCommitAdapter().run(context, runner)
    assert result.status is ToolStatus.SKIPPED
    assert "Git metadata" in result.message
    assert len(runner.calls) == 1


def test_documented_mypy_command_is_used_verbatim(tmp_path: Path) -> None:
    """The project's documented mypy command wins over an invented equivalent."""
    command = DocumentedCommand(
        tool="mypy",
        args=("mypy", "--explicit-package-bases", "/app"),
        source="TESTING.md",
        service="django",
    )
    context = _context(tmp_path, ToolKind.MYPY, commands=(command,))
    runner = RecordingRunner()
    MypyAdapter().run(context, runner)
    assert runner.calls[0][0] == (*_PREFIX, "mypy", "--explicit-package-bases", "/app")
    assert "--explicit-package-bases" in runner.calls[0][0]


def test_documented_pytest_command_keeps_selection(tmp_path: Path) -> None:
    """A documented pytest entry point receives the computed selection."""
    command = DocumentedCommand(tool="pytest", args=("pytest",), source="justfile", service="django")
    context = _context(tmp_path, ToolKind.PYTEST, commands=(command,))
    runner = RecordingRunner()
    PytestAdapter().run(context, runner)
    assert runner.calls[0][0] == (*_PREFIX, "pytest")


def test_documented_behave_command_keeps_settings_and_targets(tmp_path: Path) -> None:
    """Behave uses manage.py with the project settings plus feature targets."""
    command = DocumentedCommand(
        tool="behave",
        args=("python", "manage.py", "behave", "--settings=config.settings.behave"),
        source="TESTING.md",
        service="django",
    )
    context = _context(tmp_path, ToolKind.BEHAVE, commands=(command,), features=True)
    context = replace(context, project=replace(context.project, compose_service="django"))
    runner = RecordingRunner()
    BehaveAdapter().run(context, runner)
    assert runner.calls[0][0] == (
        *_PREFIX,
        "python",
        "manage.py",
        "behave",
        "--settings=config.settings.behave",
        "features",
    )
