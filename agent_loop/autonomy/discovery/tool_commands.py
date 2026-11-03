"""Discover the target project's documented tool commands."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.compose import evidence_files
from agent_loop.autonomy.discovery.compose import join_continuations

if TYPE_CHECKING:
    from pathlib import Path

    from agent_loop.autonomy.security import TargetWorkspace

_EXEC_COMMAND = re.compile(
    r"\b(?:exec|run)(?:\s+--?[a-zA-Z0-9-]+(?:=[^\s]+)?)*\s+(?:-T\s+)?([a-zA-Z0-9_.-]+)\s+([^\n]+)",
)
_PYTHON_MODULE_MIN_PARTS = 3
_COVERAGE_MIN_PARTS = 5
_TOOL_PREFIXES = (
    (("python", "manage.py", "behave"), "behave"),
    (("python3", "manage.py", "behave"), "behave"),
    (("python", "-m"), "python-module"),
    (("python3", "-m"), "python-module"),
    (("pytest",), "pytest"),
    (("mypy",), "mypy"),
    (("ruff",), "ruff"),
    (("pylint",), "pylint"),
    (("bandit",), "bandit"),
    (("pre-commit",), "pre_commit"),
    (("coverage", "run", "-m"), "coverage-module"),
)


@dataclass(frozen=True, slots=True)
class DocumentedCommand:
    """One tool command explicitly documented by the target project."""

    tool: str
    args: tuple[str, ...]
    source: str
    service: str | None = None


def discover_documented_commands(
    workspace: TargetWorkspace,
    services: tuple[str, ...],
) -> tuple[DocumentedCommand, ...]:
    """Return the first documented Compose command per tool kind."""
    found: dict[str, DocumentedCommand] = {}
    for path in evidence_files(workspace):
        text = join_continuations(_read_text(path))
        for match in _EXEC_COMMAND.finditer(text):
            service, raw = match.group(1), match.group(2)
            if services and service not in services:
                continue
            command = _parse_command(raw)
            if command is None:
                continue
            kind, args, inner_service = _classify(command, service)
            if kind is None or kind in found:
                continue
            found[kind] = DocumentedCommand(
                tool=kind,
                args=args,
                source=path.relative_to(workspace.root).as_posix(),
                service=inner_service,
            )
    return tuple(found[kind] for kind in sorted(found))


def _parse_command(raw: str) -> tuple[str, ...] | None:
    text = raw.strip()
    for separator in ("&&", "||", "|", ";"):
        text = text.split(separator, 1)[0].strip()
    try:
        parts = tuple(shlex.split(text))
    except ValueError:
        return None
    return parts or None


def _classify(command: tuple[str, ...], service: str) -> tuple[str | None, tuple[str, ...], str | None]:
    for prefix, kind in _TOOL_PREFIXES:
        if command[: len(prefix)] != prefix:
            continue
        if kind == "python-module":
            return _python_module(command)
        if kind == "coverage-module":
            return _coverage_module(command)
        return kind, command, service
    return None, command, None


def _python_module(command: tuple[str, ...]) -> tuple[str | None, tuple[str, ...], None]:
    if len(command) < _PYTHON_MODULE_MIN_PARTS:
        return None, command, None
    module = command[2]
    return (module if module in {"pytest", "mypy", "ruff", "pylint", "bandit"} else None), command, None


def _coverage_module(command: tuple[str, ...]) -> tuple[str | None, tuple[str, ...], None]:
    if len(command) < _COVERAGE_MIN_PARTS or command[3] != "-m":
        return None, command, None
    return ("pytest" if command[4] == "pytest" else None), command, None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
