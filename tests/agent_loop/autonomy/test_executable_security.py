"""Workspace executable resolution and external-symlink rejection tests."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.discovery.tools import ToolInventory
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.discovery.tools import ToolState
from agent_loop.autonomy.discovery.tools import discover_tools
from agent_loop.autonomy.execution.environment import resolve_environment
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.security import find_system_executable
from agent_loop.autonomy.security import resolve_workspace_executable
from agent_loop.autonomy.tools.adapters import DockerComposeAdapter
from agent_loop.autonomy.tools.adapters import RuffAdapter
from agent_loop.autonomy.tools.base import ToolStatus
from tests.agent_loop.autonomy.support import FakeProbe
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_context
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    from pathlib import Path


def _probe(**values: bool) -> FakeProbe:
    modules = dict.fromkeys(("ruff", "mypy", "pylint", "bandit", "pytest", "behave", "pre_commit"), False)
    modules.update(values)
    return FakeProbe(modules)


def _script(path: Path, marker: Path) -> Path:
    write(path, f"#!/bin/sh\ntouch {marker}\nexit 0\n")
    path.chmod(0o755)
    return path


def _context_with(root: Path, inventory: ToolInventory, changed: tuple[str, ...]):
    context = make_context(root, changed_files=changed)
    return replace(context, inventory=inventory)


def test_external_ruff_symlink_is_unavailable_and_never_executed(tmp_path: Path) -> None:
    """A .venv/bin/ruff link to an external script is not a valid target tool."""
    root = make_project(tmp_path / "repo")
    marker = tmp_path / "executed"
    external = _script(tmp_path / "evil" / "ruff", marker)
    ruff_bin = root / ".venv" / "bin" / "ruff"
    ruff_bin.symlink_to(external)
    workspace = TargetWorkspace.open(root)
    probe = _probe()
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=probe)
    entry = inventory.get(ToolKind.RUFF)
    assert entry is not None
    assert entry.state is ToolState.CONFIGURED_UNAVAILABLE
    assert entry.executable != str(external)
    context = _context_with(root, inventory, ("apps/sample/__init__.py",))
    result = RuffAdapter().run(context, RecordingRunner())
    assert result.status is ToolStatus.UNAVAILABLE
    assert marker.exists() is False


def test_interpreter_symlink_without_pyvenv_config_is_rejected(tmp_path: Path) -> None:
    """A python link without the virtualenv marker cannot report the target prefix."""
    root = make_project(tmp_path / "repo")
    (root / ".venv" / "pyvenv.cfg").unlink()
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_probe(ruff=True))
    entry = inventory.get(ToolKind.RUFF)
    assert entry is not None
    assert entry.state is ToolState.CONFIGURED_UNAVAILABLE
    environment = resolve_environment(workspace, discover_django_project(workspace), AutonomyConfig())
    assert environment.has_python is False


def test_chained_and_broken_symlinks_are_rejected(tmp_path: Path) -> None:
    """Chained and broken links never resolve into an executable tool."""
    root = make_project(tmp_path / "repo")
    marker = tmp_path / "executed"
    external = _script(tmp_path / "evil" / "ruff", marker)
    first = root / "bin-link"
    first.symlink_to(external)
    ruff_bin = root / ".venv" / "bin" / "ruff"
    ruff_bin.symlink_to(first)
    broken = root / ".venv" / "bin" / "mypy"
    broken.symlink_to(tmp_path / "missing")
    workspace = TargetWorkspace.open(root)
    probe = _probe()
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=probe)
    ruff = inventory.get(ToolKind.RUFF)
    mypy = inventory.get(ToolKind.MYPY)
    assert ruff is not None
    assert ruff.state is ToolState.CONFIGURED_UNAVAILABLE
    assert mypy is not None
    assert mypy.state is ToolState.CONFIGURED_UNAVAILABLE
    assert marker.exists() is False


def test_legitimate_workspace_binary_is_discovered_and_executed(tmp_path: Path) -> None:
    """A real executable inside the workspace remains valid and is the one used."""
    root = make_project(tmp_path / "repo")
    ruff_bin = root / ".venv" / "bin" / "ruff"
    write(ruff_bin, "#!/bin/sh\nexit 0\n")
    ruff_bin.chmod(0o755)
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_probe())
    entry = inventory.get(ToolKind.RUFF)
    assert entry is not None
    assert entry.state is ToolState.CONFIGURED_AVAILABLE
    assert entry.executable == str(ruff_bin)
    runner = RecordingRunner()
    RuffAdapter().run(_context_with(root, inventory, ("apps/sample/__init__.py",)), runner)
    assert runner.calls[0][0][0] == str(ruff_bin)


def test_workspace_shim_on_path_cannot_intercept_system_tool(tmp_path: Path, monkeypatch) -> None:
    """PATH entries owned by the workspace are skipped for privileged tools."""
    root = make_project(tmp_path / "repo")
    workspace_script = root / ".venv" / "bin" / "docker"
    write(workspace_script, "#!/bin/sh\nexit 0\n")
    workspace_script.chmod(0o755)
    system_dir = tmp_path / "system-bin"
    system_tool = _script(system_dir / "docker", tmp_path / "unused")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((str(root / ".venv" / "bin"), str(system_dir), os.environ.get("PATH", ""))),
    )
    resolved = find_system_executable("docker", workspace=TargetWorkspace.open(root))
    assert resolved == system_tool.resolve()


def test_compose_executes_the_discovered_absolute_executable(tmp_path: Path) -> None:
    """Compose uses the validated discovery path, never a workspace shim."""
    root = make_project(tmp_path / "repo", compose=True)
    shim = root / ".venv" / "bin" / "docker"
    write(shim, "#!/bin/sh\nexit 0\n")
    shim.chmod(0o755)
    external = _script(tmp_path / "system" / "docker", tmp_path / "unused")
    context = make_context(root, full=True)
    compose_entry = context.inventory.get(ToolKind.DOCKER_COMPOSE)
    assert compose_entry is not None
    inventory = ToolInventory(
        entries=tuple(
            replace(
                entry,
                state=ToolState.CONFIGURED_AVAILABLE,
                executable=str(external),
                command_prefix=(str(external), "compose"),
            )
            if entry.kind is ToolKind.DOCKER_COMPOSE
            else entry
            for entry in context.inventory.entries
        ),
    )
    context = replace(context, inventory=inventory)
    runner = RecordingRunner()
    DockerComposeAdapter().run(context, runner)
    command = runner.calls[0][0]
    assert command[0] == str(external)
    assert str(shim) not in command


def test_virtualenv_interpreter_symlink_with_config_is_authorized(tmp_path: Path) -> None:
    """The documented venv interpreter link remains valid when pyvenv.cfg exists."""
    root = make_project(tmp_path / "repo")
    workspace = TargetWorkspace.open(root)
    resolved = resolve_workspace_executable(
        workspace,
        ".venv/bin/python",
        allow_venv_interpreter=True,
    )
    assert resolved is not None
    assert resolved == workspace.root / ".venv" / "bin" / "python"


def test_real_docker_discovery_and_execution_share_the_same_path(tmp_path: Path) -> None:
    """When Docker exists, discovery and execution use the same absolute binary."""
    root = make_project(tmp_path / "repo", compose=True)
    workspace = TargetWorkspace.open(root)
    docker = find_system_executable("docker", workspace=workspace)
    if docker is None:
        pytest.skip("Docker is not installed in this environment")
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_probe())
    entry = inventory.get(ToolKind.DOCKER_COMPOSE)
    assert entry is not None
    if entry.state is not ToolState.CONFIGURED_AVAILABLE:
        pytest.skip(f"Docker Compose is not available: {entry.reason}")
    assert entry.executable == str(docker)
    assert entry.command_prefix[0] == str(docker)
    context = replace(make_context(root, full=True), inventory=inventory)
    runner = RecordingRunner()
    DockerComposeAdapter().run(context, runner)
    assert runner.calls[0][0][0] == str(docker)
