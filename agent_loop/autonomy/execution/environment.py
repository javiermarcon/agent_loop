"""Resolve the target repository's Python environment without leaking ours."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.security import resolve_workspace_executable
from agent_loop.autonomy.security import sanitize_environment

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agent_loop.autonomy.config import AutonomyConfig
    from agent_loop.autonomy.discovery.django_project import DjangoProjectInfo


@dataclass(frozen=True, slots=True)
class TargetEnvironment:
    """The interpreter and environment used to execute target tools."""

    python_executable: Path | None
    bin_dir: Path | None
    variables: Mapping[str, str]
    reason: str

    @property
    def has_python(self) -> bool:
        """Return whether a target interpreter was resolved."""
        return self.python_executable is not None


def resolve_environment(
    workspace: TargetWorkspace,
    project: DjangoProjectInfo,
    config: AutonomyConfig,
) -> TargetEnvironment:
    """Resolve the target virtualenv and build an isolated process environment."""
    python_executable, bin_dir, reason = _resolve_python(workspace, project)
    path = _path_without_agent_loop_venv(os.environ.get("PATH", ""))
    if bin_dir is not None:
        path = f"{bin_dir}{os.pathsep}{path}" if path else str(bin_dir)
    variables = sanitize_environment(os.environ, extra=config.target_env, path_prepend=None)
    variables["PATH"] = path
    if python_executable is not None and bin_dir is not None:
        variables["VIRTUAL_ENV"] = str(bin_dir.parent)
    return TargetEnvironment(
        python_executable=python_executable,
        bin_dir=bin_dir,
        variables=variables,
        reason=reason,
    )


def _resolve_python(
    workspace: TargetWorkspace,
    project: DjangoProjectInfo,
) -> tuple[Path | None, Path | None, str]:
    if project.virtualenv is None or project.python_executable is None:
        return None, None, "no target virtualenv detected"
    python_executable = resolve_workspace_executable(
        workspace,
        project.python_executable,
        allow_venv_interpreter=True,
    )
    if python_executable is None:
        return None, None, "target virtualenv interpreter is not a safe executable inside the workspace"
    bin_dir = python_executable.parent
    return python_executable, bin_dir, "target virtualenv resolved"


def _path_without_agent_loop_venv(path: str) -> str:
    entries = [entry for entry in path.split(os.pathsep) if entry and not _is_agent_loop_venv(entry)]
    return os.pathsep.join(entries)


def _is_agent_loop_venv(entry: str) -> bool:
    candidates = _agent_loop_venv_bins()
    resolved = Path(entry).resolve(strict=False)
    return any(resolved == candidate for candidate in candidates)


def _agent_loop_venv_bins() -> tuple[Path, ...]:
    bins: list[Path] = []
    current = os.environ.get("VIRTUAL_ENV")
    if current:
        bins.append(Path(current).resolve(strict=False) / _bin_name())
    if sys.prefix != sys.base_prefix:
        bins.append(Path(sys.prefix).resolve(strict=False) / _bin_name())
    return tuple(bins)


def _bin_name() -> str:
    return "Scripts" if os.name == "nt" else "bin"
