"""Resolve Django settings and installed applications from static evidence."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_loop.autonomy.errors import SecurityError

if TYPE_CHECKING:
    from pathlib import Path

    from agent_loop.autonomy.security import TargetWorkspace

_SETTINGS_CANDIDATES = ("config/settings.py", "settings.py")
_IMPORT_DEPTH = 8
_SETDEFAULT_MIN_ARGS = 2
_TEXT_MODULE_RE = re.compile(r"DJANGO_SETTINGS_MODULE[\"']?\s*(?:,|])\s*[\"']([^\"']+)[\"']")


@dataclass(frozen=True, slots=True)
class SettingsDiscovery:
    """Settings module resolution with explicit ambiguity evidence."""

    module: str | None = None
    candidates: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    apps: tuple[str, ...] = ()
    reason: str | None = None


def discover_settings(
    workspace: TargetWorkspace,
    manage_py: Path | None,
    *,
    runtime_cwd: str | None = None,
) -> SettingsDiscovery:
    """Discover the settings module and apps without importing target code."""
    candidates: tuple[str, ...] = ()
    selected: str | None = None
    reason: str | None = None
    if manage_py is not None:
        candidates, selected, reason = _module_candidates(manage_py, runtime_cwd)
    module = selected
    if reason is None and module is None and len(candidates) > 1:
        reason = f"ambiguous Django settings modules: {', '.join(candidates)}"
    files = _settings_files(workspace, module)
    apps = _installed_apps(files)
    return SettingsDiscovery(
        module=module,
        candidates=candidates,
        files=tuple(path.relative_to(workspace.root).as_posix() for path in files),
        apps=apps,
        reason=reason,
    )


def _module_candidates(
    manage_py: Path,
    runtime_cwd: str | None,
) -> tuple[tuple[str, ...], str | None, str | None]:
    text = _read_text(manage_py)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        match = _TEXT_MODULE_RE.search(text)
        candidate = (match.group(1),) if match else ()
        return candidate, (candidate[0] if candidate else None), None
    assignments = _string_assignments(tree)
    nodes = _settings_value_nodes(tree)
    values: list[str] = []
    for node in nodes:
        values.extend(_resolve_value(node, assignments))
    if not values:
        match = _TEXT_MODULE_RE.search(text)
        candidate = (match.group(1),) if match else ()
        return candidate, (candidate[0] if candidate else None), None
    candidates = tuple(dict.fromkeys(values))
    if len(candidates) == 1:
        return candidates, candidates[0], None
    selected = _select_by_runtime(tree, nodes, runtime_cwd)
    if selected is not None:
        return candidates, selected, None
    if runtime_cwd is None:
        return candidates, None, None
    return candidates, None, f"no settings branch matches the runtime working directory {runtime_cwd!r}"


def _string_assignments(tree: ast.AST) -> dict[str, list[str]]:
    assignments: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    values = _string_items(node.value)
                    if values:
                        assignments.setdefault(target.id, []).extend(values)
    return assignments


def _settings_value_nodes(tree: ast.AST) -> tuple[ast.expr, ...]:
    nodes: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_setdefault(node) and len(node.args) >= _SETDEFAULT_MIN_ARGS:
            key = node.args[0]
            if isinstance(key, ast.Constant) and key.value == "DJANGO_SETTINGS_MODULE":
                nodes.append(node.args[1])
        if isinstance(node, ast.Assign):
            nodes.extend(node.value for target in node.targets if _is_settings_subscript(target))
    return tuple(nodes)


def _is_setdefault(node: ast.Call) -> bool:
    function = node.func
    return isinstance(function, ast.Attribute) and function.attr == "setdefault"


def _is_settings_subscript(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "DJANGO_SETTINGS_MODULE"
    )


def _resolve_value(node: ast.expr, assignments: dict[str, list[str]]) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        return list(assignments.get(node.id, ()))
    return []


def _select_by_runtime(
    tree: ast.AST,
    nodes: tuple[ast.expr, ...],
    runtime_cwd: str | None,
) -> str | None:
    if runtime_cwd is None:
        return None
    names = {node.id for node in nodes if isinstance(node, ast.Name)}
    if len(names) != 1:
        return None
    branches = _branches(tree, names.pop())
    matching = [module for condition, module in branches if _condition_matches(condition, runtime_cwd)]
    defaults = [module for condition, module in branches if condition is None]
    if len(matching) == 1:
        return matching[0]
    if not matching and len(defaults) == 1:
        return defaults[0]
    return None


def _branches(tree: ast.AST, name: str) -> tuple[tuple[tuple[str, str] | None, str], ...]:
    branches: list[tuple[tuple[str, str] | None, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        module = _assigned_module(node.body, name)
        if module is not None:
            condition = _condition(node.test)
            if condition is not None:
                branches.append((condition, module))
        default = _assigned_module(node.orelse, name)
        if default is not None and not any(isinstance(item, ast.If) for item in node.orelse):
            branches.append((None, default))
    return tuple(branches)


def _assigned_module(body: list[ast.stmt], name: str) -> str | None:
    for node in body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            values = _string_items(node.value)
            if values:
                return values[0]
    return None


def _condition(test: ast.expr) -> tuple[str, str] | None:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    left, right, operator = test.left, test.comparators[0], test.ops[0]
    if (
        isinstance(left, ast.Name)
        and isinstance(right, ast.Constant)
        and isinstance(right.value, str)
        and isinstance(operator, ast.Eq)
    ):
        return ("equals", right.value)
    if (
        isinstance(left, ast.Constant)
        and isinstance(left.value, str)
        and isinstance(right, ast.Name)
        and isinstance(operator, ast.In)
    ):
        return ("contains", left.value)
    return None


def _condition_matches(condition: tuple[str, str] | None, runtime_cwd: str) -> bool:
    if condition is None:
        return False
    kind, value = condition
    normalized_cwd = runtime_cwd.rstrip("/") or "/"
    normalized_value = value.rstrip("/") or "/"
    if kind == "equals":
        return normalized_cwd == normalized_value
    return value in runtime_cwd


def _settings_files(workspace: TargetWorkspace, module: str | None) -> tuple[Path, ...]:
    direct = _module_files(workspace, module) if module else ()
    if not direct:
        direct = tuple(
            candidate for name in _SETTINGS_CANDIDATES if (candidate := _workspace_file(workspace, name)) is not None
        )
    if not direct:
        direct = _walk_named(workspace, "settings.py", limit=10)
    files = _follow_imports(workspace, direct)
    return tuple(sorted(dict.fromkeys(files)))


def _module_files(workspace: TargetWorkspace, module: str) -> tuple[Path, ...]:
    relative = module.replace(".", "/")
    found: list[Path] = []
    for name in (f"{relative}.py", f"{relative}/__init__.py"):
        candidate = _workspace_file(workspace, name)
        if candidate is not None:
            found.append(candidate)
    return tuple(found)


def _follow_imports(workspace: TargetWorkspace, seeds: tuple[Path, ...]) -> tuple[Path, ...]:
    queue: list[tuple[Path, int]] = [(path, 0) for path in seeds]
    seen: dict[Path, None] = {}
    while queue:
        path, depth = queue.pop(0)
        if path in seen:
            continue
        seen[path] = None
        if depth >= _IMPORT_DEPTH:
            continue
        queue.extend((module, depth + 1) for module in _local_imports(workspace, path))
    return tuple(seen)


def _local_imports(workspace: TargetWorkspace, path: Path) -> tuple[Path, ...]:
    try:
        tree = ast.parse(_read_text(path))
    except SyntaxError:
        return ()
    relative = path.relative_to(workspace.root).as_posix()
    module_path = relative.removesuffix(".py")
    if module_path.endswith("/__init__"):
        package = module_path.removesuffix("/__init__").split("/")
    else:
        package = module_path.split("/")[:-1]
    found: list[Path] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        parts = _import_parts(package, node.level, node.module)
        if parts is None:
            continue
        candidate = _workspace_file(workspace, f"{'/'.join(parts)}.py") or _workspace_file(
            workspace,
            f"{'/'.join(parts)}/__init__.py",
        )
        if candidate is not None:
            found.append(candidate)
    return tuple(found)


def _import_parts(package: list[str], level: int, module: str) -> list[str] | None:
    module_parts = module.split(".")
    if level == 0:
        return module_parts
    base = package[: len(package) - (level - 1)] if level > 1 else list(package)
    if level - 1 >= len(package):
        return None
    return [*base, *module_parts]


def _installed_apps(files: tuple[Path, ...]) -> tuple[str, ...]:
    apps: list[str] = []
    for path in files:
        apps.extend(_apps_in_file(path))
    return tuple(sorted(dict.fromkeys(apps)))


def _apps_in_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(_read_text(path))
    except SyntaxError:
        return []
    constants = _string_constants(tree)
    apps: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "INSTALLED_APPS" for target in targets):
                apps.extend(_string_items(node.value, constants))
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "INSTALLED_APPS"
        ):
            apps.extend(_string_items(node.value, constants))
    return apps


def _string_constants(tree: ast.AST) -> dict[str, list[str]]:
    constants: dict[str, list[str]] = {}
    for _ in range(3):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    values = _string_items(node.value, constants)
                    if values:
                        constants[target.id] = values
    return constants


def _string_items(node: ast.AST | None, constants: dict[str, list[str]] | None = None) -> list[str]:
    if node is None:
        return []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name) and constants is not None:
        return list(constants.get(node.id, ()))
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return [*_string_items(node.left, constants), *_string_items(node.right, constants)]
    return []


def _walk_named(workspace: TargetWorkspace, name: str, *, limit: int) -> tuple[Path, ...]:
    found: list[Path] = []
    for candidate in workspace.iter_files(skip_directories=_SKIP_DIRECTORIES):
        if candidate.name != name:
            continue
        found.append(candidate)
        if len(found) >= limit:
            break
    return tuple(found)


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


def _workspace_file(workspace: TargetWorkspace, name: str) -> Path | None:
    try:
        candidate = workspace.resolve(name)
    except SecurityError:
        return None
    return candidate if candidate.is_file() else None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
