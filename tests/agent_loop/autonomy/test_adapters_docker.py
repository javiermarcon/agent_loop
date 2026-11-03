"""Docker Compose adapter command construction tests."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.tools import ToolDiscovery
from agent_loop.autonomy.discovery.tools import ToolInventory
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.discovery.tools import ToolState
from agent_loop.autonomy.security import resolve_system_executable
from agent_loop.autonomy.tools.adapters import DockerComposeAdapter
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import CommandOutcome
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import git_executable
from tests.agent_loop.autonomy.support import make_adapter_context

if TYPE_CHECKING:
    from pathlib import Path


def _git() -> str:
    resolved = resolve_system_executable(git_executable())
    assert resolved is not None
    return str(resolved)


def test_docker_compose_validates_configuration_without_starting_services(tmp_path: Path) -> None:
    """Compose V2 validation uses the discovered command prefix."""
    context = make_adapter_context(tmp_path, compose=True)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(
                kind=ToolKind.DOCKER_COMPOSE,
                state=ToolState.CONFIGURED_AVAILABLE,
                command_prefix=(_git(), "compose"),
            ),
        ),
    )
    context = replace(context, inventory=inventory)
    runner = RecordingRunner()
    DockerComposeAdapter().run(context, runner)
    assert runner.calls[0][0] == (_git(), "compose", "-f", "docker-compose.yml", "config", "--quiet")


def test_docker_compose_v1_uses_its_own_command_prefix(tmp_path: Path) -> None:
    """Compose V1 uses docker-compose rather than docker compose."""
    context = make_adapter_context(tmp_path, compose=True)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(
                kind=ToolKind.DOCKER_COMPOSE,
                state=ToolState.CONFIGURED_AVAILABLE,
                command_prefix=(_git(),),
            ),
        ),
    )
    context = replace(context, inventory=inventory)
    runner = RecordingRunner()
    DockerComposeAdapter().run(context, runner)
    assert runner.calls[0][0] == (_git(), "-f", "docker-compose.yml", "config", "--quiet")


def test_docker_compose_without_discovered_prefix_is_unavailable(tmp_path: Path) -> None:
    """A discovered Compose config without a command contract cannot execute."""
    context = make_adapter_context(tmp_path, compose=True)
    inventory = ToolInventory(
        entries=(ToolDiscovery(kind=ToolKind.DOCKER_COMPOSE, state=ToolState.CONFIGURED_AVAILABLE),),
    )
    context = replace(context, inventory=inventory)
    runner = RecordingRunner()
    result = DockerComposeAdapter().run(context, runner)
    assert result.status is ToolStatus.UNAVAILABLE
    assert runner.calls == []


def test_docker_compose_execution_error_is_preserved(tmp_path: Path) -> None:
    """Compose validation failures preserve the exact command and exit code."""
    context = make_adapter_context(tmp_path, compose=True)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(
                kind=ToolKind.DOCKER_COMPOSE,
                state=ToolState.CONFIGURED_AVAILABLE,
                command_prefix=(_git(), "compose"),
            ),
        ),
    )
    context = replace(context, inventory=inventory)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("docker",),
                cwd=str(context.workspace.root),
                exit_code=1,
                stdout="",
                stderr="invalid compose",
                duration_seconds=0.0,
            ),
        ],
    )
    result = DockerComposeAdapter().run(context, runner)
    assert runner.calls[0][0] == (_git(), "compose", "-f", "docker-compose.yml", "config", "--quiet")
    assert result.status is ToolStatus.FAIL
    assert result.exit_code == 1


def test_docker_compose_timeout_is_unknown(tmp_path: Path) -> None:
    """A Compose timeout cannot be reported as success or proven failure."""
    context = make_adapter_context(tmp_path, compose=True)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(
                kind=ToolKind.DOCKER_COMPOSE,
                state=ToolState.CONFIGURED_AVAILABLE,
                command_prefix=(_git(), "compose"),
            ),
        ),
    )
    context = replace(context, inventory=inventory)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("docker",),
                cwd=str(context.workspace.root),
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=1.0,
                timed_out=True,
                error="command timed out",
            ),
        ],
    )
    result = DockerComposeAdapter().run(context, runner)
    assert result.status is ToolStatus.UNKNOWN
