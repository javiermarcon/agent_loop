"""Index/worktree attribution tests for staged transitions after a baseline."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.git import capture_git_baseline
from agent_loop.autonomy.discovery.git import discover_git
from agent_loop.autonomy.discovery.git_attribution import ChangeAttribution
from agent_loop.autonomy.discovery.git_attribution import audit_git_changes
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import git_executable

if TYPE_CHECKING:
    from pathlib import Path


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.run((git_executable(), *args), cwd=root, check=True)  # noqa: S603


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init")


def _audit(root: Path):
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    return workspace, baseline


def _attribution(root: Path, baseline, workspace) -> dict[str, ChangeAttribution]:
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    return {change.path: change.attribution for change in audit.changes}


def test_unstaged_change_staged_after_baseline_is_execution(tmp_path: Path) -> None:
    """A git add transition after the baseline belongs to the observation window."""
    root = _repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _commit(root)
    _write(root, "a.py", "a = 2\n")
    workspace, baseline = _audit(root)
    _git(root, "add", "a.py")
    assert _attribution(root, baseline, workspace)["a.py"] is ChangeAttribution.EXECUTION


def test_preexisting_staged_change_remains_preexisting(tmp_path: Path) -> None:
    """An already staged change untouched by the window stays preexisting."""
    root = _repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _commit(root)
    _write(root, "a.py", "a = 2\n")
    _git(root, "add", "a.py")
    workspace, baseline = _audit(root)
    assert _attribution(root, baseline, workspace)["a.py"] is ChangeAttribution.PREEXISTING


def test_staged_change_modified_in_worktree_is_execution(tmp_path: Path) -> None:
    """A second edit of a staged file is attributable to the execution window."""
    root = _repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _commit(root)
    _write(root, "a.py", "a = 2\n")
    _git(root, "add", "a.py")
    workspace, baseline = _audit(root)
    _write(root, "a.py", "a = 3\n")
    assert _attribution(root, baseline, workspace)["a.py"] is ChangeAttribution.EXECUTION


def test_unstaging_an_already_staged_change_is_execution(tmp_path: Path) -> None:
    """A restore --staged transition is observable even when bytes do not change."""
    root = _repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _commit(root)
    _write(root, "a.py", "a = 2\n")
    _git(root, "add", "a.py")
    workspace, baseline = _audit(root)
    _git(root, "restore", "--staged", "a.py")
    assert _attribution(root, baseline, workspace)["a.py"] is ChangeAttribution.EXECUTION


def test_staging_a_new_file_after_baseline_is_execution(tmp_path: Path) -> None:
    """A new file added and then staged is still an execution change."""
    root = _repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _commit(root)
    workspace, baseline = _audit(root)
    _write(root, "new.py", "new = 1\n")
    _git(root, "add", "new.py")
    assert _attribution(root, baseline, workspace)["new.py"] is ChangeAttribution.EXECUTION


def test_staged_deletion_then_unstaged_remains_attributed(tmp_path: Path) -> None:
    """Unstaging a deletion is an index transition after the baseline."""
    root = _repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _commit(root)
    _git(root, "rm", "-q", "a.py")
    workspace, baseline = _audit(root)
    _git(root, "restore", "--staged", "a.py")
    assert _attribution(root, baseline, workspace)["a.py"] is ChangeAttribution.EXECUTION


def test_preexisting_and_new_overlap_is_execution(tmp_path: Path) -> None:
    """A new edit on top of a preexisting change is execution evidence."""
    root = _repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _commit(root)
    _write(root, "a.py", "a = 2\n")
    workspace, baseline = _audit(root)
    _write(root, "a.py", "a = 3\n")
    _git(root, "add", "a.py")
    assert _attribution(root, baseline, workspace)["a.py"] is ChangeAttribution.EXECUTION


def test_rename_after_baseline_remains_execution(tmp_path: Path) -> None:
    """Rename detection is preserved with index-aware attribution."""
    root = _repo(tmp_path)
    _write(root, "old.py", "value = 1\n")
    _commit(root)
    workspace, baseline = _audit(root)
    _git(root, "mv", "old.py", "new.py")
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    change = next(change for change in audit.changes if change.path == "new.py")
    assert change.attribution is ChangeAttribution.EXECUTION
    assert change.previous_path == "old.py"
