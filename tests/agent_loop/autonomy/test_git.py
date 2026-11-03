"""Git discovery, baseline and change-attribution tests."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.git import capture_git_baseline
from agent_loop.autonomy.discovery.git import discover_git
from agent_loop.autonomy.discovery.git_attribution import ChangeAttribution
from agent_loop.autonomy.discovery.git_attribution import audit_git_changes
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import git_executable

if TYPE_CHECKING:
    from pathlib import Path


def _git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run_git(root, "init", "-q")
    return root


def _run_git(root: Path, *args: str) -> None:
    subprocess.run((git_executable(), *args), cwd=root, check=True)  # noqa: S603


def _git_output(root: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        (git_executable(), *args),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _head(root: Path) -> str:
    return _git_output(root, "rev-parse", "HEAD")


def _commit(root: Path, message: str = "init") -> None:
    _run_git(root, "add", "-A")
    _run_git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", message)


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_discover_git_reports_repository_and_untracked_files(tmp_path: Path) -> None:
    """A Git work tree is identified with its dirty files."""
    root = _git_repo(tmp_path)
    _write(root, "README.md", "hello\n")
    state = discover_git(TargetWorkspace.open(root))
    assert state.is_repository is True
    assert state.dirty is True
    assert "README.md" in state.changed_files
    assert "README.md" in state.untracked_files
    assert state.error is None


def test_discover_git_reports_clean_repository(tmp_path: Path) -> None:
    """A committed, clean work tree has no changed files."""
    root = _git_repo(tmp_path)
    _write(root, "README.md", "hello\n")
    _commit(root)
    state = discover_git(TargetWorkspace.open(root))
    assert state.is_repository is True
    assert state.dirty is False
    assert state.changed_files == ()
    assert state.head is not None


def test_discover_git_separates_index_worktree_and_untracked(tmp_path: Path) -> None:
    """Staged, unstaged and untracked changes are reported independently."""
    root = _git_repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _write(root, "b.py", "b = 1\n")
    _commit(root)
    _write(root, "a.py", "a = 2\n")
    _run_git(root, "add", "a.py")
    _write(root, "b.py", "b = 2\n")
    _write(root, "new.py", "new = 1\n")
    state = discover_git(TargetWorkspace.open(root))
    assert state.staged_files == ("a.py",)
    assert state.unstaged_files == ("b.py",)
    assert state.untracked_files == ("new.py",)
    assert set(state.changed_files) == {"a.py", "b.py", "new.py"}


def test_baseline_preserves_preexisting_staged_and_untracked_changes(tmp_path: Path) -> None:
    """Preexisting changes are not attributed to the agent."""
    root = _git_repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _commit(root)
    _write(root, "a.py", "a = 2\n")
    _run_git(root, "add", "a.py")
    _write(root, "pre.py", "pre = 1\n")
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    assert {change.path for change in audit.preexisting_changes} == {"a.py", "pre.py"}
    assert audit.execution_changes == ()


def test_audit_attributes_new_modifications_creations_and_deletions(tmp_path: Path) -> None:
    """Changes after the baseline are attributed to the execution window."""
    root = _git_repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _write(root, "b.py", "b = 1\n")
    _commit(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _write(root, "a.py", "a = 2\n")
    _write(root, "new.py", "new = 1\n")
    (root / "b.py").unlink()
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    execution = {change.path: change for change in audit.execution_changes}
    assert set(execution) == {"a.py", "b.py", "new.py"}
    assert execution["a.py"].status == "modified"
    assert execution["b.py"].status == "deleted"
    assert execution["new.py"].status == "untracked"


def test_audit_detects_rename_after_baseline(tmp_path: Path) -> None:
    """A rename performed during execution is attributed to execution."""
    root = _git_repo(tmp_path)
    _write(root, "old.py", "value = 1\n")
    _commit(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _run_git(root, "mv", "old.py", "new.py")
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    renamed = [change for change in audit.execution_changes if change.status == "renamed"]
    assert len(renamed) == 1
    assert renamed[0].path == "new.py"
    assert renamed[0].previous_path == "old.py"


def test_audit_detects_simultaneous_edit_of_preexisting_change(tmp_path: Path) -> None:
    """A second edit of an already-modified file is attributed to execution."""
    root = _git_repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _commit(root)
    _write(root, "a.py", "a = 2\n")
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _write(root, "a.py", "a = 3\n")
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    change = next(change for change in audit.changes if change.path == "a.py")
    assert change.attribution is ChangeAttribution.EXECUTION
    assert change.baseline_hash is not None
    assert change.current_hash is not None
    assert change.baseline_hash != change.current_hash


def test_audit_marks_cleared_baseline_change_undetermined(tmp_path: Path) -> None:
    """A baseline change that disappears cannot be attributed with certainty."""
    root = _git_repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _commit(root)
    _write(root, "a.py", "a = 2\n")
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _write(root, "a.py", "a = 1\n")
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    change = next(change for change in audit.changes if change.path == "a.py")
    assert change.status == "cleared"
    assert change.attribution is ChangeAttribution.UNDETERMINED


def test_audit_reports_changes_outside_the_authorized_scope(tmp_path: Path) -> None:
    """Execution changes outside the configured scope are surfaced explicitly."""
    root = _git_repo(tmp_path)
    _write(root, "README.md", "base\n")
    _commit(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _write(root, "apps/allowed/x.py", "x = 1\n")
    _write(root, "secrets/outside.py", "secret = 1\n")
    audit = audit_git_changes(
        workspace,
        baseline,
        discover_git(workspace),
        scope_paths=("apps/allowed",),
    )
    assert audit.unexpected_paths == ("secrets/outside.py",)


def test_transient_create_and_delete_is_not_observable_post_hoc(tmp_path: Path) -> None:
    """A file created and deleted between snapshots leaves no observable change."""
    root = _git_repo(tmp_path)
    _write(root, "README.md", "base\n")
    _commit(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _write(root, "transient.py", "transient = 1\n")
    (root / "transient.py").unlink()
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    assert audit.changes == ()
    assert audit.execution_changes == ()


def test_audit_preserves_preexisting_deletion_as_preexisting(tmp_path: Path) -> None:
    """A deletion that predates the baseline is not attributed to execution."""
    root = _git_repo(tmp_path)
    _write(root, "a.py", "a = 1\n")
    _write(root, "b.py", "b = 1\n")
    _commit(root)
    (root / "a.py").unlink()
    _write(root, "b.py", "b = 2\n")
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    attribution = {change.path: change.attribution for change in audit.changes}
    assert attribution["a.py"] is ChangeAttribution.PREEXISTING
    assert attribution["b.py"] is ChangeAttribution.PREEXISTING
    assert audit.execution_changes == ()
    assert audit.undetermined_changes == ()


def test_audit_distinguishes_preexisting_and_new_deletions(tmp_path: Path) -> None:
    """Only the deletion performed after the baseline belongs to execution."""
    root = _git_repo(tmp_path)
    _write(root, "old.py", "old = 1\n")
    _write(root, "new.py", "new = 1\n")
    _commit(root)
    (root / "old.py").unlink()
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    (root / "new.py").unlink()
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    attribution = {change.path: change.attribution for change in audit.changes}
    assert attribution["old.py"] is ChangeAttribution.PREEXISTING
    assert attribution["new.py"] is ChangeAttribution.EXECUTION


def test_audit_detects_head_change_and_commit_paths(tmp_path: Path) -> None:
    """A clean working tree cannot hide a commit made during the window."""
    root = _git_repo(tmp_path)
    _write(root, "README.md", "base\n")
    _commit(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _write(root, "agent_output.py", "agent = 1\n")
    _run_git(root, "add", "agent_output.py")
    _run_git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "agent commit",
    )
    final_state = discover_git(workspace)
    assert final_state.changed_files == ()
    audit = audit_git_changes(workspace, baseline, final_state)
    assert audit.head_changed is True
    assert audit.commit_paths == ("agent_output.py",)
    assert audit.execution_changes == ()
    change = next(change for change in audit.changes if change.path == "agent_output.py")
    assert change.attribution is ChangeAttribution.UNDETERMINED
    assert audit.warnings


def test_audit_detects_branch_change_without_head_change(tmp_path: Path) -> None:
    """Switching branches is visible even when the commit hash is unchanged."""
    root = _git_repo(tmp_path)
    _write(root, "README.md", "base\n")
    _commit(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _run_git(root, "checkout", "-q", "-b", "other")
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    assert audit.branch_changed is True
    assert audit.head_changed is False
    assert any("branch" in warning for warning in audit.warnings)


def test_audit_detects_head_that_returned_to_its_original_value(tmp_path: Path) -> None:
    """Reflog evidence exposes a transient commit even after a hard reset."""
    root = _git_repo(tmp_path)
    _write(root, "README.md", "base\n")
    _commit(root)
    original = _head(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _write(root, "transient.py", "transient = 1\n")
    _commit(root, "transient")
    _run_git(root, "reset", "--hard", original)
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    assert _head(root) == original
    assert audit.head_changed is True
    assert "transient.py" in audit.commit_paths
    change = next(change for change in audit.changes if change.path == "transient.py")
    assert change.attribution is ChangeAttribution.UNDETERMINED
    assert any("reflog" in warning for warning in audit.warnings)


def test_audit_commit_rename_and_deletion_are_reported(tmp_path: Path) -> None:
    """Commit-only renames and deletions remain visible in the audit."""
    root = _git_repo(tmp_path)
    _write(root, "old name.py", "value = 1\n")
    _write(root, "gone.py", "value = 2\n")
    _commit(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _run_git(root, "mv", "old name.py", "new café.py")
    (root / "gone.py").unlink()
    _run_git(root, "add", "-A")
    _run_git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "moves")
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    changes = {change.path: change for change in audit.changes}
    assert changes["new café.py"].status == "renamed"
    assert changes["new café.py"].previous_path == "old name.py"
    assert changes["gone.py"].status == "deleted"
    assert all(change.attribution is ChangeAttribution.UNDETERMINED for change in changes.values())


def test_audit_reports_commit_range_inspection_failure(tmp_path: Path) -> None:
    """A failed commit inspection is surfaced instead of producing a clean result."""
    root = _git_repo(tmp_path)
    _write(root, "README.md", "base\n")
    _commit(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    broken = replace(baseline, state=replace(baseline.state, head="0" * 40))
    audit = audit_git_changes(workspace, broken, discover_git(workspace))
    assert audit.head_changed is True
    assert audit.commit_paths == ()
    assert any("inspection failed" in warning for warning in audit.warnings)


def test_discover_git_decodes_special_paths_and_renames(tmp_path: Path) -> None:
    """Porcelain NUL records preserve spaces, non-ASCII names and rename pairs."""
    root = _git_repo(tmp_path)
    _write(root, "docs with space/keep.py", "keep = 1\n")
    _write(root, "old name.py", "old = 1\n")
    _write(root, "borrar ñ.py", "delete = 1\n")
    _write(root, "café tracked.py", "cafe = 1\n")
    _write(root, 'quote " and \\ slash.py', "quoted = 1\n")
    _commit(root)
    _write(root, "docs with space/keep.py", "keep = 2\n")
    _write(root, "café tracked.py", "cafe = 2\n")
    _write(root, 'quote " and \\ slash.py', "quoted = 2\n")
    _write(root, "docs with space/modified file.py", "modified = 1\n")
    _write(root, "café.py", "cafe = 1\n")
    _run_git(root, "mv", "old name.py", "new café.py")
    (root / "borrar ñ.py").unlink()
    state = discover_git(TargetWorkspace.open(root))
    assert "docs with space/keep.py" in state.unstaged_files
    assert "café tracked.py" in state.unstaged_files
    assert "docs with space/modified file.py" in state.untracked_files
    assert "café.py" in state.untracked_files
    assert ("old name.py", "new café.py") in state.renamed_files
    assert "borrar ñ.py" in state.deleted_files
    assert "new café.py" in state.changed_files
    assert "old name.py" not in state.changed_files
    assert 'quote " and \\ slash.py' in state.changed_files


def test_audit_attributes_special_paths_after_baseline(tmp_path: Path) -> None:
    """Special-character paths reach attribution without quoting artefacts."""
    root = _git_repo(tmp_path)
    _write(root, "docs with space/base.py", "base = 1\n")
    _write(root, "rename me.py", "rename = 1\n")
    _commit(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _write(root, "docs with space/new file.py", "new = 1\n")
    _write(root, "café.py", "cafe = 1\n")
    _run_git(root, "mv", "rename me.py", "renombrado ñ.py")
    audit = audit_git_changes(workspace, baseline, discover_git(workspace))
    execution = {change.path: change for change in audit.execution_changes}
    assert "docs with space/new file.py" in execution
    assert "café.py" in execution
    assert execution["renombrado ñ.py"].status == "renamed"
    assert execution["renombrado ñ.py"].previous_path == "rename me.py"


def test_audit_scope_uses_component_boundaries(tmp_path: Path) -> None:
    """A sibling directory sharing a textual prefix is outside the scope."""
    root = _git_repo(tmp_path)
    _write(root, "README.md", "base\n")
    _commit(root)
    workspace = TargetWorkspace.open(root)
    baseline = capture_git_baseline(workspace)
    _write(root, "apps/allowed/x.py", "x = 1\n")
    _write(root, "apps/allowed-other/y.py", "y = 1\n")
    audit = audit_git_changes(workspace, baseline, discover_git(workspace), scope_paths=("apps/allowed",))
    assert audit.unexpected_paths == ("apps/allowed-other/y.py",)
