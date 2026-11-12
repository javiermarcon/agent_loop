"""Contract tests for the focused pytest pre-commit mapper."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from scripts import pytest_precommit

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
from scripts.pytest_precommit import REPO_ROOT
from scripts.pytest_precommit import is_test_module
from scripts.pytest_precommit import normalize_path
from scripts.pytest_precommit import package_test_candidates
from scripts.pytest_precommit import resolve_targets


def test_normalize_path_accepts_relative_and_absolute_forms() -> None:
    """Pre-commit may pass either spelling; both resolve to repo-relative POSIX."""
    assert normalize_path("./agent_loop/domain/work_item.py") == "agent_loop/domain/work_item.py"
    assert normalize_path(str(REPO_ROOT / "agent_loop" / "domain" / "work_item.py")) == (
        "agent_loop/domain/work_item.py"
    )
    assert normalize_path("agent_loop\\domain\\work_item.py") == "agent_loop/domain/work_item.py"


def test_is_test_module_rejects_non_collectable_files() -> None:
    """Only concrete ``test_*.py`` modules are collectable targets."""
    assert is_test_module("tests/agent_loop/domain/test_work_item.py") is True
    assert is_test_module("tests/agent_loop/domain/__init__.py") is False
    assert is_test_module("tests/agent_loop/domain/conftest.py") is False


def test_package_test_candidates_prefer_exact_mirror() -> None:
    """The mirror test comes before sibling and parent-package fallbacks."""
    candidates = package_test_candidates("agent_loop/domain/work_item.py")
    assert candidates[0] == REPO_ROOT / "tests" / "agent_loop" / "domain" / "test_work_item.py"
    assert all(candidate.suffix == ".py" for candidate in candidates)


def test_package_test_candidates_ignore_package_markers() -> None:
    """Package ``__init__`` changes have no meaningful focused module."""
    assert package_test_candidates("agent_loop/adapters/__init__.py") == ()
    assert package_test_candidates("README.md") == ()


def test_resolve_targets_maps_exact_mirror_test() -> None:
    """A changed domain module maps to its mirrored test module."""
    assert resolve_targets(["agent_loop/domain/work_item.py"]) == [
        "tests/agent_loop/domain/test_work_item.py",
    ]


def test_resolve_targets_uses_suffix_suite_when_exact_mirror_is_absent() -> None:
    """``autonomy/requirement.py`` has no exact mirror; its suffix suite is used."""
    targets = resolve_targets(["agent_loop/autonomy/requirement.py"])
    assert "tests/agent_loop/autonomy/test_requirement_trace.py" in targets


def test_resolve_targets_keeps_changed_test_modules() -> None:
    """A staged test module is itself the focused target."""
    assert resolve_targets(["tests/agent_loop/domain/test_work_item.py"]) == [
        "tests/agent_loop/domain/test_work_item.py",
    ]


def test_resolve_targets_ignores_missing_non_python_and_helpers() -> None:
    """Deleted files, docs and non-test helpers do not become pytest targets."""
    assert resolve_targets(["agent_loop/domain/missing.py", "README.md", "tests/agent_loop/domain/__init__.py"]) == []


def test_resolve_targets_covers_root_entrypoint_and_conftest() -> None:
    """Root Python files are mapped instead of silently bypassing the hook."""
    assert resolve_targets(["run.py"]) == ["tests/agent_loop/autonomy/test_cli.py"]
    assert resolve_targets(["conftest.py"]) == ["tests/agent_loop"]


def test_main_without_targets_is_success() -> None:
    """A change without a focused test does not fail the pre-commit hook."""
    assert pytest_precommit.main([]) == 0


def test_main_sanitizes_git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit hook's GIT_* context cannot redirect temporary Git fixtures."""
    environments: list[dict[str, str]] = []

    def _run(
        command: list[str],
        *,
        cwd: Path,
        check: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del command, cwd, check
        environments.append(env)
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setenv("GIT_INDEX_FILE", ".git/index")
    monkeypatch.setattr(pytest_precommit.subprocess, "run", _run)
    assert pytest_precommit.main(["tests/agent_loop/domain/test_work_item.py"]) == 0
    assert "GIT_INDEX_FILE" not in environments[0]


def test_test_module_detection_does_not_depend_on_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mapping uses ``REPO_ROOT`` from ``__file__`` rather than the process cwd."""
    monkeypatch.chdir(tmp_path)
    assert resolve_targets(["agent_loop/domain/work_item.py"]) == [
        "tests/agent_loop/domain/test_work_item.py",
    ]
