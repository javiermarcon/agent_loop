"""Git adapter read-only and failure-reporting tests."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.tools import ToolDiscovery
from agent_loop.autonomy.discovery.tools import ToolInventory
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.discovery.tools import ToolState
from agent_loop.autonomy.execution.runner import ValidationRunner
from agent_loop.autonomy.tools.adapters import GitAdapter
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import CommandOutcome
from agent_loop.autonomy.tools.registry import ToolRegistry
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import git_executable
from tests.agent_loop.autonomy.support import make_adapter_context

if TYPE_CHECKING:
    from pathlib import Path


def test_git_adapter_is_registered_and_read_only(tmp_path: Path) -> None:
    """Git is registered and only executes a read-only status command."""
    context = make_adapter_context(tmp_path)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(
                kind=ToolKind.GIT,
                state=ToolState.CONFIGURED_AVAILABLE,
                executable=git_executable(),
            ),
        ),
    )
    context = replace(context, inventory=inventory)
    runner = RecordingRunner()
    result = GitAdapter().run(context, runner)
    command = runner.calls[0][0]
    assert command == ("/usr/bin/git", "status", "--porcelain", "--untracked-files=all")
    assert all(part not in command for part in ("commit", "push", "reset", "checkout", "clean"))
    assert result.status is ToolStatus.PASS


def test_git_adapter_without_executable_is_unavailable(tmp_path: Path) -> None:
    """A discovered Git capability without an executable is unavailable."""
    context = make_adapter_context(tmp_path)
    inventory = ToolInventory(
        entries=(ToolDiscovery(kind=ToolKind.GIT, state=ToolState.CONFIGURED_AVAILABLE),),
    )
    context = replace(context, inventory=inventory)
    runner = RecordingRunner()
    result = GitAdapter().run(context, runner)
    assert result.status is ToolStatus.UNAVAILABLE
    assert runner.calls == []


def test_git_adapter_failure_is_reported(tmp_path: Path) -> None:
    """A failing Git status command is a proven failure."""
    context = make_adapter_context(tmp_path)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(
                kind=ToolKind.GIT,
                state=ToolState.CONFIGURED_AVAILABLE,
                executable=git_executable(),
            ),
        ),
    )
    context = replace(context, inventory=inventory)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("git",),
                cwd=str(context.workspace.root),
                exit_code=1,
                stdout="",
                stderr="boom",
                duration_seconds=0.0,
            ),
        ],
    )
    result = GitAdapter().run(context, runner)
    assert result.status is ToolStatus.FAIL
    assert result.exit_code == 1


def test_git_adapter_never_passes_repository_redirect_variables(tmp_path: Path) -> None:
    """Git receives a sanitized environment even if the context was mutated."""
    context = make_adapter_context(tmp_path)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(
                kind=ToolKind.GIT,
                state=ToolState.CONFIGURED_AVAILABLE,
                executable=git_executable(),
            ),
        ),
    )
    variables = {
        **context.environment.variables,
        "GIT_DIR": str(tmp_path / "other" / ".git"),
        "GIT_INDEX_FILE": str(tmp_path / "index"),
    }
    context = replace(
        context,
        inventory=inventory,
        environment=replace(context.environment, variables=variables),
    )
    runner = RecordingRunner()
    result = GitAdapter().run(context, runner)
    assert result.status is ToolStatus.PASS
    assert "GIT_DIR" not in runner.calls[0][2]
    assert "GIT_INDEX_FILE" not in runner.calls[0][2]
    assert result.invocation is not None
    assert "GIT_DIR" not in result.invocation.environment_keys


def test_git_explicit_validation_without_registered_adapter_is_unknown(tmp_path: Path) -> None:
    """An incomplete registry cannot turn a discovered Git capability into success."""
    context = make_adapter_context(tmp_path)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(
                kind=ToolKind.GIT,
                state=ToolState.CONFIGURED_AVAILABLE,
                executable=git_executable(),
            ),
        ),
    )
    context = replace(context, inventory=inventory)
    report = ValidationRunner(ToolRegistry(), RecordingRunner()).run(context, (ToolKind.GIT,))
    assert report.status is ToolStatus.UNKNOWN
    assert "no adapter registered" in report.results[0].message


def test_default_validation_with_only_git_is_not_success(tmp_path: Path) -> None:
    """Git alone is not an effective quality validation."""
    context = make_adapter_context(tmp_path)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(
                kind=ToolKind.GIT,
                state=ToolState.CONFIGURED_AVAILABLE,
                executable=git_executable(),
            ),
        ),
    )
    context = replace(context, inventory=inventory)
    registry = ToolRegistry()
    registry.register(GitAdapter())
    report = ValidationRunner(registry, RecordingRunner()).run(context)
    assert report.status is ToolStatus.UNKNOWN
    assert report.successful is False


def test_git_adapter_rejects_a_workspace_local_git_binary(tmp_path: Path) -> None:
    """A repository-local git shim cannot impersonate the system Git."""
    context = make_adapter_context(tmp_path)
    shim = context.workspace.root / ".venv" / "bin" / "git"
    shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shim.chmod(0o755)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(
                kind=ToolKind.GIT,
                state=ToolState.CONFIGURED_AVAILABLE,
                executable=str(shim),
            ),
        ),
    )
    runner = RecordingRunner()
    result = GitAdapter().run(replace(context, inventory=inventory), runner)
    assert result.status is ToolStatus.UNAVAILABLE
    assert runner.calls == []
