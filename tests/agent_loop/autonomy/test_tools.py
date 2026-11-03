"""Tool discovery tri-state tests."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.discovery.tool_config import config_details_for
from agent_loop.autonomy.discovery.tool_config import config_paths_for
from agent_loop.autonomy.discovery.tool_config import is_tool_not_required
from agent_loop.autonomy.discovery.tools import ToolDiscovery
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.discovery.tools import ToolState
from agent_loop.autonomy.discovery.tools import discover_tools
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import FakeProbe
from tests.agent_loop.autonomy.support import make_project

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _available() -> FakeProbe:
    return FakeProbe(
        {
            "ruff": True,
            "mypy": True,
            "pylint": True,
            "bandit": True,
            "pytest": True,
            "behave": True,
            "pre_commit": True,
        },
    )


def test_configured_and_available_tools_are_reported(tmp_path: Path) -> None:
    """Configured tools with importable modules become available."""
    root = make_project(tmp_path / "repo", features=True, compose=True)
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_available())
    for kind in (
        ToolKind.PRE_COMMIT,
        ToolKind.RUFF,
        ToolKind.MYPY,
        ToolKind.PYLINT,
        ToolKind.PYTEST,
        ToolKind.BEHAVE,
    ):
        entry = inventory.get(kind)
        assert entry is not None
        assert entry.state is ToolState.CONFIGURED_AVAILABLE
        assert entry.config_paths
        assert entry.executable is not None
    git = inventory.get(ToolKind.GIT)
    assert git is not None
    assert git.state is ToolState.CONFIGURED_AVAILABLE
    compose = inventory.get(ToolKind.DOCKER_COMPOSE)
    assert compose is not None
    assert compose.state in {ToolState.CONFIGURED_AVAILABLE, ToolState.CONFIGURED_UNAVAILABLE}


def test_unconfigured_tools_are_not_configured(tmp_path: Path) -> None:
    """A tool without target configuration is never assumed present."""
    root = make_project(tmp_path / "repo")
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_available())
    bandit = inventory.get(ToolKind.BANDIT)
    assert bandit is not None
    assert bandit.state is ToolState.NOT_CONFIGURED
    assert bandit.config_paths == ()


def test_configured_tool_without_target_venv_is_unavailable(tmp_path: Path) -> None:
    """Agent Loop's interpreter is never used as a fallback."""
    root = make_project(tmp_path / "repo", venv=False)
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_available())
    ruff = inventory.get(ToolKind.RUFF)
    assert ruff is not None
    assert ruff.state is ToolState.CONFIGURED_UNAVAILABLE
    assert "virtualenv" in ruff.reason


def test_configured_tool_with_missing_module_is_unavailable(tmp_path: Path) -> None:
    """A configured but non-importable module is explicitly unavailable."""
    root = make_project(tmp_path / "repo")
    workspace = TargetWorkspace.open(root)
    probe = FakeProbe({"ruff": False})
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=probe)
    ruff = inventory.get(ToolKind.RUFF)
    assert ruff is not None
    assert ruff.state is ToolState.CONFIGURED_UNAVAILABLE
    assert "not importable" in ruff.reason


def test_unverifiable_probe_is_unknown(tmp_path: Path) -> None:
    """A probe that cannot decide must not be treated as success or absence."""
    root = make_project(tmp_path / "repo")
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=FakeProbe({"ruff": None}))
    ruff = inventory.get(ToolKind.RUFF)
    assert ruff is not None
    assert ruff.state is ToolState.UNKNOWN
    assert "could not verify" in ruff.reason


def test_pre_commit_hooks_are_discovered_from_yaml(tmp_path: Path) -> None:
    """Configured pre-commit repositories and hook ids are reported."""
    root = make_project(tmp_path / "repo")
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_available())
    pre_commit = inventory.get(ToolKind.PRE_COMMIT)
    assert pre_commit is not None
    assert pre_commit.details == ("https://github.com/astral-sh/ruff-pre-commit:ruff",)


def test_tool_config_helpers_own_path_and_dependency_evidence(tmp_path: Path) -> None:
    """Configuration discovery exposes cohesive path, detail and dependency evidence."""
    root = make_project(tmp_path / "repo", features=True)
    workspace = TargetWorkspace.open(root)
    project = discover_django_project(workspace)
    assert config_paths_for(workspace, ToolKind.PRE_COMMIT) == (".pre-commit-config.yaml",)
    assert config_details_for(workspace, ToolKind.PRE_COMMIT) == ("https://github.com/astral-sh/ruff-pre-commit:ruff",)
    assert config_paths_for(workspace, ToolKind.RUFF) == ("pyproject.toml",)
    assert config_paths_for(workspace, ToolKind.PYTEST) == ("pyproject.toml", "tests/")
    assert config_paths_for(workspace, ToolKind.BEHAVE) == ("behave.ini", "features/")
    assert is_tool_not_required(workspace, project, ToolKind.PYTEST) is False
    assert config_details_for(workspace, ToolKind.RUFF) == ()


def test_inventory_helpers_filter_configured_and_available(tmp_path: Path) -> None:
    """Inventory projections preserve deterministic tool ordering."""
    root = make_project(tmp_path / "repo")
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_available())
    configured = {entry.kind for entry in inventory.configured()}
    available = {entry.kind for entry in inventory.available()}
    assert ToolKind.RUFF in configured
    assert ToolKind.RUFF in available
    assert ToolKind.BANDIT not in configured


def test_target_venv_executable_is_preferred_over_module_probe(tmp_path: Path) -> None:
    """Binary tools such as Ruff are detected from the target virtualenv."""
    root = make_project(tmp_path / "repo")
    ruff_bin = root / ".venv" / "bin" / "ruff"
    ruff_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ruff_bin.chmod(0o755)
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(
        workspace,
        discover_django_project(workspace),
        probe=FakeProbe({"ruff": False}),
    )
    ruff = inventory.get(ToolKind.RUFF)
    assert ruff is not None
    assert ruff.state is ToolState.CONFIGURED_AVAILABLE
    assert ruff.executable == str(ruff_bin)


def _compose_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    which: dict[str, str],
    probes: dict[tuple[str, tuple[str, ...]], bool],
) -> ToolDiscovery | None:
    root = make_project(tmp_path / "repo", compose=True)
    workspace = TargetWorkspace.open(root)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    resolved: dict[str, str] = {}
    by_original: dict[str, str] = {}
    for name, original in which.items():
        executable = bin_dir / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        resolved[name] = str(executable)
        by_original[original] = str(executable)
    translated = {
        (by_original.get(executable, executable), args): value for (executable, args), value in probes.items()
    }

    def _probe(executable: str, args: tuple[str, ...]) -> bool:
        return translated.get((executable, args), False)

    def _find(name: str, *, workspace: object | None = None) -> str | None:
        del workspace
        return resolved.get(name)

    monkeypatch.setattr("agent_loop.autonomy.discovery.tools.find_system_executable", _find)
    monkeypatch.setattr("agent_loop.autonomy.discovery.tools._probe_external", _probe)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_available())
    return inventory.get(ToolKind.DOCKER_COMPOSE)


def test_compose_v2_is_discovered_with_its_command_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose V2 is discovered through docker compose and exposes its prefix."""
    entry = _compose_entry(
        tmp_path,
        monkeypatch,
        {"docker": "/usr/bin/docker"},
        {("/usr/bin/docker", ("compose", "version")): True},
    )
    assert entry is not None
    assert entry.state is ToolState.CONFIGURED_AVAILABLE
    assert entry.command_prefix == (str(tmp_path / "bin" / "docker"), "compose")
    assert "compose_v2" in entry.details


def test_compose_v1_is_discovered_with_its_command_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose V1 is discovered through docker-compose."""
    entry = _compose_entry(
        tmp_path,
        monkeypatch,
        {"docker-compose": "/usr/bin/docker-compose"},
        {("/usr/bin/docker-compose", ("version",)): True},
    )
    assert entry is not None
    assert entry.state is ToolState.CONFIGURED_AVAILABLE
    assert entry.command_prefix == (str(tmp_path / "bin" / "docker-compose"),)
    assert "compose_v1" in entry.details


def test_compose_prefers_v2_when_both_are_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both implementations are coherently detected; V2 wins deterministically."""
    entry = _compose_entry(
        tmp_path,
        monkeypatch,
        {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose"},
        {
            ("/usr/bin/docker", ("compose", "version")): True,
            ("/usr/bin/docker-compose", ("version",)): True,
        },
    )
    assert entry is not None
    assert entry.command_prefix == (str(tmp_path / "bin" / "docker"), "compose")
    assert "compose_v2" in entry.details


def test_compose_is_unavailable_when_no_working_command_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured Compose without a working executable is unavailable."""
    entry = _compose_entry(tmp_path, monkeypatch, {}, {})
    assert entry is not None
    assert entry.state is ToolState.CONFIGURED_UNAVAILABLE
    assert entry.command_prefix == ()


def test_compose_is_unavailable_when_probe_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An executable found by name is not enough if its probe fails."""
    entry = _compose_entry(
        tmp_path,
        monkeypatch,
        {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose"},
        {},
    )
    assert entry is not None
    assert entry.state is ToolState.CONFIGURED_UNAVAILABLE
    assert "no working compose command" in entry.reason


def test_non_executable_target_python_is_not_used(tmp_path: Path) -> None:
    """A file named python without execute permission is not a valid interpreter."""
    root = make_project(tmp_path / "repo")
    python_path = root / ".venv" / "bin" / "python"
    python_path.unlink()
    python_path.write_text("", encoding="utf-8")
    python_path.chmod(0o644)
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_available())
    ruff = inventory.get(ToolKind.RUFF)
    assert ruff is not None
    assert ruff.state is ToolState.CONFIGURED_UNAVAILABLE
    assert "executable target virtualenv" in ruff.reason


def test_non_executable_venv_binary_falls_back_to_module_probe(tmp_path: Path) -> None:
    """A non-executable binary does not count as an available tool."""
    root = make_project(tmp_path / "repo")
    ruff_bin = root / ".venv" / "bin" / "ruff"
    ruff_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ruff_bin.chmod(0o644)
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(
        workspace,
        discover_django_project(workspace),
        probe=FakeProbe({"ruff": False}),
    )
    ruff = inventory.get(ToolKind.RUFF)
    assert ruff is not None
    assert ruff.state is ToolState.CONFIGURED_UNAVAILABLE
    assert "not importable" in ruff.reason


def test_pytest_without_config_or_tests_is_not_configured(tmp_path: Path) -> None:
    """A project without pytest config or tests does not claim pytest capability."""
    root = make_project(tmp_path / "repo")
    (root / "pyproject.toml").unlink()
    shutil.rmtree(root / "tests")
    workspace = TargetWorkspace.open(root)
    inventory = discover_tools(workspace, discover_django_project(workspace), probe=_available())
    pytest_entry = inventory.get(ToolKind.PYTEST)
    assert pytest_entry is not None
    assert pytest_entry.state is ToolState.NOT_CONFIGURED
