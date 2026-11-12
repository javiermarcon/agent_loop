"""Security policy tests for target workspaces and environments."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from agent_loop.autonomy.errors import SecurityError
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.security import git_environment
from agent_loop.autonomy.security import sanitize_environment
from tests.agent_loop.autonomy.support import git_executable
from tests.agent_loop.autonomy.support import make_project

if TYPE_CHECKING:
    from pathlib import Path


def _workspace(tmp_path: Path) -> TargetWorkspace:
    return TargetWorkspace.open(make_project(tmp_path / "repo"))


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        (git_executable(), *args),
        cwd=root,
        check=True,
        env=git_environment(),
    )


def _commit(root: Path) -> None:
    _run_git(root, "add", "-A")
    _run_git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init")


def test_open_accepts_git_directory(tmp_path: Path) -> None:
    """A real Git work tree becomes the authorized root."""
    workspace = _workspace(tmp_path)
    assert workspace.root == (tmp_path / "repo").resolve()


def test_open_rejects_missing_and_non_git_paths(tmp_path: Path) -> None:
    """Missing paths and non-Git directories fail closed."""
    with pytest.raises(SecurityError, match="does not exist"):
        TargetWorkspace.open(tmp_path / "missing")
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(SecurityError, match="not a Git work tree"):
        TargetWorkspace.open(plain)


def test_resolve_rejects_parent_traversal_and_absolute_escape(tmp_path: Path) -> None:
    """Relative traversal and absolute paths outside the root are rejected."""
    workspace = _workspace(tmp_path)
    with pytest.raises(SecurityError, match="escapes"):
        workspace.resolve("../outside.txt")
    outside = tmp_path / "outside.txt"
    with pytest.raises(SecurityError, match="escapes"):
        workspace.resolve(outside)


def test_resolve_rejects_symlink_escape_but_member_path_allows_interpreter_symlink(tmp_path: Path) -> None:
    """Symlinked files cannot escape, while the venv interpreter symlink is allowed lexically."""
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace.root / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(SecurityError, match="escapes"):
        workspace.resolve(link)
    venv_python = workspace.root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.unlink(missing_ok=True)
    venv_python.symlink_to(outside)
    assert workspace.member_path(".venv/bin/python") == venv_python


def test_sanitize_environment_removes_agent_loop_and_shipii_context() -> None:
    """Only neutral variables survive; secrets and interpreter paths do not."""
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "PYTHONPATH": "/shipii",
        "VIRTUAL_ENV": "/shipii/.venv",
        "SHIPII_TOKEN": "secret",
        "AGENT_LOOP_RUN_ID": "run-1",
        "DJANGO_SETTINGS_MODULE": "shipii.settings",
    }
    environment = sanitize_environment(source, extra={"TARGET_FLAG": "1"}, path_prepend="/target/bin")
    assert environment["PATH"] == "/target/bin:/usr/bin"
    assert environment["HOME"] == "/home/user"
    assert environment["TARGET_FLAG"] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "PYTHONPATH" not in environment
    assert "VIRTUAL_ENV" not in environment
    assert "SHIPII_TOKEN" not in environment
    assert "AGENT_LOOP_RUN_ID" not in environment
    assert "DJANGO_SETTINGS_MODULE" not in environment


def test_sanitize_environment_rejects_forbidden_extra_variables() -> None:
    """Explicit target variables cannot reintroduce Agent Loop or Shipii context."""
    with pytest.raises(SecurityError, match="not allowed"):
        sanitize_environment({}, extra={"SHIPII_TOKEN": "secret"})
    with pytest.raises(SecurityError, match="not allowed"):
        sanitize_environment({}, extra={"AGENT_LOOP_RUN_ID": "run"})
    with pytest.raises(SecurityError, match="invalid environment"):
        sanitize_environment({}, extra={"BAD=KEY": "value"})


def test_sanitize_environment_rejects_git_context_variables(tmp_path: Path) -> None:
    """Explicit target variables cannot redirect Git discovery or its index."""
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_EXEC_PATH",
        "GIT_SSH_COMMAND",
    ):
        with pytest.raises(SecurityError, match="Git context variable"):
            sanitize_environment({}, extra={name: str(tmp_path / "redirect")})


def test_open_accepts_git_worktree(tmp_path: Path) -> None:
    """A Git worktree is a valid target even though .git is a file."""
    repo = make_project(tmp_path / "repo")
    _commit(repo)
    worktree = tmp_path / "worktree"
    _run_git(repo, "worktree", "add", "-q", str(worktree))
    workspace = TargetWorkspace.open(worktree)
    assert workspace.root == worktree.resolve()


def test_open_rejects_arbitrary_git_file(tmp_path: Path) -> None:
    """An arbitrary file named .git is not a repository marker."""
    root = tmp_path / "fake"
    root.mkdir()
    (root / ".git").write_text("not a gitdir\n", encoding="utf-8")
    with pytest.raises(SecurityError, match="not a Git work tree"):
        TargetWorkspace.open(root)


def test_open_rejects_dot_git_directory_without_repository(tmp_path: Path) -> None:
    """An empty .git directory is not a valid Git work tree."""
    root = tmp_path / "fake"
    (root / ".git").mkdir(parents=True)
    with pytest.raises(SecurityError, match="not a Git work tree"):
        TargetWorkspace.open(root)


def test_open_rejects_nested_directory_inside_repository(tmp_path: Path) -> None:
    """A subdirectory of a repository is not itself the authorized root."""
    repo = make_project(tmp_path / "repo")
    nested = repo / "nested"
    nested.mkdir()
    with pytest.raises(SecurityError, match="not a Git work tree"):
        TargetWorkspace.open(nested)


def test_iter_files_skips_external_broken_and_chained_symlinks(tmp_path: Path) -> None:
    """Traversal never reads outside the workspace or loops through links."""
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (workspace.root / "external").symlink_to(outside)
    (workspace.root / "broken").symlink_to(tmp_path / "missing")
    (workspace.root / "chain").symlink_to(workspace.root / "external")
    (workspace.root / "loop").symlink_to(workspace.root)
    (workspace.root / "local").mkdir()
    (workspace.root / "local" / "ok.txt").write_text("ok", encoding="utf-8")
    files = {path.relative_to(workspace.root).as_posix() for path in workspace.iter_files()}
    assert "local/ok.txt" in files
    assert "external/secret.txt" not in files
    assert "chain/secret.txt" not in files
    assert all("secret.txt" not in path for path in files)


def test_iter_files_allows_internal_symlink(tmp_path: Path) -> None:
    """An internal symlink remains traversable."""
    workspace = _workspace(tmp_path)
    target = workspace.root / "target"
    target.mkdir()
    (target / "ok.txt").write_text("ok", encoding="utf-8")
    (workspace.root / "alias").symlink_to(target)
    files = {path.relative_to(workspace.root).as_posix() for path in workspace.iter_files()}
    assert "target/ok.txt" in files or "alias/ok.txt" in files


def test_git_environment_removes_repository_redirect_variables() -> None:
    """Git isolation drops path, index, object and configuration overrides."""
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "GIT_DIR": "/opt/other/.git",
        "GIT_WORK_TREE": "/opt/other",
        "GIT_INDEX_FILE": "/opt/other/index",
        "GIT_OBJECT_DIRECTORY": "/opt/other/objects",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/opt/other/alt",
        "GIT_CONFIG": "/opt/other/config",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.worktree",
        "GIT_CONFIG_VALUE_0": "/opt/other",
        "GIT_EXEC_PATH": "/opt/other/libexec",
        "GIT_SSH_COMMAND": "ssh -o ProxyCommand=evil",
    }
    environment = git_environment(source)
    assert environment["PATH"] == "/usr/bin"
    assert environment["HOME"] == "/home/user"
    assert all("GIT_" not in name for name in environment)


def test_open_rejects_hostile_git_dir_and_work_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-repository cannot borrow another repository through GIT_* variables."""
    real = make_project(tmp_path / "real")
    fake = tmp_path / "fake"
    fake.mkdir()
    monkeypatch.setenv("GIT_DIR", str(real / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(fake))
    with pytest.raises(SecurityError, match="not a Git work tree"):
        TargetWorkspace.open(fake)


def test_open_still_accepts_real_repository_under_hostile_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanitization does not break legitimate repositories and worktrees."""
    real = make_project(tmp_path / "real")
    _commit(real)
    worktree = tmp_path / "worktree"
    _run_git(real, "worktree", "add", "-q", str(worktree))
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("GIT_DIR", str(other))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert TargetWorkspace.open(real).root == real.resolve()
    assert TargetWorkspace.open(worktree).root == worktree.resolve()


def test_relative_normalizes_equivalent_workspace_paths(tmp_path: Path) -> None:
    """Canonical relative POSIX paths bound instruction and audit scope."""
    workspace = _workspace(tmp_path)
    expected = "apps/sample"
    assert workspace.relative("apps/sample") == expected
    assert workspace.relative("./apps/sample") == expected
    assert workspace.relative("apps/sample/") == expected
    assert workspace.relative(workspace.root / "apps" / "sample") == expected
    with pytest.raises(SecurityError, match="escapes"):
        workspace.relative(tmp_path / "outside")
    with pytest.raises(SecurityError, match="escapes"):
        workspace.relative("../outside")
