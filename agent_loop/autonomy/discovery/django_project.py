"""Discover a Django project's layout, runtime and dependency metadata."""

from __future__ import annotations

import configparser
import re
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.compose import discover_compose
from agent_loop.autonomy.discovery.django_settings import discover_settings
from agent_loop.autonomy.discovery.tool_commands import discover_documented_commands
from agent_loop.autonomy.errors import SecurityError

if TYPE_CHECKING:
    from pathlib import Path

    from agent_loop.autonomy.discovery.compose import ComposeDiscovery
    from agent_loop.autonomy.discovery.django_settings import SettingsDiscovery
    from agent_loop.autonomy.discovery.tool_commands import DocumentedCommand
    from agent_loop.autonomy.security import TargetWorkspace

_SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "vendor",
    },
)
_DEPENDENCY_NAMES = (
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "uv.lock",
    "setup.cfg",
    "setup.py",
    "tox.ini",
)
_DJANGO_RE = re.compile(r"(?i)\bdjango\b\s*(?:\[[^\]]*\])?\s*[=~<>!]=?\s*([0-9][^\s,;\"']*)")
_DJANGO_FRAMEWORK_PREFIX = "django."
_LOCAL_APP_PARTS = 2


@dataclass(frozen=True, slots=True)
class DependencyFile:
    """One dependency or build file discovered in the target repository."""

    relative_path: str
    kind: str


@dataclass(frozen=True, slots=True)
class DjangoProjectInfo:
    """Normalized Django project discovery result."""

    is_django: bool
    manage_py: str | None
    settings_module: str | None
    settings_files: tuple[str, ...]
    apps: tuple[str, ...]
    python_version: str | None
    django_version: str | None
    dependency_files: tuple[DependencyFile, ...]
    virtualenv: str | None
    python_executable: str | None
    compose_files: tuple[str, ...]
    compose_project_files: tuple[str, ...]
    compose_error: str | None
    dockerfiles: tuple[str, ...]
    evidence: tuple[str, ...]
    pytest_testpaths: tuple[str, ...] = ()
    settings_candidates: tuple[str, ...] = ()
    settings_reason: str | None = None
    local_apps: tuple[str, ...] = ()
    third_party_apps: tuple[str, ...] = ()
    framework_apps: tuple[str, ...] = ()
    compose_reason: str | None = None
    compose_evidence: tuple[str, ...] = ()
    compose_service: str | None = None
    compose_service_reason: str | None = None
    compose_runtime_cwd: str | None = None
    documented_commands: tuple[DocumentedCommand, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "is_django": self.is_django,
            "manage_py": self.manage_py,
            "settings_module": self.settings_module,
            "settings_files": list(self.settings_files),
            "settings_candidates": list(self.settings_candidates),
            "settings_reason": self.settings_reason,
            "apps": list(self.apps),
            "local_apps": list(self.local_apps),
            "third_party_apps": list(self.third_party_apps),
            "framework_apps": list(self.framework_apps),
            "python_version": self.python_version,
            "django_version": self.django_version,
            "dependency_files": [{"path": item.relative_path, "kind": item.kind} for item in self.dependency_files],
            "virtualenv": self.virtualenv,
            "python_executable": self.python_executable,
            "compose_files": list(self.compose_files),
            "compose_project_files": list(self.compose_project_files),
            "compose_error": self.compose_error,
            "compose_reason": self.compose_reason,
            "compose_evidence": list(self.compose_evidence),
            "compose_service": self.compose_service,
            "compose_service_reason": self.compose_service_reason,
            "compose_runtime_cwd": self.compose_runtime_cwd,
            "dockerfiles": list(self.dockerfiles),
            "documented_commands": [
                {"tool": command.tool, "args": list(command.args), "source": command.source}
                for command in self.documented_commands
            ],
            "evidence": list(self.evidence),
            "pytest_testpaths": list(self.pytest_testpaths),
        }


def discover_django_project(workspace: TargetWorkspace) -> DjangoProjectInfo:
    """Discover a Django project without importing or executing target code."""
    manage_py = _first_file(workspace, ("manage.py",))
    compose = discover_compose(workspace)
    settings = discover_settings(workspace, manage_py, runtime_cwd=compose.runtime_cwd)
    local_apps, third_party_apps, framework_apps = _classify_apps(workspace, settings.apps)
    dependency_files = _dependency_files(workspace)
    python_version = _python_version(workspace)
    django_version = _django_version(workspace, dependency_files)
    virtualenv, python_executable = _virtualenv(workspace)
    dockerfiles = _dockerfiles(workspace)
    pytest_testpaths = _pytest_testpaths(workspace)
    documented_commands = discover_documented_commands(
        workspace,
        (compose.service,) if compose.service is not None else (),
    )
    evidence = (
        *_settings_evidence(workspace, manage_py, settings),
        *_runtime_evidence(dependency_files, python_version, django_version, virtualenv),
        *_compose_evidence(compose),
        *_documented_evidence(documented_commands),
    )
    return DjangoProjectInfo(
        is_django=bool(manage_py or settings.apps or django_version),
        manage_py=workspace.relative(manage_py) if manage_py is not None else None,
        settings_module=settings.module,
        settings_files=settings.files,
        apps=settings.apps,
        python_version=python_version,
        django_version=django_version,
        dependency_files=dependency_files,
        virtualenv=virtualenv,
        python_executable=python_executable,
        compose_files=compose.files,
        compose_project_files=compose.project_files,
        compose_error=compose.error,
        dockerfiles=dockerfiles,
        evidence=evidence,
        pytest_testpaths=pytest_testpaths,
        settings_candidates=settings.candidates,
        settings_reason=settings.reason,
        local_apps=local_apps,
        third_party_apps=third_party_apps,
        framework_apps=framework_apps,
        compose_reason=compose.reason,
        compose_evidence=compose.evidence,
        compose_service=compose.service,
        compose_service_reason=compose.service_reason,
        compose_runtime_cwd=compose.runtime_cwd,
        documented_commands=documented_commands,
    )


def _settings_evidence(
    workspace: TargetWorkspace,
    manage_py: Path | None,
    settings: SettingsDiscovery,
) -> list[str]:
    evidence: list[str] = []
    if manage_py is not None:
        evidence.append(f"manage.py: {workspace.relative(manage_py)}")
    if settings.module:
        evidence.append(f"settings module: {settings.module}")
    elif settings.reason:
        evidence.append(f"settings: {settings.reason}")
    if settings.files:
        evidence.append(f"settings: {', '.join(settings.files)}")
    if settings.apps:
        evidence.append(f"installed_apps: {len(settings.apps)}")
    return evidence


def _runtime_evidence(
    dependency_files: tuple[DependencyFile, ...],
    python_version: str | None,
    django_version: str | None,
    virtualenv: str | None,
) -> list[str]:
    evidence: list[str] = []
    if dependency_files:
        evidence.append(f"dependencies: {len(dependency_files)}")
    if python_version:
        evidence.append(f"python: {python_version}")
    if django_version:
        evidence.append(f"django: {django_version}")
    if virtualenv:
        evidence.append(f"virtualenv: {virtualenv}")
    return evidence


def _compose_evidence(compose: ComposeDiscovery) -> list[str]:
    evidence: list[str] = []
    if compose.project_files:
        evidence.append(f"compose: {compose.reason or ', '.join(compose.project_files)}")
    elif compose.error:
        evidence.append(f"compose: {compose.error}")
    if compose.service:
        evidence.append(f"compose service: {compose.service}")
    return evidence


def _documented_evidence(commands: tuple[DocumentedCommand, ...]) -> list[str]:
    return [f"documented commands: {len(commands)}"] if commands else []


def _classify_apps(
    workspace: TargetWorkspace,
    apps: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    local: list[str] = []
    third_party: list[str] = []
    framework: list[str] = []
    for app in apps:
        if app == "django" or app.startswith(_DJANGO_FRAMEWORK_PREFIX):
            framework.append(app)
        elif _is_local_app(workspace, app):
            local.append(app)
        else:
            third_party.append(app)
    return tuple(local), tuple(third_party), tuple(framework)


def _is_local_app(workspace: TargetWorkspace, app: str) -> bool:
    parts = app.split(".")
    if len(parts) < _LOCAL_APP_PARTS:
        return False
    try:
        candidate = workspace.resolve("/".join(parts))
    except SecurityError:
        return False
    return candidate.is_dir()


def _first_file(workspace: TargetWorkspace, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = _workspace_file(workspace, name)
        if candidate is not None:
            return candidate
    return None


def _workspace_file(workspace: TargetWorkspace, name: str) -> Path | None:
    try:
        candidate = workspace.resolve(name)
    except SecurityError:
        return None
    return candidate if candidate.is_file() else None


def _workspace_dir(workspace: TargetWorkspace, name: str) -> Path | None:
    try:
        candidate = workspace.resolve(name)
    except SecurityError:
        return None
    return candidate if candidate.is_dir() else None


def _dependency_files(workspace: TargetWorkspace) -> tuple[DependencyFile, ...]:
    files = [
        DependencyFile(relative_path=name, kind=_dependency_kind(name))
        for name in _DEPENDENCY_NAMES
        if _workspace_file(workspace, name) is not None
    ]
    requirements_dir = _workspace_dir(workspace, "requirements")
    if requirements_dir is not None:
        files.extend(
            DependencyFile(relative_path=candidate.relative_to(workspace.root).as_posix(), kind="requirements")
            for candidate in sorted(workspace.iter_files(skip_directories=_SKIP_DIRECTORIES))
            if candidate.parent == requirements_dir and candidate.suffix == ".txt"
        )
    return tuple(files)


def _dependency_kind(name: str) -> str:
    if name.startswith("requirements") or name == "Pipfile":
        return "requirements"
    if name in {"poetry.lock", "uv.lock", "Pipfile.lock"}:
        return "lock"
    return "build"


def _python_version(workspace: TargetWorkspace) -> str | None:
    for name in (".python-version", "runtime.txt"):
        candidate = _workspace_file(workspace, name)
        if candidate is not None:
            value = _read_text(candidate).strip().splitlines()
            if value:
                return value[0].removeprefix("python-").strip() or None
    pyproject = _read_pyproject(workspace)
    if pyproject is not None:
        project = pyproject.get("project")
        if isinstance(project, dict):
            requires = project.get("requires-python")
            if isinstance(requires, str):
                return requires
    return None


def _django_version(workspace: TargetWorkspace, dependency_files: tuple[DependencyFile, ...]) -> str | None:
    for dependency in dependency_files:
        candidate = _workspace_file(workspace, dependency.relative_path)
        if candidate is None:
            continue
        match = _DJANGO_RE.search(_read_text(candidate))
        if match:
            return match.group(1).strip()
    return None


def _virtualenv(workspace: TargetWorkspace) -> tuple[str | None, str | None]:
    root = workspace.root
    for name in (".venv", "venv", "env", ".virtualenv"):
        directory = _workspace_dir(workspace, name)
        if directory is None:
            continue
        for executable_name in ("bin/python", "Scripts/python.exe"):
            try:
                executable = workspace.member_path(f"{name}/{executable_name}")
            except SecurityError:
                continue
            if executable.is_file():
                return name, executable.relative_to(root).as_posix()
    return None, None


def _dockerfiles(workspace: TargetWorkspace) -> tuple[str, ...]:
    found = {
        candidate.relative_to(workspace.root).as_posix()
        for candidate in workspace.iter_files(skip_directories=_SKIP_DIRECTORIES)
        if candidate.parent == workspace.root and candidate.name.startswith("Dockerfile")
    }
    found.update(
        candidate.relative_to(workspace.root).as_posix()
        for candidate in _walk_named(workspace, "Dockerfile", limit=10)
    )
    return tuple(sorted(found))


def _pytest_testpaths(workspace: TargetWorkspace) -> tuple[str, ...]:
    values = _ini_testpaths(workspace, "pytest.ini", "pytest")
    if not values:
        values = _pyproject_testpaths(workspace)
    if not values:
        values = _ini_testpaths(workspace, "tox.ini", "pytest")
    if not values:
        values = _ini_testpaths(workspace, "setup.cfg", "tool:pytest")
    return values


def _pyproject_testpaths(workspace: TargetWorkspace) -> tuple[str, ...]:
    testpaths: object = None
    pyproject = _read_pyproject(workspace)
    if pyproject is not None:
        tool = pyproject.get("tool")
        if isinstance(tool, dict):
            pytest_config = tool.get("pytest")
            if isinstance(pytest_config, dict):
                ini_options = pytest_config.get("ini_options")
                if isinstance(ini_options, dict):
                    testpaths = ini_options.get("testpaths")
    if isinstance(testpaths, str):
        return (testpaths,)
    if isinstance(testpaths, list):
        return tuple(item for item in testpaths if isinstance(item, str))
    return ()


def _ini_testpaths(workspace: TargetWorkspace, name: str, section: str) -> tuple[str, ...]:
    candidate = _workspace_file(workspace, name)
    if candidate is None:
        return ()
    parser = configparser.ConfigParser()
    try:
        parser.read(candidate, encoding="utf-8")
    except (OSError, configparser.Error):
        return ()
    if not parser.has_option(section, "testpaths"):
        return ()
    raw = parser.get(section, "testpaths")
    return tuple(part.strip() for part in raw.replace(",", "\n").splitlines() if part.strip())


def _read_pyproject(workspace: TargetWorkspace) -> dict[str, object] | None:
    candidate = _workspace_file(workspace, "pyproject.toml")
    if candidate is None:
        return None
    try:
        with candidate.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _walk_named(
    workspace: TargetWorkspace,
    name: str,
    *,
    limit: int,
    parent_name: str | None = None,
) -> tuple[Path, ...]:
    found: list[Path] = []
    for path in workspace.iter_files(skip_directories=_SKIP_DIRECTORIES):
        if path.name != name:
            continue
        if parent_name is not None and path.parent.name != parent_name:
            continue
        found.append(path)
        if len(found) >= limit:
            break
    return tuple(found)
