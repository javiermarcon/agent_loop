"""Fail-closed security, Git inspection and attribution edge contracts."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from agent_loop.autonomy.discovery.git import ChangeAttribution
from agent_loop.autonomy.discovery.git import GitAudit
from agent_loop.autonomy.discovery.git import GitBaseline
from agent_loop.autonomy.discovery.git import GitChange
from agent_loop.autonomy.discovery.git import GitState
from agent_loop.autonomy.discovery.git import _clean
from agent_loop.autonomy.discovery.git import _hash_file
from agent_loop.autonomy.discovery.git import _index_blobs
from agent_loop.autonomy.discovery.git import _parse_status
from agent_loop.autonomy.discovery.git import _reflog_heads
from agent_loop.autonomy.discovery.git import _status_records
from agent_loop.autonomy.discovery.git import capture_git_baseline
from agent_loop.autonomy.discovery.git import discover_git
from agent_loop.autonomy.discovery.git_attribution import _attribute_change
from agent_loop.autonomy.discovery.git_attribution import _attribution_for_existing_path
from agent_loop.autonomy.discovery.git_attribution import _attribution_for_new_path
from agent_loop.autonomy.discovery.git_attribution import _attribution_for_rename
from agent_loop.autonomy.discovery.git_attribution import _commit_diff
from agent_loop.autonomy.discovery.git_attribution import _history_evidence
from agent_loop.autonomy.discovery.git_attribution import _in_scope
from agent_loop.autonomy.discovery.git_attribution import _integrate_commit_changes
from agent_loop.autonomy.discovery.git_attribution import _parse_name_status
from agent_loop.autonomy.discovery.git_attribution import _reflog_change_evidence
from agent_loop.autonomy.discovery.git_attribution import merge_window_attribution
from agent_loop.autonomy.discovery.instructions import _append_document
from agent_loop.autonomy.discovery.instructions import _iter_architecture_docs
from agent_loop.autonomy.discovery.instructions import _iter_named
from agent_loop.autonomy.discovery.instructions import _read_text
from agent_loop.autonomy.discovery.instructions import _root_named
from agent_loop.autonomy.discovery.instructions import discover_instructions
from agent_loop.autonomy.errors import SecurityError
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.security import _executable_extensions
from agent_loop.autonomy.security import _is_executable_file
from agent_loop.autonomy.security import _is_git_work_tree
from agent_loop.autonomy.security import _is_venv_interpreter
from agent_loop.autonomy.security import find_system_executable
from agent_loop.autonomy.security import resolve_system_executable
from agent_loop.autonomy.security import resolve_workspace_executable
from agent_loop.autonomy.security import run_git_command
from agent_loop.autonomy.security import sanitize_environment
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write


def _workspace(tmp_path: Path) -> TargetWorkspace:
    return TargetWorkspace.open(make_project(tmp_path / "repo"))


def test_workspace_open_rejects_files_nonexistent_and_escapes(tmp_path: Path) -> None:
    """Only existing directories inside a Git work tree are authorized."""
    plain = tmp_path / "file.txt"
    plain.write_text("x", encoding="utf-8")
    with pytest.raises(SecurityError, match="does not exist"):
        TargetWorkspace.open(tmp_path / "missing")
    with pytest.raises(SecurityError, match="not a directory"):
        TargetWorkspace.open(plain)
    workspace = _workspace(tmp_path)
    with pytest.raises(SecurityError, match="cannot be resolved"):
        workspace.resolve("\0")
    with pytest.raises(SecurityError, match="escapes"):
        workspace.resolve("../outside")
    with pytest.raises(SecurityError, match="escapes"):
        workspace.member_path("../outside")
    assert workspace.relative("manage.py") == "manage.py"
    assert workspace.is_within("manage.py") is True
    assert workspace.is_within("../outside") is False


def test_iter_files_skips_unreadable_and_escaping_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Unreadable roots and broken/escaping symlinks are skipped."""
    workspace = _workspace(tmp_path)
    (workspace.root / "broken").symlink_to(tmp_path / "missing")
    files = list(workspace.iter_files(skip_directories=frozenset()))
    assert all("broken" not in path.name for path in files)
    original = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda self: (_ for _ in ()).throw(OSError("denied")))
    try:
        assert list(workspace.iter_files()) == []
    finally:
        monkeypatch.setattr(Path, "iterdir", original)


def test_workspace_executable_authorization_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Workspace executables reject bare/escaping/broken candidates."""
    workspace = _workspace(tmp_path)
    executable = workspace.root / "tool"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    assert resolve_workspace_executable(workspace, "tool") == executable
    assert resolve_workspace_executable(workspace, "../tool") is None
    assert resolve_workspace_executable(workspace, "missing") is None
    assert resolve_workspace_executable(workspace, "manage.py") is None
    assert resolve_system_executable("relative") is None
    assert resolve_system_executable(executable) == executable
    broken = workspace.root / "broken-tool"
    broken.symlink_to(tmp_path / "missing")
    assert resolve_workspace_executable(workspace, "broken-tool") is None


def test_venv_interpreter_and_executable_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The venv exception requires bin/ layout and an internal pyvenv.cfg."""
    workspace = _workspace(tmp_path)
    link = workspace.root / "bin" / "python"
    link.parent.mkdir()
    link.symlink_to(Path(sys.executable))
    resolved = Path(sys.executable).resolve()
    assert _is_venv_interpreter(workspace, link, resolved) is False
    wrong_parent = workspace.root / "lib" / "python"
    wrong_parent.parent.mkdir()
    wrong_parent.symlink_to(resolved)
    assert _is_venv_interpreter(workspace, wrong_parent, resolved) is False
    (workspace.root / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    assert _is_venv_interpreter(workspace, link, resolved) is True
    monkeypatch.setattr(Path, "is_file", lambda self: (_ for _ in ()).throw(OSError("x")))
    assert _is_executable_file(workspace.root / "x") is False


def test_executable_extensions_windows_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows PATHEXT is normalized and empty values still yield one probe."""
    monkeypatch.setattr("agent_loop.autonomy.security.os.name", "nt")
    monkeypatch.setenv("PATHEXT", ".EXE;.BAT")
    assert _executable_extensions() == (".exe", ".bat")
    monkeypatch.setenv("PATHEXT", "")
    assert _executable_extensions() == ("",)


def test_find_system_executable_skips_relative_and_workspace_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PATH lookup ignores relative and workspace-controlled directories."""
    workspace = _workspace(tmp_path)
    local = workspace.root / "git"
    local.write_text("#!/bin/sh\n", encoding="utf-8")
    local.chmod(0o755)
    monkeypatch.setenv("PATH", f"relative{os.pathsep}{workspace.root}")
    assert find_system_executable("git", workspace=workspace) is None
    monkeypatch.setenv("PATH", "/definitely/missing")
    assert find_system_executable("git") is None


def test_run_git_command_rejects_workspace_binary_and_subprocess_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A workspace-controlled Git binary and OS failures are not executed."""
    workspace = _workspace(tmp_path)
    local = workspace.root / "git"
    local.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("agent_loop.autonomy.security.find_system_executable", lambda name: local)
    assert run_git_command(workspace.root, "status") is None
    monkeypatch.setattr(
        "agent_loop.autonomy.security.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    monkeypatch.setattr("agent_loop.autonomy.security.find_system_executable", lambda name: Path("/bin/git"))
    assert run_git_command(workspace.root, "status") is None


def test_is_git_work_tree_short_and_missing_toplevel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Malformed rev-parse output and an unresolved toplevel fail closed."""
    monkeypatch.setattr(
        "agent_loop.autonomy.security.run_git_command",
        lambda root, *args: SimpleNamespace(returncode=0, stdout="true\n"),
    )
    assert _is_git_work_tree(tmp_path) is False
    monkeypatch.setattr(
        "agent_loop.autonomy.security.run_git_command",
        lambda root, *args: SimpleNamespace(returncode=0, stdout="true\n/missing/toplevel\n"),
    )
    assert _is_git_work_tree(tmp_path) is False


def test_sanitize_environment_rejects_forbidden_names() -> None:
    """Target execution never inherits AgentLoop, Shipii or Git redirectors."""
    assert sanitize_environment({"PYTHONPATH": "x", "PATH": "/bin"}, path_prepend="/tools")["PATH"].startswith(
        "/tools",
    )
    for name in ("PYTHONPATH", "AGENT_LOOP_RUN_ID", "SHIPII_X", "GIT_DIR", "", "A=B"):
        with pytest.raises(SecurityError):
            sanitize_environment({}, extra={name: "value"})


def test_git_discovery_error_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Git failures preserve explicit error evidence instead of empty success."""
    workspace = _workspace(tmp_path)
    monkeypatch.setattr("agent_loop.autonomy.discovery.git._git", lambda root, *args: None)
    assert discover_git(workspace).error == "git rev-parse could not be executed"
    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.git._git",
        lambda root, *args: SimpleNamespace(returncode=1, stdout="", stderr="bad"),
    )
    assert discover_git(workspace).error == "target is not a Git work tree"

    def _git(root: Path, *args: str) -> object:
        if args[0] == "rev-parse":
            return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
        if args[:2] == ("status", "--porcelain=v1"):
            return SimpleNamespace(returncode=1, stdout="", stderr="status bad")
        return SimpleNamespace(returncode=0, stdout=f"{args[-1]}\n", stderr="")

    monkeypatch.setattr("agent_loop.autonomy.discovery.git._git", _git)
    assert "git status failed" in str(discover_git(workspace).error)

    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.git._git",
        lambda root, *args: (
            SimpleNamespace(returncode=0, stdout="true\n", stderr="")
            if args[0] == "rev-parse"
            else SimpleNamespace(returncode=0, stdout="bad", stderr="")
        ),
    )
    assert "malformed" in str(discover_git(workspace).error)


def test_status_parser_rejects_malformed_records() -> None:
    """Status parsing fails closed for every malformed record shape."""
    for output, message in (
        ("M\n", "malformed"),
        ("X  path\0", "unknown status code"),
        ("?  path\0", "inconsistent untracked"),
        ("R  new\0", "missing its source path"),
    ):
        parsed, error = _parse_status(output)
        assert parsed is None
        assert message in str(error)
    parsed, error = _parse_status("M  a\0")
    assert error is None
    assert parsed is not None
    assert parsed.staged == ("a",)
    assert _status_records("") == ((), None)


def test_status_parser_untracked_and_renamed() -> None:
    """Untracked and rename records are represented without duplication."""
    parsed, error = _parse_status("?? new\0R  new\0old\0")
    assert error is None
    assert parsed is not None
    assert parsed.untracked == ("new",)
    assert parsed.renamed == (("old", "new"),)


def test_hash_file_symlink_missing_directory_and_escape(tmp_path: Path) -> None:
    """File hashing distinguishes links, missing entries and escaping targets."""
    workspace = _workspace(tmp_path)
    write(workspace.root / "plain.txt", "content\n")
    plain_digest = _hash_file(workspace, "plain.txt")
    assert plain_digest is not None
    (workspace.root / "link").symlink_to("plain.txt")
    link_digest = _hash_file(workspace, "link")
    assert link_digest is not None
    assert link_digest.startswith("symlink:")
    assert _hash_file(workspace, "missing") is None
    assert _hash_file(workspace, ".") is None
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    (workspace.root / "escape").symlink_to(outside)
    escape_digest = _hash_file(workspace, "escape")
    assert escape_digest is not None
    assert escape_digest.startswith("symlink:")


def test_index_blobs_and_reflog_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Index/reflog evidence returns empty on missing or malformed Git output."""
    workspace = _workspace(tmp_path)
    assert _index_blobs(workspace, ()) == {}
    monkeypatch.setattr("agent_loop.autonomy.discovery.git._git", lambda root, *args: None)
    assert _index_blobs(workspace, ("a",)) == {}
    assert _reflog_heads(workspace.root) == ()
    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.git._git",
        lambda root, *args: SimpleNamespace(
            returncode=0,
            stdout="100644 hash 0\ta\0bad\0100644 other 0\tq\0",
            stderr="",
        ),
    )
    assert _index_blobs(workspace, ("a",)) == {"a": "hash"}


def test_capture_baseline_and_clean(tmp_path: Path) -> None:
    """A clean repository has no changes and empty strings normalize to None."""
    workspace = _workspace(tmp_path)
    baseline = capture_git_baseline(workspace)
    assert baseline.state.is_repository is True
    assert baseline.hash_for("manage.py") is not None
    assert baseline.hash_for("missing") is None
    assert baseline.index_hash_for("missing") is None
    assert baseline.to_dict()["indexed_paths"] == 0
    assert _clean("  ") is None
    assert _clean("main\n") == "main"


def _baseline(state: GitState, *, hashes: tuple[tuple[str, str], ...] = ()) -> GitBaseline:
    return GitBaseline(state=state, file_hashes=hashes)


def test_attribution_for_new_deleted_renamed_and_existing_paths() -> None:
    """Every attribution branch is explicit and deterministic."""
    assert _attribution_for_new_path("x", None, GitState(True)) is ChangeAttribution.UNDETERMINED  # noqa: FBT003
    assert _attribution_for_new_path("x", None, GitState(True, deleted_files=("x",))) is ChangeAttribution.EXECUTION  # noqa: FBT003
    assert _attribution_for_new_path("x", "hash", GitState(True)) is ChangeAttribution.EXECUTION  # noqa: FBT003
    baseline = _baseline(GitState(True, renamed_files=(("old", "new"),)), hashes=(("new", "hash"),))  # noqa: FBT003
    assert _attribution_for_rename("new", "old", baseline, "hash") is ChangeAttribution.PREEXISTING
    assert _attribution_for_rename("new", "old", baseline, "other") is ChangeAttribution.EXECUTION

    baseline_state = GitState(True, changed_files=("a", "b", "c"), deleted_files=("b",))  # noqa: FBT003
    baseline = _baseline(baseline_state, hashes=(("a", "h"),))
    assert (
        _attribution_for_existing_path("a", baseline, "h", GitState(True), index_changed=True)  # noqa: FBT003
        is ChangeAttribution.EXECUTION
    )
    assert (
        _attribution_for_existing_path("a", baseline, "h", GitState(True), index_changed=False)  # noqa: FBT003
        is ChangeAttribution.PREEXISTING
    )
    assert (
        _attribution_for_existing_path("a", baseline, "x", GitState(True), index_changed=False)  # noqa: FBT003
        is ChangeAttribution.EXECUTION
    )
    assert (
        _attribution_for_existing_path("b", baseline, None, GitState(True, deleted_files=("b",)), index_changed=False)  # noqa: FBT003
        is ChangeAttribution.PREEXISTING
    )
    assert (
        _attribution_for_existing_path("b", baseline, None, GitState(True), index_changed=False)  # noqa: FBT003
        is ChangeAttribution.EXECUTION
    )
    assert (
        _attribution_for_existing_path("c", baseline, None, GitState(True, deleted_files=("c",)), index_changed=False)  # noqa: FBT003
        is ChangeAttribution.UNDETERMINED
    )
    context = SimpleNamespace(
        baseline=baseline,
        baseline_index={},
        final_index={},
        final_hashes={},
        final_state=GitState(True),  # noqa: FBT003
    )
    assert _attribute_change("c", None, cast(Any, context)) is ChangeAttribution.UNDETERMINED  # noqa: TC006


def test_history_evidence_branch_head_and_reflog_warnings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Branch changes and commit/reflog failures become explicit warnings."""
    workspace = _workspace(tmp_path)
    baseline = _baseline(GitState(True, branch="main", head="a"))  # noqa: FBT003
    evidence = _history_evidence(workspace, baseline, GitState(True, branch="dev", head="a"))  # noqa: FBT003
    assert evidence.branch_changed is True
    assert any("branch changed" in warning for warning in evidence.warnings)

    evidence = _history_evidence(workspace, baseline, GitState(True, branch="main", head=None))  # noqa: FBT003
    assert evidence.head_changed is True
    assert any("final HEAD could not be resolved" in warning for warning in evidence.warnings)

    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.git_attribution._commit_diff",
        lambda *args: ((), "commit diff failed"),
    )
    evidence = _history_evidence(workspace, baseline, GitState(True, branch="main", head="b"))  # noqa: FBT003
    assert any("commit diff failed" in warning for warning in evidence.warnings)

    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.git_attribution._reflog_heads",
        lambda root: ("a",),
    )
    baseline_with_reflog = GitBaseline(state=baseline.state, file_hashes=(), reflog_heads=("a",))
    changed, changes, warnings = _reflog_change_evidence(workspace, baseline_with_reflog, "a")
    assert changed is False
    assert changes == []
    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.git_attribution._reflog_heads",
        lambda root: ("a", "b"),
    )
    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.git_attribution._commit_diff",
        lambda *args: ((), "transient"),
    )
    changed, changes, warnings = _reflog_change_evidence(workspace, baseline, "a")
    assert changed is True
    assert any("transient" in warning for warning in warnings)


def test_commit_diff_and_name_status_parsing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Commit diffs parse renames/copies and fail closed when truncated."""
    workspace = _workspace(tmp_path)
    monkeypatch.setattr("agent_loop.autonomy.discovery.git_attribution._git", lambda *args: None)
    assert _commit_diff(workspace, "a", "b")[1] == "Git commit range inspection could not be executed"
    monkeypatch.setattr(
        "agent_loop.autonomy.discovery.git_attribution._git",
        lambda *args: SimpleNamespace(returncode=1, stderr="bad", stdout=""),
    )
    assert "failed" in str(_commit_diff(workspace, "a", "b")[1])
    parsed = _parse_name_status("R100\0old\0new\0M\0file\0A\0added\0")
    assert (parsed[0].path, parsed[0].status, parsed[0].previous_path) == ("new", "renamed", "old")
    assert (parsed[1].path, parsed[1].status) == ("file", "modified")
    assert (parsed[2].path, parsed[2].status) == ("added", "added")
    assert _parse_name_status("M") == ()
    assert _parse_name_status("C100\0old\0copy\0")[0].status == "copied"


def test_integrate_commit_changes_and_scope() -> None:
    """Commit evidence downgrades execution changes to undetermined."""
    changes = {
        "a": GitChange("a", "modified", ChangeAttribution.EXECUTION),
        "b": GitChange("b", "cleared", ChangeAttribution.UNDETERMINED),
        "c": GitChange("c", "modified", ChangeAttribution.PREEXISTING),
    }
    commits = (
        SimpleNamespace(path="a", status="modified", previous_path=None),
        SimpleNamespace(path="b", status="modified", previous_path="old"),
        SimpleNamespace(path="c", status="modified", previous_path=None),
        SimpleNamespace(path="d", status="added", previous_path=None),
    )
    _integrate_commit_changes(changes, commits)  # type: ignore[arg-type]
    assert changes["a"].attribution is ChangeAttribution.UNDETERMINED
    assert changes["b"].attribution is ChangeAttribution.UNDETERMINED
    assert changes["c"].attribution is ChangeAttribution.PREEXISTING
    assert changes["d"].attribution is ChangeAttribution.UNDETERMINED
    assert _in_scope("apps/x.py", ()) is True
    assert _in_scope("apps/x.py", ("apps",)) is True
    assert _in_scope("apps/x.py", ("other",)) is False
    assert _in_scope("apps/x.py", ("",)) is True


def test_merge_window_attribution_marks_ambiguous_paths(tmp_path: Path) -> None:
    """A path changed by both windows cannot be attributed to one actor."""
    workspace = _workspace(tmp_path)
    baseline = capture_git_baseline(workspace)
    final = GitState(True, changed_files=("a", "b"))  # noqa: FBT003
    change = GitChange("a", "modified", ChangeAttribution.EXECUTION)
    audit = GitAudit(
        baseline=baseline,
        final_state=final,
        changes=(change, GitChange("b", "modified", ChangeAttribution.EXECUTION)),
        unexpected_paths=("a", "b"),
    )

    def _window() -> GitAudit:
        return GitAudit(baseline=baseline, final_state=final, changes=(change,), unexpected_paths=())

    merged = merge_window_attribution(
        audit,
        agent_audits=(_window(),),
        validation_audits=(_window(),),
    )
    assert merged.changes[0].attribution is ChangeAttribution.UNDETERMINED
    assert merged.unexpected_paths == ("b",)


def test_discover_instructions_limits_deduplicates_and_handles_security(tmp_path: Path) -> None:
    """Instruction discovery is bounded and skips unreadable/escaping files."""
    workspace = _workspace(tmp_path)
    for index in range(85):
        write(workspace.root / f"pkg{index}" / "AGENTS.md", "instruction\n")
    write(workspace.root / "README.md", "readme\n")
    write(workspace.root / "ARCHITECTURE.md", "architecture\n")
    instructions = discover_instructions(workspace)
    assert len(instructions.documents) <= 80
    assert any(document.kind == "readme" for document in instructions.documents)
    assert len(list(_iter_named(workspace, "AGENTS.md"))) == 80
    assert len(list(_iter_architecture_docs(workspace))) >= 1
    documents: list[object] = []
    seen: set[str] = set()
    _append_document(documents, seen, workspace, workspace.root / "README.md", kind="readme")  # type: ignore[arg-type]
    _append_document(documents, seen, workspace, workspace.root / "README.md", kind="readme")  # type: ignore[arg-type]
    assert len(documents) == 1
    _append_document(documents, seen, workspace, tmp_path / "outside.md", kind="readme")  # type: ignore[arg-type]
    assert _read_text(tmp_path / "missing.md") == ""
    assert list(_root_named(workspace, ("missing.md",))) == []
