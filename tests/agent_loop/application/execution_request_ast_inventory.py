"""AST inventory helpers for S16 execution-request construction/consumers.

Static analysis only. Distinguishes import, symbol reference, and invocation.
Does not analyze dataflow from builder results to ``AgentRuntime.execute``.
Does not prove runtime integration or its absence. Not a security guarantee.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

BUILDER_MODULE_NAME = "agent_loop.application.execution_request_builder"
BUILDER_SYMBOLS = frozenset({"build_execution_request", "ExecutionRequestOptions"})
BUILD_FUNCTION = "build_execution_request"
AGENT_RUNTIME_MODULE = "agent_loop.ports.agent_runtime"
REQUEST_SYMBOL = "AgentExecutionRequest"
REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_REQUEST_CTORS = frozenset(
    {
        "agent_loop/application/execution_request_builder.py",
    },
)
TEST_REQUEST_CTORS = frozenset(
    {
        "tests/agent_loop/adapters/claude/test_runtime.py",
        "tests/agent_loop/adapters/opencode/test_runtime.py",
        "tests/agent_loop/adapters/opencode_server/test_coverage_edges.py",
        "tests/agent_loop/adapters/opencode_server/test_identity.py",
        "tests/agent_loop/adapters/opencode_server/test_integration.py",
        "tests/agent_loop/adapters/opencode_server/test_runtime.py",
        "tests/agent_loop/application/test_execution_request_builder.py",
        "tests/agent_loop/ports/test_agent_runtime.py",
    },
)


def package_name_for_source_file(path: Path, *, repo_root: Path = REPO_ROOT) -> str:
    """Return ``__package__`` equivalent for ``path`` under ``repo_root``."""
    rel = path.resolve().relative_to(repo_root.resolve())
    parts = list(rel.parts)
    if not parts or not parts[-1].endswith(".py"):
        message = f"expected a .py path under repo root, got {path}"
        raise ValueError(message)
    if parts[-1] == "__init__.py":
        return ".".join(parts[:-1])
    return ".".join(parts[:-1])


def resolve_import_from(package: str, node: ast.ImportFrom) -> str | None:
    """Resolve an ``ImportFrom`` to an absolute module name (CPython-style)."""
    if node.level == 0:
        return node.module
    if not package:
        return None
    bits = package.rsplit(".", node.level - 1) if node.level > 1 else [package]
    if len(bits) < node.level:
        return None
    base = bits[0]
    if node.module:
        return f"{base}.{node.module}"
    return base


def parse_source(source: str) -> ast.AST:
    """Parse ``source``; syntax errors propagate (never swallowed)."""
    return ast.parse(source)


def import_bound_name(alias: ast.alias) -> str:
    """Return the local name bound by an ``import`` alias (Python semantics).

    ``import a.b.c`` binds ``a``. ``import a.b.c as x`` binds ``x``.
    """
    if alias.asname is not None:
        return alias.asname
    return alias.name.split(".", maxsplit=1)[0]


def import_access_prefix(alias: ast.alias) -> str:
    """Return the dotted prefix used to access an imported module in source.

    ``import a.b.c`` → ``a.b.c`` (accessed as ``a.b.c``).
    ``import a.b.c as x`` → ``x``.
    """
    if alias.asname is not None:
        return alias.asname
    return alias.name


def dotted_expr_name(node: ast.AST) -> str | None:
    """Return a dotted name for ``Name`` / ``Attribute`` chains, else ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_expr_name(node.value)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    return None


def imported_request_name_aliases(tree: ast.AST, *, package: str) -> set[str]:
    """Local names bound to ``AgentExecutionRequest`` via ImportFrom only.

    The bare name is included only when imported from ``agent_runtime``.
    Assignment aliases and wildcards are not expanded.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if resolve_import_from(package, node) != AGENT_RUNTIME_MODULE:
            continue
        for alias in node.names:
            if alias.name == REQUEST_SYMBOL:
                aliases.add(alias.asname or alias.name)
    return aliases


def imported_runtime_module_prefixes(tree: ast.AST, *, package: str) -> set[str]:
    """Dotted access prefixes bound to ``agent_loop.ports.agent_runtime``."""
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == AGENT_RUNTIME_MODULE:
                    prefixes.add(import_access_prefix(alias))
        elif isinstance(node, ast.ImportFrom):
            resolved = resolve_import_from(package, node)
            if resolved == "agent_loop.ports":
                for alias in node.names:
                    if alias.name == "agent_runtime":
                        prefixes.add(alias.asname or alias.name)
    return prefixes


def calls_agent_execution_request(tree: ast.AST, *, package: str = "") -> bool:
    """Detect ``AgentExecutionRequest`` construction with known provenance.

    Covered:
    - ``AgentExecutionRequest(...)`` / aliases from ``from …agent_runtime import``;
    - ``<recognized_module>.AgentExecutionRequest(...)`` where the module prefix
      comes from a recognized import (absolute/relative/aliased).

    Not covered: arbitrary ``something.AgentExecutionRequest(...)``, assignment
    aliases, ``getattr``, ``eval``/``exec``, wildcard expansion.
    """
    name_aliases = imported_request_name_aliases(tree, package=package)
    module_prefixes = imported_runtime_module_prefixes(tree, package=package)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in name_aliases:
            return True
        if isinstance(func, ast.Attribute) and func.attr == REQUEST_SYMBOL:
            prefix = dotted_expr_name(func.value)
            if prefix is not None and prefix in module_prefixes:
                return True
    return False


def agent_execution_request_ctor_paths(*, repo_root: Path = REPO_ROOT) -> set[str]:
    """Return relative paths that construct ``AgentExecutionRequest`` in source.

    Scans ``agent_loop/``, ``scripts/``, and ``tests/agent_loop/``. Parse errors
    are not ignored.
    """
    found: set[str] = set()
    search_roots = (
        repo_root / "agent_loop",
        repo_root / "scripts",
        repo_root / "tests" / "agent_loop",
    )
    for root in search_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            tree = parse_source(path.read_text(encoding="utf-8"))
            package = package_name_for_source_file(path, repo_root=repo_root)
            if calls_agent_execution_request(tree, package=package):
                found.add(str(path.relative_to(repo_root)))
    return found


def _record_builder_import_from(
    node: ast.ImportFrom,
    *,
    package: str,
    symbol_aliases: set[str],
    module_prefixes: set[str],
) -> bool:
    """Record builder ImportFrom bindings; return True if a wildcard was seen."""
    resolved = resolve_import_from(package, node)
    if resolved == BUILDER_MODULE_NAME:
        saw_wildcard = False
        for alias in node.names:
            if alias.name == "*":
                saw_wildcard = True
            elif alias.name in BUILDER_SYMBOLS:
                symbol_aliases.add(alias.asname or alias.name)
        return saw_wildcard
    if resolved == "agent_loop.application":
        for alias in node.names:
            if alias.name == "execution_request_builder":
                module_prefixes.add(alias.asname or alias.name)
    return False


def builder_module_and_symbol_bindings(
    tree: ast.AST,
    *,
    package: str,
) -> tuple[set[str], set[str], bool]:
    """Return ``(symbol_aliases, module_access_prefixes, wildcard_import)``.

    A builder ``import *`` sets ``wildcard_import`` without inventing symbol
    aliases. Wildcards of unrelated modules are ignored.
    """
    symbol_aliases: set[str] = set()
    module_prefixes: set[str] = set()
    wildcard_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == BUILDER_MODULE_NAME:
                    module_prefixes.add(import_access_prefix(alias))
        elif isinstance(node, ast.ImportFrom):
            if _record_builder_import_from(
                node,
                package=package,
                symbol_aliases=symbol_aliases,
                module_prefixes=module_prefixes,
            ):
                wildcard_import = True
    return symbol_aliases, module_prefixes, wildcard_import


def source_imports_execution_request_builder(source: str, *, package: str) -> bool:
    """True when ``source`` imports the builder module, symbols, or wildcard.

    A ``from …execution_request_builder import *`` counts as an import. It does
    not invent resolvable symbol aliases for reference/invocation detectors.
    """
    tree = parse_source(source)
    symbol_aliases, module_prefixes, wildcard_import = builder_module_and_symbol_bindings(
        tree,
        package=package,
    )
    return bool(symbol_aliases or module_prefixes or wildcard_import)


def _name_loads(tree: ast.AST) -> set[str]:
    """Return ``Name`` ids used in Load context (not import bindings alone)."""
    loads: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.add(node.id)
    return loads


def source_references_builder_symbols(source: str, *, package: str) -> bool:
    """True when a known builder symbol is referenced with static provenance.

    Known symbols: ``build_execution_request``, ``ExecutionRequestOptions``.
    Covered: direct/aliased ImportFrom bindings and
    ``<recognized_module_prefix>.<symbol>`` attribute access.

    A bare load of an imported module name alone is not a symbol reference.
    Wildcard imports do not invent unresolved aliases. Does not require a call.
    """
    tree = parse_source(source)
    symbol_aliases, module_prefixes, _wildcard = builder_module_and_symbol_bindings(
        tree,
        package=package,
    )
    loads = _name_loads(tree)
    if symbol_aliases & loads:
        return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        prefix = dotted_expr_name(node.value)
        if prefix is None or prefix not in module_prefixes:
            continue
        if node.attr in BUILDER_SYMBOLS:
            return True
    return False


def _build_function_aliases(tree: ast.AST, *, package: str) -> set[str]:
    """Local names bound specifically to ``build_execution_request``."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if resolve_import_from(package, node) != BUILDER_MODULE_NAME:
            continue
        for alias in node.names:
            if alias.name == BUILD_FUNCTION:
                aliases.add(alias.asname or alias.name)
    return aliases


def source_invokes_build_execution_request(source: str, *, package: str) -> bool:
    """True when ``build_execution_request`` is called via a recognized binding.

    Invocation is not proof of adapter dispatch or real orchestration.
    Wildcard imports alone do not establish call provenance.
    """
    tree = parse_source(source)
    _, module_prefixes, _wildcard = builder_module_and_symbol_bindings(
        tree,
        package=package,
    )
    build_aliases = _build_function_aliases(tree, package=package)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in build_aliases:
            return True
        if isinstance(func, ast.Attribute) and func.attr == BUILD_FUNCTION:
            prefix = dotted_expr_name(func.value)
            if prefix is not None and prefix in module_prefixes:
                return True
    return False


def application_modules_importing_builder(*, repo_root: Path = REPO_ROOT) -> list[str]:
    """Application modules (excl. builder) that import the builder (any form).

    Scan root: ``agent_loop/application/`` only (not ``scripts/`` / full tree).
    """
    return _scan_application_modules(
        repo_root=repo_root,
        predicate=source_imports_execution_request_builder,
    )


def application_modules_invoking_builder(*, repo_root: Path = REPO_ROOT) -> list[str]:
    """Application modules (excl. builder) that call ``build_execution_request``.

    Scan root: ``agent_loop/application/`` only (not ``scripts/`` / full tree).
    """
    return _scan_application_modules(
        repo_root=repo_root,
        predicate=source_invokes_build_execution_request,
    )


def _scan_application_modules(
    *,
    repo_root: Path,
    predicate: Callable[..., bool],
) -> list[str]:
    application_root = repo_root / "agent_loop" / "application"
    matched: list[str] = []
    for path in sorted(application_root.rglob("*.py")):
        if path.name == "execution_request_builder.py":
            continue
        source = path.read_text(encoding="utf-8")
        package = package_name_for_source_file(path, repo_root=repo_root)
        if predicate(source, package=package):
            matched.append(str(path.relative_to(repo_root)))
    return matched
