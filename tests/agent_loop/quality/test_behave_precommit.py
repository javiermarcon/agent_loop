"""Contract tests for the focused Behave pre-commit mapper."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from scripts import behave_precommit
from scripts.behave_precommit import features_for_step_file
from scripts.behave_precommit import normalize_path
from scripts.behave_precommit import resolve_targets

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_normalize_path_returns_repo_relative_posix() -> None:
    """Absolute and Windows-style inputs normalize to repository-relative paths."""
    assert normalize_path("./features/execution_tree.feature") == "features/execution_tree.feature"
    assert normalize_path("features\\execution_tree.feature") == "features/execution_tree.feature"


def test_resolve_targets_keeps_existing_feature_files() -> None:
    """A changed feature is executed directly, and missing files are ignored."""
    assert resolve_targets(["features/execution_tree.feature"]) == ["features/execution_tree.feature"]
    assert resolve_targets(["features/missing.feature", "README.md"]) == []


def test_resolve_targets_maps_literal_step_patterns_to_features() -> None:
    """A changed step module resolves the features that contain its literal steps."""
    matched = features_for_step_file("features/steps/autonomy_e2e_steps.py")
    assert "features/final_result_and_autonomy.feature" in matched
    assert resolve_targets(["features/steps/autonomy_e2e_steps.py"]) == sorted(matched)


def test_step_file_without_patterns_has_no_specific_target() -> None:
    """A helper module in the steps package does not invent feature targets."""
    assert features_for_step_file("features/steps/__init__.py") == set()


def test_main_runs_resolved_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook invokes Behave with the resolved feature and propagates its code."""
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []

    def _run(
        command: list[str],
        *,
        cwd: Path,
        check: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check
        calls.append(command)
        environments.append(env)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("GIT_INDEX_FILE", ".git/index")
    monkeypatch.setattr(behave_precommit.subprocess, "run", _run)
    assert behave_precommit.main(["features/execution_tree.feature"]) == 0
    assert calls == [
        [behave_precommit.sys.executable, "-m", "behave", "features/execution_tree.feature"],
    ]
    assert "GIT_INDEX_FILE" not in environments[0]


def test_main_falls_back_to_the_complete_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unresolved change never becomes an unvalidated no-op."""
    calls: list[list[str]] = []

    def _run(
        command: list[str],
        *,
        cwd: Path,
        check: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, env
        calls.append(command)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(behave_precommit.subprocess, "run", _run)
    assert behave_precommit.main([]) == 7
    assert calls == [[behave_precommit.sys.executable, "-m", "behave", "features"]]
