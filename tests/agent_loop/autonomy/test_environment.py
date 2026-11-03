"""Target environment isolation tests."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.errors import SecurityError
from agent_loop.autonomy.execution.environment import resolve_environment
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import make_project

if TYPE_CHECKING:
    from pathlib import Path


def test_environment_uses_target_virtualenv_and_strips_agent_loop_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The target interpreter and venv are selected without leaking our context."""
    root = make_project(tmp_path / "repo")
    monkeypatch.setenv("SHIPII_TOKEN", "secret")
    monkeypatch.setenv("PYTHONPATH", "/shipii")
    monkeypatch.setenv("VIRTUAL_ENV", "/agent-loop/.venv")
    monkeypatch.setenv("PATH", f"/agent-loop/.venv/bin{os.pathsep}/usr/bin")
    workspace = TargetWorkspace.open(root)
    project = discover_django_project(workspace)
    environment = resolve_environment(workspace, project, AutonomyConfig(target_environment=(("TARGET_FLAG", "1"),)))
    assert environment.has_python is True
    assert environment.python_executable == root / ".venv" / "bin" / "python"
    assert environment.bin_dir == root / ".venv" / "bin"
    assert environment.variables["VIRTUAL_ENV"] == str(root / ".venv")
    assert environment.variables["TARGET_FLAG"] == "1"
    assert environment.variables["PATH"].startswith(f"{root / '.venv' / 'bin'}{os.pathsep}")
    assert "/agent-loop/.venv/bin" not in environment.variables["PATH"]
    assert "SHIPII_TOKEN" not in environment.variables
    assert "PYTHONPATH" not in environment.variables


def test_missing_virtualenv_is_reported_without_fallback(tmp_path: Path) -> None:
    """No target venv means no Python tool execution, never Agent Loop's Python."""
    root = make_project(tmp_path / "repo", venv=False)
    workspace = TargetWorkspace.open(root)
    project = discover_django_project(workspace)
    environment = resolve_environment(workspace, project, AutonomyConfig())
    assert environment.has_python is False
    assert environment.python_executable is None
    assert environment.reason == "no target virtualenv detected"


def test_forbidden_target_environment_variable_is_rejected(tmp_path: Path) -> None:
    """Callers cannot inject Agent Loop or Shipii variables into the target."""
    root = make_project(tmp_path / "repo")
    workspace = TargetWorkspace.open(root)
    project = discover_django_project(workspace)
    config = AutonomyConfig(target_environment=(("SHIPII_TOKEN", "secret"),))
    with pytest.raises(SecurityError, match="not allowed"):
        resolve_environment(workspace, project, config)
