"""Discover repository instructions and architectural documentation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent_loop.autonomy.errors import SecurityError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Sequence

    from agent_loop.autonomy.security import TargetWorkspace

_SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
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
_ARCHITECTURE_NAME_MARKERS = (
    "architecture",
    "contributing",
    "conventions",
    "style",
    "testing",
)
_MAX_DOCUMENTS = 80


@dataclass(frozen=True, slots=True)
class InstructionDocument:
    """One instruction or documentation file with its applicability scope."""

    relative_path: str
    kind: str
    scope: str
    content: str

    def applies_to(self, relative_path: str) -> bool:
        """Return whether this document governs a repository-relative path."""
        if not self.scope:
            return True
        return relative_path == self.scope or relative_path.startswith(f"{self.scope}/")


@dataclass(frozen=True, slots=True)
class InstructionSet:
    """Deterministically ordered instructions discovered in a repository."""

    documents: tuple[InstructionDocument, ...]

    def for_paths(self, paths: Sequence[str]) -> tuple[InstructionDocument, ...]:
        """Return documents applicable to the given repository paths.

        An empty path list means "no file-specific filtering": every
        discovered document is returned. Otherwise root documents and the
        ancestor ``AGENTS.md`` chain for each path are kept.
        """
        if not paths:
            return self.documents
        selected = [document for document in self.documents if any(document.applies_to(path) for path in paths)]
        return tuple(sorted(selected, key=_document_order))

    def render(self, paths: Sequence[str] | None = None) -> str:
        """Render the applicable instructions as a prompt fragment."""
        selected = self.for_paths(paths or ())
        blocks = [
            f"### {document.relative_path} ({document.kind}, scope: {document.scope or '.'})\n"
            f"{document.content.strip()}"
            for document in selected
        ]
        return "\n\n".join(blocks)


def discover_instructions(workspace: TargetWorkspace) -> InstructionSet:
    """Discover ``AGENTS.md``, README and architecture/testing docs."""
    documents: list[InstructionDocument] = []
    seen: set[str] = set()

    for path in _iter_named(workspace, "AGENTS.md"):
        _append_document(documents, seen, workspace, path, kind="agents")
    for path in _root_named(workspace, ("README.md", "README.rst")):
        _append_document(documents, seen, workspace, path, kind="readme")
    for path in _iter_architecture_docs(workspace):
        _append_document(documents, seen, workspace, path, kind="architecture")

    ordered = tuple(sorted(documents, key=_document_order)[:_MAX_DOCUMENTS])
    return InstructionSet(documents=ordered)


def _append_document(
    documents: list[InstructionDocument],
    seen: set[str],
    workspace: TargetWorkspace,
    path: Path,
    *,
    kind: str,
) -> None:
    try:
        relative = workspace.relative(path)
    except SecurityError:
        return
    if relative in seen:
        return
    seen.add(relative)
    content = _read_text(path)
    if not content.strip():
        return
    scope = Path(relative).parent.as_posix()
    if scope == ".":
        scope = ""
    documents.append(
        InstructionDocument(
            relative_path=relative,
            kind=kind,
            scope=scope,
            content=content,
        ),
    )


def _document_order(document: InstructionDocument) -> tuple[int, str, str]:
    depth = 0 if not document.scope else document.scope.count("/") + 1
    return (depth, document.scope, document.relative_path)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _root_named(workspace: TargetWorkspace, names: Sequence[str]) -> Iterable[Path]:
    for name in names:
        try:
            candidate = workspace.resolve(name)
        except SecurityError:
            continue
        if candidate.is_file():
            yield candidate


def _iter_named(workspace: TargetWorkspace, name: str) -> Iterable[Path]:
    count = 0
    for path in workspace.iter_files(skip_directories=_SKIP_DIRECTORIES):
        if path.name != name:
            continue
        yield path
        count += 1
        if count >= _MAX_DOCUMENTS:
            return


def _iter_architecture_docs(workspace: TargetWorkspace) -> Iterable[Path]:
    count = 0
    for path in workspace.iter_files(skip_directories=_SKIP_DIRECTORIES):
        if path.suffix.lower() not in {".md", ".rst"}:
            continue
        lowered = path.name.lower()
        if not any(marker in lowered for marker in _ARCHITECTURE_NAME_MARKERS):
            continue
        yield path
        count += 1
        if count >= _MAX_DOCUMENTS:
            return
