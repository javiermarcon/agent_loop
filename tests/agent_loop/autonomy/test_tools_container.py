"""Container-backed tool discovery tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import agent_loop.autonomy.discovery.tools as tools_module
from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.discovery.tools import ToolState
from agent_loop.autonomy.discovery.tools import discover_tools
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import FakeProbe
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    import pytest


class FakeContainerProbe:
    """Deterministic container module probe."""

    def __init__(self, *, available: bool | None) -> None:
        self._available = available
        self.calls: list[tuple[tuple[str, ...], str]] = []

    def is_available(self, command_prefix: tuple[str, ...], module: str) -> bool | None:
        self.calls.append((command_prefix, module))
        return self._available


_LOCAL_PROBE = FakeProbe(
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


def _project_root(tmp_path: Path) -> Path:
    root = make_project(tmp_path / "repo", venv=False, compose=False)
    write(
        root / "docker-compose.local.yml",
        "services:\n  django:\n    image: python:3.12\n  postgres:\n    image: postgres:16\n",
    )
    write(root / "README.md", "docker compose -f docker-compose.local.yml exec -T django pytest\n")
    return root


def _discover(root: Path, probe: FakeContainerProbe, monkeypatch: pytest.MonkeyPatch):
    workspace = TargetWorkspace.open(root)
    project = discover_django_project(workspace)
    monkeypatch.setattr(
        tools_module,
        "find_system_executable",
        lambda name, *, workspace=None: Path("/usr/bin/docker") if name == "docker" else None,
    )
    monkeypatch.setattr(tools_module, "_probe_external", lambda executable, args: True)
    monkeypatch.setattr(tools_module, "_detect_compose_project", lambda *args, **kwargs: "fixture-project")
    return discover_tools(workspace, project, probe=_LOCAL_PROBE, container_probe=probe)


def test_tool_available_inside_container_is_configured_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A container import proves the tool is installed even without a local venv."""
    inventory = _discover(_project_root(tmp_path), FakeContainerProbe(available=True), monkeypatch)
    ruff = inventory.get(ToolKind.RUFF)
    assert ruff is not None
    assert ruff.state is ToolState.CONFIGURED_AVAILABLE
    assert ruff.executable == "python"
    assert ruff.container_service == "django"
    assert ruff.command_prefix[:4] == ("/usr/bin/docker", "compose", "-p", "fixture-project")
    assert ruff.command_prefix[-3:] == ("exec", "-T", "django")
    assert "docker-compose.local.yml" in ruff.command_prefix


def test_tool_missing_inside_container_is_configured_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative container probe is a real availability result."""
    inventory = _discover(_project_root(tmp_path), FakeContainerProbe(available=False), monkeypatch)
    ruff = inventory.get(ToolKind.RUFF)
    assert ruff is not None
    assert ruff.state is ToolState.CONFIGURED_UNAVAILABLE
    assert "not importable inside Compose service" in ruff.reason


def test_tool_probe_that_cannot_verify_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stopped container or failed probe cannot be reported as available."""
    inventory = _discover(_project_root(tmp_path), FakeContainerProbe(available=None), monkeypatch)
    ruff = inventory.get(ToolKind.RUFF)
    assert ruff is not None
    assert ruff.state is ToolState.UNKNOWN
    assert "could not verify" in ruff.reason


def test_container_tools_without_documented_service_are_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambiguous or missing service evidence prevents a false container claim."""
    root = make_project(tmp_path / "repo", venv=False, compose=False)
    write(
        root / "docker-compose.local.yml",
        "services:\n  alpha:\n    image: python:3.12\n  beta:\n    image: python:3.12\n",
    )
    inventory = _discover(root, FakeContainerProbe(available=True), monkeypatch)
    ruff = inventory.get(ToolKind.RUFF)
    assert ruff is not None
    assert ruff.state is ToolState.CONFIGURED_UNAVAILABLE
    assert "virtualenv" in ruff.reason


def test_config_only_tool_is_not_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config file without dependency or invocation is not an unmet requirement."""
    root = _project_root(tmp_path)
    write(root / ".bandit", "skips: []\n")
    inventory = _discover(root, FakeContainerProbe(available=False), monkeypatch)
    bandit = inventory.get(ToolKind.BANDIT)
    assert bandit is not None
    assert bandit.state is ToolState.NOT_REQUIRED
    assert bandit.config_paths == (".bandit",)


def test_dependency_declared_tool_stays_configured_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared dependency that is missing is unavailable, not silently optional."""
    root = _project_root(tmp_path)
    write(root / ".bandit", "skips: []\n")
    write(root / "requirements.txt", "Django==5.0.1\nbandit==1.7.0\n")
    inventory = _discover(root, FakeContainerProbe(available=False), monkeypatch)
    bandit = inventory.get(ToolKind.BANDIT)
    assert bandit is not None
    assert bandit.state is ToolState.CONFIGURED_UNAVAILABLE
