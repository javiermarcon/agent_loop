"""Detect tool configuration files and dependencies in the target repository."""

from __future__ import annotations

import configparser
import tomllib
from collections.abc import Mapping
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.tool_models import EXECUTABLE_NAMES
from agent_loop.autonomy.discovery.tool_models import MODULE_NAMES
from agent_loop.autonomy.discovery.tool_models import ToolKind
from agent_loop.autonomy.errors import SecurityError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from agent_loop.autonomy.discovery.django_project import DjangoProjectInfo
    from agent_loop.autonomy.security import TargetWorkspace


def config_paths_for(
    workspace: TargetWorkspace,
    kind: ToolKind,
    project: DjangoProjectInfo | None = None,
) -> tuple[str, ...]:
    """Return the configuration paths that make a tool applicable."""
    handlers: dict[ToolKind, Callable[[TargetWorkspace], tuple[str, ...]]] = {
        ToolKind.GIT: _git_config,
        ToolKind.DOCKER_COMPOSE: _compose_config,
        ToolKind.PRE_COMMIT: _precommit_config,
        ToolKind.RUFF: _ruff_config,
        ToolKind.MYPY: _mypy_config,
        ToolKind.PYLINT: _pylint_config,
        ToolKind.BANDIT: _bandit_config,
        ToolKind.PYTEST: _pytest_config,
        ToolKind.BEHAVE: _behave_config,
    }
    if kind is ToolKind.DOCKER_COMPOSE and project is not None and project.compose_files:
        return project.compose_files
    handler = handlers.get(kind)
    return handler(workspace) if handler is not None else ()


def config_details_for(workspace: TargetWorkspace, kind: ToolKind) -> tuple[str, ...]:
    """Return supplementary configuration evidence for a tool kind."""
    if kind is not ToolKind.PRE_COMMIT:
        return ()
    return _pre_commit_hooks(workspace)


def is_tool_not_required(
    workspace: TargetWorkspace,
    project: DjangoProjectInfo,
    kind: ToolKind,
) -> bool:
    """Return True when a config file exists but the project neither installs nor invokes the tool."""
    if kind in {ToolKind.PYTEST, ToolKind.BEHAVE}:
        return False
    names = {MODULE_NAMES.get(kind, ""), EXECUTABLE_NAMES.get(kind, "")} - {""}
    documented = tuple(command for command in project.documented_commands if command.tool == kind.value)
    if documented:
        return False
    texts = [_dependency_text(workspace, dependency.relative_path) for dependency in project.dependency_files]
    texts.append(_read_text(_workspace_file(workspace, ".pre-commit-config.yaml")))
    texts.append(_read_text(_workspace_file(workspace, ".pre-commit-config.yml")))
    return not any(name in text for name in names for text in texts)


def _dependency_text(workspace: TargetWorkspace, name: str) -> str:
    """Return declared dependency strings, not unrelated tool configuration sections."""
    if not name.endswith("pyproject.toml"):
        return _read_text(_workspace_file(workspace, name))
    data = _read_toml(workspace, name)
    project = data.get("project")
    strings: list[str] = []
    if isinstance(project, dict):
        for key in ("dependencies", "optional-dependencies", "dependency-groups"):
            strings.extend(_toml_strings(project.get(key)))
    build = data.get("build-system")
    if isinstance(build, dict):
        strings.extend(_toml_strings(build.get("requires")))
    poetry = data.get("tool")
    if isinstance(poetry, dict):
        dependencies = poetry.get("poetry")
        if isinstance(dependencies, dict):
            strings.extend(_toml_strings(dependencies.get("dependencies")))
    return "\n".join(strings)


def _toml_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _toml_strings(nested)]
    return []


def _git_config(workspace: TargetWorkspace) -> tuple[str, ...]:
    return (".git",) if (workspace.root / ".git").exists() else ()


def _compose_config(workspace: TargetWorkspace) -> tuple[str, ...]:
    names = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
    return tuple(name for name in names if _workspace_file(workspace, name) is not None)


def _precommit_config(workspace: TargetWorkspace) -> tuple[str, ...]:
    names = (".pre-commit-config.yaml", ".pre-commit-config.yml")
    return tuple(name for name in names if _workspace_file(workspace, name) is not None)


def _ruff_config(workspace: TargetWorkspace) -> tuple[str, ...]:
    paths = ["pyproject.toml"] if _toml_table(workspace, ("tool", "ruff")) else []
    paths.extend(name for name in ("ruff.toml", ".ruff.toml") if _workspace_file(workspace, name) is not None)
    return tuple(paths)


def _mypy_config(workspace: TargetWorkspace) -> tuple[str, ...]:
    paths = ["pyproject.toml"] if _toml_table(workspace, ("tool", "mypy")) else []
    paths.extend(name for name in ("mypy.ini", ".mypy.ini") if _workspace_file(workspace, name) is not None)
    paths.extend(
        name
        for name, section in (("setup.cfg", "mypy"), ("tox.ini", "mypy"))
        if _ini_section(workspace, name, section)
    )
    return tuple(paths)


def _pylint_config(workspace: TargetWorkspace) -> tuple[str, ...]:
    paths = ["pyproject.toml"] if _toml_table(workspace, ("tool", "pylint")) else []
    paths.extend(name for name in (".pylintrc", "pylintrc") if _workspace_file(workspace, name) is not None)
    paths.extend(
        name
        for name, section in (("setup.cfg", "pylint"), ("tox.ini", "pylint"))
        if _ini_section(workspace, name, section)
    )
    return tuple(paths)


def _bandit_config(workspace: TargetWorkspace) -> tuple[str, ...]:
    paths = ["pyproject.toml"] if _toml_table(workspace, ("tool", "bandit")) else []
    paths.extend(
        name for name in (".bandit", "bandit.yml", "bandit.yaml") if _workspace_file(workspace, name) is not None
    )
    return tuple(paths)


def _pytest_config(workspace: TargetWorkspace) -> tuple[str, ...]:
    paths = ["pyproject.toml"] if _toml_table(workspace, ("tool", "pytest", "ini_options")) else []
    paths.extend(name for name in ("pytest.ini", "conftest.py") if _workspace_file(workspace, name) is not None)
    paths.extend(
        name
        for name, section in (("setup.cfg", "tool:pytest"), ("tox.ini", "pytest"))
        if _ini_section(workspace, name, section)
    )
    if _workspace_dir(workspace, "tests") is not None:
        paths.append("tests/")
    return tuple(paths)


def _behave_config(workspace: TargetWorkspace) -> tuple[str, ...]:
    paths = [name for name in ("behave.ini", ".behaverc") if _workspace_file(workspace, name) is not None]
    paths.extend(
        name
        for name, section in (("setup.cfg", "behave"), ("tox.ini", "behave"))
        if _ini_section(workspace, name, section)
    )
    if _workspace_dir(workspace, "features") is not None:
        paths.append("features/")
    return tuple(paths)


def _pre_commit_hooks(workspace: TargetWorkspace) -> tuple[str, ...]:
    data = _load_pre_commit_data(workspace)
    if data is None:
        return ()
    repositories = data.get("repos")
    if not isinstance(repositories, list):
        return ()
    return tuple(hook for repository in repositories for hook in _repository_hooks(repository))


def _load_pre_commit_data(workspace: TargetWorkspace) -> Mapping[str, object] | None:
    config = _workspace_file(workspace, ".pre-commit-config.yaml")
    if config is None:
        config = _workspace_file(workspace, ".pre-commit-config.yml")
    if config is None:
        return None
    try:
        import yaml  # noqa: PLC0415 - optional dependency, isolated to pre-commit introspection
    except ImportError:
        return None
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, Mapping) else None


def _repository_hooks(repository: object) -> tuple[str, ...]:
    if not isinstance(repository, Mapping):
        return ()
    repo = repository.get("repo")
    entries = repository.get("hooks")
    if not isinstance(entries, list):
        return ()
    return tuple(hook for entry in entries if (hook := _hook_name(repo, entry)) is not None)


def _hook_name(repo: object, entry: object) -> str | None:
    if not isinstance(entry, Mapping):
        return None
    hook_id = entry.get("id")
    if not isinstance(hook_id, str):
        return None
    return f"{repo}:{hook_id}" if isinstance(repo, str) else hook_id


def _toml_table(workspace: TargetWorkspace, keys: tuple[str, ...]) -> bool:
    data = _read_toml(workspace, "pyproject.toml")
    current: object = data
    for key in keys:
        if not isinstance(current, Mapping):
            return False
        current = current.get(key)
    return isinstance(current, Mapping)


def _read_toml(workspace: TargetWorkspace, name: str) -> dict[str, object]:
    candidate = _workspace_file(workspace, name)
    if candidate is None:
        return {}
    try:
        with candidate.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _ini_section(workspace: TargetWorkspace, name: str, section: str) -> bool:
    candidate = _workspace_file(workspace, name)
    if candidate is None:
        return False
    parser = configparser.ConfigParser()
    try:
        parser.read(candidate, encoding="utf-8")
    except (OSError, configparser.Error):
        return False
    return parser.has_section(section)


def _read_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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
