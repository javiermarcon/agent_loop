"""Shared fixtures and fakes for autonomy tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.discovery.tools import discover_tools
from agent_loop.autonomy.execution.environment import resolve_environment
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.security import git_environment
from agent_loop.autonomy.tools.base import ToolContext
from agent_loop.autonomy.tools.command import CommandOutcome

if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Sequence
    from pathlib import Path

PYPROJECT = """\
[project]
name = "fixture-project"
requires-python = ">=3.12"
dependencies = ["Django>=5.0"]

[tool.ruff]
line-length = 100

[tool.mypy]
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.pylint.main]
py-version = "3.12"
"""


def write(path: Path, content: str) -> Path:
    """Write UTF-8 text, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def git_executable() -> str:
    """Return the absolute Git executable or fail the test setup explicitly."""
    executable = shutil.which("git")
    if executable is None:
        message = "git executable is required for autonomy tests"
        raise RuntimeError(message)
    return executable


def make_project(
    root: Path,
    *,
    venv: bool = True,
    features: bool = False,
    compose: bool = False,
    pre_commit: bool = True,
) -> Path:
    """Create a minimal Django-like repository for discovery tests."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603
        (git_executable(), "init", "-q"),
        cwd=root,
        check=True,
        capture_output=True,
        env=git_environment(),
    )
    write(root / "manage.py", 'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")\n')
    write(
        root / "config" / "settings.py",
        'INSTALLED_APPS = ["django.contrib.contenttypes", "apps.sample"]\n',
    )
    write(root / "requirements.txt", "Django==5.0.1\nPyYAML>=6\n")
    write(root / ".python-version", "3.12\n")
    write(root / "pyproject.toml", PYPROJECT)
    write(root / "tests" / "test_sample.py", "def test_sample():\n    assert True\n")
    write(root / "apps" / "sample" / "__init__.py", "")
    if pre_commit:
        write(
            root / ".pre-commit-config.yaml",
            "repos:\n"
            "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
            "    rev: v0.1.0\n"
            "    hooks:\n"
            "      - id: ruff\n",
        )
    if features:
        write(root / "features" / "sample.feature", "Feature: sample\n  Scenario: one\n    Given a step\n")
        write(root / "behave.ini", "[behave]\n")
    if compose:
        write(root / "docker-compose.yml", "services:\n  web:\n    image: python:3.12\n")
    if venv:
        bin_dir = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        bin_dir.mkdir(parents=True, exist_ok=True)
        write(root / ".venv" / "pyvenv.cfg", f"home = {sys.base_prefix}\n")
        executable = bin_dir / ("python.exe" if os.name == "nt" else "python")
        executable.symlink_to(sys.executable)
    return root


@dataclass
class RecordingRunner:
    """Command runner that records calls and replays canned outcomes."""

    outcomes: list[CommandOutcome] = field(default_factory=list)
    calls: list[tuple[tuple[str, ...], Path, dict[str, str], int | None]] = field(default_factory=list)
    inputs: list[str | None] = field(default_factory=list)

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int | None = None,
        stdin_text: str | None = None,
    ) -> CommandOutcome:
        """Record one call and return the next canned outcome."""
        self.calls.append((tuple(command), cwd, dict(env), timeout_seconds))
        self.inputs.append(stdin_text)
        if self.outcomes:
            return self.outcomes.pop(0)
        return CommandOutcome(
            command=tuple(command),
            cwd=str(cwd),
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )


class FakeProbe:
    """Deterministic module-availability probe."""

    def __init__(self, available: Mapping[str, bool | None]) -> None:
        self._available = dict(available)

    def is_available(self, python_executable: Path, module: str) -> bool | None:
        """Return the configured result for a module."""
        return self._available.get(module)


def make_context(root: Path, *, changed_files: tuple[str, ...] = (), full: bool = False) -> ToolContext:
    """Build a real discovery context with a deterministic availability probe."""
    workspace = TargetWorkspace.open(root)
    project = discover_django_project(workspace)
    probe = FakeProbe(
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
    inventory = discover_tools(workspace, project, probe=probe)
    environment = resolve_environment(workspace, project, AutonomyConfig())
    return ToolContext(
        workspace=workspace,
        project=project,
        environment=environment,
        inventory=inventory,
        changed_files=changed_files,
        full=full,
        config=AutonomyConfig(),
    )


def make_adapter_context(
    tmp_path: Path,
    *,
    changed_files: tuple[str, ...] = ("apps/sample/models.py",),
    full: bool = False,
    features: bool = False,
    compose: bool = False,
) -> ToolContext:
    """Build an adapter context with a sample app and mirrored test."""
    root = make_project(tmp_path / "repo", features=features, compose=compose)
    write(root / "apps" / "sample" / "models.py", "class Sample:\n    pass\n")
    write(root / "tests" / "test_models.py", "def test_models():\n    assert True\n")
    return make_context(root, changed_files=changed_files, full=full)
