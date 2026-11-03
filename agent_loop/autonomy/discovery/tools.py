"""Discover tools configured in the target repository and their availability."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol

from agent_loop.autonomy.discovery.tool_config import config_details_for
from agent_loop.autonomy.discovery.tool_config import config_paths_for
from agent_loop.autonomy.discovery.tool_config import is_tool_not_required
from agent_loop.autonomy.discovery.tool_models import DEFAULT_TOOL_ORDER
from agent_loop.autonomy.discovery.tool_models import EXECUTABLE_NAMES
from agent_loop.autonomy.discovery.tool_models import MODULE_NAMES
from agent_loop.autonomy.discovery.tool_models import ToolDiscovery
from agent_loop.autonomy.discovery.tool_models import ToolInventory
from agent_loop.autonomy.discovery.tool_models import ToolKind
from agent_loop.autonomy.discovery.tool_models import ToolState
from agent_loop.autonomy.security import find_system_executable
from agent_loop.autonomy.security import resolve_workspace_executable
from agent_loop.autonomy.security import sanitize_environment

if TYPE_CHECKING:
    from agent_loop.autonomy.discovery.django_project import DjangoProjectInfo
    from agent_loop.autonomy.security import TargetWorkspace

_PROBE_TIMEOUT_SECONDS = 15
_MODULE_MISSING_EXIT = 3


@dataclass(frozen=True, slots=True)
class _ToolConfig:
    paths: tuple[str, ...]
    details: tuple[str, ...] = ()


class ModuleProbe(Protocol):
    """Probe whether a module is importable by the target interpreter."""

    def is_available(self, python_executable: Path, module: str) -> bool | None:
        """Return True/False, or None when availability cannot be verified."""


class ContainerModuleProbe(Protocol):
    """Probe whether a module is importable inside a Compose service."""

    def is_available(self, command_prefix: tuple[str, ...], module: str) -> bool | None:
        """Return True/False, or None when the container probe cannot decide."""


class SubprocessModuleProbe:
    """Probe module availability by executing the target interpreter."""

    def __init__(self, *, timeout_seconds: int = _PROBE_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    def is_available(self, python_executable: Path, module: str) -> bool | None:
        script = _module_script(module)
        return _probe_python((str(python_executable), "-c", script), self._timeout_seconds)


class SubprocessContainerProbe:
    """Probe module availability inside a running Compose service."""

    def __init__(self, *, timeout_seconds: int = _PROBE_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    def is_available(self, command_prefix: tuple[str, ...], module: str) -> bool | None:
        script = _module_script(module)
        return _probe_python((*command_prefix, "python", "-c", script), self._timeout_seconds)


def _module_script(module: str) -> str:
    # Exit codes other than 0 (available) and 3 (missing) are infrastructure
    # failures, not evidence about the module.
    return f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec({module!r}) else 3)"


def _probe_python(command: tuple[str, ...], timeout_seconds: int) -> bool | None:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            env=sanitize_environment({}),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == _MODULE_MISSING_EXIT:
        return False
    return None


def discover_tools(
    workspace: TargetWorkspace,
    project: DjangoProjectInfo,
    *,
    probe: ModuleProbe | None = None,
    container_probe: ContainerModuleProbe | None = None,
) -> ToolInventory:
    """Discover every supported tool without executing project code."""
    module_probe = probe or SubprocessModuleProbe()
    container_module_probe = container_probe or SubprocessContainerProbe()
    python_executable = _target_python(workspace, project)
    compose_entry = _discover_tool(
        workspace,
        _ToolRequest(
            workspace=workspace,
            project=project,
            kind=ToolKind.DOCKER_COMPOSE,
            python_executable=python_executable,
            probe=module_probe,
            container=None,
        ),
    )
    container = None if python_executable is not None else _container_context(workspace, project, compose_entry)
    entries: list[ToolDiscovery] = []
    for kind in DEFAULT_TOOL_ORDER:
        if kind is ToolKind.DOCKER_COMPOSE:
            entries.append(compose_entry)
            continue
        entries.append(
            _discover_tool(
                workspace,
                _ToolRequest(
                    workspace=workspace,
                    project=project,
                    kind=kind,
                    python_executable=python_executable,
                    probe=module_probe,
                    container=container,
                    container_probe=container_module_probe,
                ),
            ),
        )
    return ToolInventory(entries=tuple(entries))


@dataclass(frozen=True, slots=True)
class _ContainerContext:
    command_prefix: tuple[str, ...]
    service: str


def _container_context(
    workspace: TargetWorkspace,
    project: DjangoProjectInfo,
    compose_entry: ToolDiscovery,
) -> _ContainerContext | None:
    if project.compose_error or not project.compose_project_files or project.compose_service is None:
        return None
    if compose_entry.state is not ToolState.CONFIGURED_AVAILABLE or not compose_entry.command_prefix:
        return None
    prefix: list[str] = [*compose_entry.command_prefix]
    project_name = _detect_compose_project(
        compose_entry.command_prefix[0],
        workspace,
        project.compose_project_files,
    )
    if project_name is not None:
        prefix.extend(("-p", project_name))
    for compose_file in project.compose_project_files:
        prefix.extend(("-f", compose_file))
    prefix.extend(("exec", "-T", project.compose_service))
    return _ContainerContext(command_prefix=tuple(prefix), service=project.compose_service)


def _detect_compose_project(
    executable: str,
    workspace: TargetWorkspace,
    compose_files: tuple[str, ...],
) -> str | None:
    """Bind discovery to the running Compose project that owns this worktree."""
    expected = {workspace.resolve(name) for name in compose_files}
    try:
        completed = subprocess.run(  # noqa: S603 - validated absolute executable, no shell
            (executable, "compose", "ls", "--format", "json"),
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROBE_TIMEOUT_SECONDS,
            env=sanitize_environment(os.environ),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        projects = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(projects, list):
        return None
    matches: list[str] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        name = project.get("Name")
        config_files = project.get("ConfigFiles")
        if not isinstance(name, str) or not isinstance(config_files, str):
            continue
        candidates = {Path(item.strip()).resolve() for item in config_files.split(",") if item.strip()}
        if candidates & expected:
            matches.append(name)
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True, slots=True)
class _ToolRequest:
    workspace: TargetWorkspace
    project: DjangoProjectInfo
    kind: ToolKind
    python_executable: Path | None
    probe: ModuleProbe
    container: _ContainerContext | None
    container_probe: ContainerModuleProbe | None = None


def _discover_tool(workspace: TargetWorkspace, request: _ToolRequest) -> ToolDiscovery:
    kind = request.kind
    config = _ToolConfig(
        paths=config_paths_for(workspace, kind, request.project),
        details=config_details_for(workspace, kind),
    )
    if kind is ToolKind.GIT:
        return _external_tool(workspace, kind, config, "git", ("--version",))
    if kind is ToolKind.DOCKER_COMPOSE:
        return _docker_compose_tool(workspace, kind, config)
    if not config.paths:
        return ToolDiscovery(kind=kind, state=ToolState.NOT_CONFIGURED, reason="not configured by the target project")
    if request.python_executable is not None:
        return _python_tool(request, config, request.python_executable, request.probe)
    if request.container is not None and request.container_probe is not None:
        return _container_python_tool(request, config)
    return ToolDiscovery(
        kind=kind,
        state=ToolState.CONFIGURED_UNAVAILABLE,
        config_paths=config.paths,
        details=config.details,
        reason="no executable target virtualenv interpreter detected; refusing to use Agent Loop's interpreter",
    )


def _python_tool(
    request: _ToolRequest,
    config: _ToolConfig,
    python_executable: Path,
    probe: ModuleProbe,
) -> ToolDiscovery:
    kind = request.kind
    venv_executable = _venv_executable(request.workspace, python_executable.parent, kind)
    if venv_executable is not None:
        return ToolDiscovery(
            kind=kind,
            state=ToolState.CONFIGURED_AVAILABLE,
            config_paths=config.paths,
            executable=str(venv_executable),
            details=config.details,
            reason="executable found in the target virtualenv",
        )
    module = MODULE_NAMES[kind]
    available = probe.is_available(python_executable, module)
    if available is None:
        return ToolDiscovery(
            kind=kind,
            state=ToolState.UNKNOWN,
            config_paths=config.paths,
            executable=str(python_executable),
            details=config.details,
            reason=f"could not verify module {module!r} in the target interpreter",
        )
    state = ToolState.CONFIGURED_AVAILABLE if available else ToolState.CONFIGURED_UNAVAILABLE
    reason = f"module {module!r} is importable" if available else f"module {module!r} is not importable"
    return ToolDiscovery(
        kind=kind,
        state=state,
        config_paths=config.paths,
        executable=str(python_executable),
        details=config.details,
        reason=reason,
    )


def _container_python_tool(request: _ToolRequest, config: _ToolConfig) -> ToolDiscovery:
    container = request.container
    probe = request.container_probe
    if container is None or probe is None:
        message = "container execution was requested without a probe context"
        raise RuntimeError(message)
    kind = request.kind
    module = MODULE_NAMES[kind]
    available = probe.is_available(container.command_prefix, module)
    details = (*config.details, "docker_compose_container")
    if available is None:
        return ToolDiscovery(
            kind=kind,
            state=ToolState.UNKNOWN,
            config_paths=config.paths,
            details=details,
            container_service=container.service,
            reason=(
                f"could not verify module {module!r} inside Compose service {container.service!r}; "
                "the service may not be running"
            ),
        )
    if not available and is_tool_not_required(request.workspace, request.project, kind):
        return _not_required_discovery(kind, config, container=container)
    state = ToolState.CONFIGURED_AVAILABLE if available else ToolState.CONFIGURED_UNAVAILABLE
    reason = (
        f"module {module!r} is importable inside Compose service {container.service!r}"
        if available
        else f"module {module!r} is not importable inside Compose service {container.service!r}"
    )
    return ToolDiscovery(
        kind=kind,
        state=state,
        config_paths=config.paths,
        executable="python" if available else None,
        command_prefix=container.command_prefix if available else (),
        container_service=container.service,
        details=details,
        reason=reason,
    )


def _not_required_discovery(
    kind: ToolKind,
    config: _ToolConfig,
    *,
    container: _ContainerContext | None = None,
) -> ToolDiscovery:
    details = (*config.details, "not_required")
    return ToolDiscovery(
        kind=kind,
        state=ToolState.NOT_REQUIRED,
        config_paths=config.paths,
        container_service=container.service if container is not None else None,
        details=details,
        reason="configuration exists but the project does not install or invoke this tool",
    )


def _external_tool(
    workspace: TargetWorkspace,
    kind: ToolKind,
    config: _ToolConfig,
    executable: str,
    probe_args: tuple[str, ...],
) -> ToolDiscovery:
    if not config.paths:
        return ToolDiscovery(kind=kind, state=ToolState.NOT_CONFIGURED, reason="not configured by the target project")
    resolved = find_system_executable(executable, workspace=workspace)
    if resolved is None:
        return ToolDiscovery(
            kind=kind,
            state=ToolState.CONFIGURED_UNAVAILABLE,
            config_paths=config.paths,
            details=config.details,
            reason=f"executable {executable!r} not found as a validated absolute path",
        )
    if not _probe_external(str(resolved), probe_args):
        return ToolDiscovery(
            kind=kind,
            state=ToolState.CONFIGURED_UNAVAILABLE,
            config_paths=config.paths,
            executable=str(resolved),
            details=config.details,
            reason=f"executable {executable!r} found but its version probe failed",
        )
    return ToolDiscovery(
        kind=kind,
        state=ToolState.CONFIGURED_AVAILABLE,
        config_paths=config.paths,
        executable=str(resolved),
        details=config.details,
        reason=f"executable {executable!r} found",
    )


def _docker_compose_tool(
    workspace: TargetWorkspace,
    kind: ToolKind,
    config: _ToolConfig,
) -> ToolDiscovery:
    if not config.paths:
        return ToolDiscovery(kind=kind, state=ToolState.NOT_CONFIGURED, reason="not configured by the target project")
    for executable, args, trailing, version in (
        ("docker", ("compose", "version"), ("compose",), "compose_v2"),
        ("docker-compose", ("version",), (), "compose_v1"),
    ):
        resolved = find_system_executable(executable, workspace=workspace)
        if resolved is not None and _probe_external(str(resolved), args):
            return ToolDiscovery(
                kind=kind,
                state=ToolState.CONFIGURED_AVAILABLE,
                config_paths=config.paths,
                executable=str(resolved),
                command_prefix=(str(resolved), *trailing),
                details=(*config.details, version),
                reason=f"{executable!r} compose support is available",
            )
    return ToolDiscovery(
        kind=kind,
        state=ToolState.CONFIGURED_UNAVAILABLE,
        config_paths=config.paths,
        details=config.details,
        reason="Docker Compose is configured but no working compose command was found",
    )


def _probe_external(executable: str, args: tuple[str, ...]) -> bool:
    try:
        completed = subprocess.run(  # noqa: S603 - validated absolute executable, no shell
            (executable, *args),
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROBE_TIMEOUT_SECONDS,
            env=sanitize_environment(os.environ),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _target_python(workspace: TargetWorkspace, project: DjangoProjectInfo) -> Path | None:
    if project.python_executable is None:
        return None
    return resolve_workspace_executable(
        workspace,
        project.python_executable,
        allow_venv_interpreter=True,
    )


def _venv_executable(workspace: TargetWorkspace, bin_dir: Path, kind: ToolKind) -> Path | None:
    name = EXECUTABLE_NAMES.get(kind)
    if name is None:
        return None
    for candidate in (bin_dir / name, bin_dir / f"{name}.exe"):
        resolved = resolve_workspace_executable(workspace, candidate)
        if resolved is not None:
            return resolved
    return None


def module_for(kind: ToolKind) -> str | None:
    """Return the Python module name backing a tool kind, if any."""
    return MODULE_NAMES.get(kind)


def tool_order() -> tuple[ToolKind, ...]:
    """Return the deterministic default execution order."""
    return DEFAULT_TOOL_ORDER


def probe_importable(module: str) -> bool:
    """Return whether a module is importable by the current interpreter."""
    return importlib.util.find_spec(module) is not None
