"""Instruction discovery and scoped resolution tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.instructions import discover_instructions
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    from pathlib import Path


def _workspace(tmp_path: Path) -> TargetWorkspace:
    root = make_project(tmp_path / "repo")
    write(root / "AGENTS.md", "root rules\n")
    write(root / "backend" / "AGENTS.md", "backend rules\n")
    write(root / "frontend" / "AGENTS.md", "frontend rules\n")
    write(root / "README.md", "readme\n")
    write(root / "ARCHITECTURE.md", "architecture\n")
    write(root / "docs" / "testing.md", "testing guide\n")
    return TargetWorkspace.open(root)


def test_discovery_finds_agents_readme_and_architecture_docs(tmp_path: Path) -> None:
    """All supported instruction kinds are discovered with repository-relative paths."""
    workspace = _workspace(tmp_path)
    instructions = discover_instructions(workspace)
    paths = {document.relative_path for document in instructions.documents}
    assert paths == {
        "AGENTS.md",
        "README.md",
        "ARCHITECTURE.md",
        "backend/AGENTS.md",
        "docs/testing.md",
        "frontend/AGENTS.md",
    }
    kinds = {document.relative_path: document.kind for document in instructions.documents}
    assert kinds["AGENTS.md"] == "agents"
    assert kinds["README.md"] == "readme"
    assert kinds["ARCHITECTURE.md"] == "architecture"


def test_for_paths_keeps_root_and_ancestor_agents_only(tmp_path: Path) -> None:
    """Path-scoped instructions include root and the matching ancestor chain."""
    workspace = _workspace(tmp_path)
    instructions = discover_instructions(workspace)
    selected = instructions.for_paths(("backend/apps/sample/models.py",))
    paths = [document.relative_path for document in selected]
    assert "AGENTS.md" in paths
    assert "backend/AGENTS.md" in paths
    assert "frontend/AGENTS.md" not in paths


def test_for_paths_without_paths_returns_every_document(tmp_path: Path) -> None:
    """Initial context uses every discovered document."""
    workspace = _workspace(tmp_path)
    instructions = discover_instructions(workspace)
    assert len(instructions.for_paths(())) == len(instructions.documents)


def test_render_includes_content_and_scope(tmp_path: Path) -> None:
    """Rendered instructions preserve content and identify their scope."""
    workspace = _workspace(tmp_path)
    rendered = discover_instructions(workspace).render(("backend/apps/sample/models.py",))
    assert "root rules" in rendered
    assert "backend rules" in rendered
    assert "frontend rules" not in rendered
    assert "scope: backend" in rendered


def test_empty_instruction_files_are_ignored(tmp_path: Path) -> None:
    """Blank instruction files do not produce prompt noise."""
    workspace = _workspace(tmp_path)
    write(workspace.root / "empty" / "AGENTS.md", "   \n")
    paths = {document.relative_path for document in discover_instructions(workspace).documents}
    assert "empty/AGENTS.md" not in paths


def test_instructions_do_not_read_external_symlinks(tmp_path: Path) -> None:
    """Instruction discovery never reads AGENTS.md outside the workspace."""
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "AGENTS.md").write_text("outside rules\n", encoding="utf-8")
    (workspace.root / "linked").symlink_to(outside)
    paths = {document.relative_path for document in discover_instructions(workspace).documents}
    assert all("linked" not in path for path in paths)
    assert "outside rules" not in discover_instructions(workspace).render()
