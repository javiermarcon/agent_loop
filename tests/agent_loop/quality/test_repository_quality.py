"""Publication contracts: license, quality wiring and CI enforcement."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import cast

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_COPYRIGHT = "Copyright (c) 2026 Javier Marcon"
REQUIRED_GENERIC_HOOKS = {
    "check-case-conflict",
    "check-docstring-first",
    "check-json",
    "check-toml",
    "check-xml",
    "check-yaml",
    "detect-private-key",
    "end-of-file-fixer",
    "trailing-whitespace",
}
REQUIRED_LOCAL_HOOKS = {
    "architecture-boundaries",
    "mypy-changed-files",
    "pylint-changed-files",
    "pytest-changed-files",
    "ruff",
    "ruff-format",
}


def _pyproject() -> dict[str, object]:
    """Load ``pyproject.toml`` as a mapping."""
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _pre_commit_config() -> dict[str, object]:
    """Load the pre-commit configuration as a mapping."""
    return yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))


def _repositories(configuration: dict[str, object]) -> list[dict[str, object]]:
    """Return the repository entries of a pre-commit configuration."""
    return cast("list[dict[str, object]]", configuration["repos"])


def _hooks(repository: dict[str, object]) -> list[dict[str, object]]:
    """Return the hook entries of one pre-commit repository."""
    return cast("list[dict[str, object]]", repository["hooks"])


def _hook_ids(configuration: dict[str, object]) -> set[str]:
    """Return every hook id declared in the configuration."""
    identifiers: set[str] = set()
    for repository in _repositories(configuration):
        for hook in _hooks(repository):
            identifiers.add(cast("str", hook["id"]))
    return identifiers


def test_license_is_mit_with_the_expected_owner() -> None:
    """The distribution carries the standard MIT text and exact copyright line."""
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n")
    assert EXPECTED_COPYRIGHT in text
    assert "AgentLoop contributors" not in text
    assert "Permission is hereby granted, free of charge" in text


def test_pyproject_declares_mit_and_ships_the_license() -> None:
    """PEP 639 metadata, classifiers and the console entry point stay coherent."""
    project = cast("dict[str, object]", _pyproject()["project"])
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [{"name": "Javier Marcon"}]
    assert project["requires-python"] == ">=3.12"
    assert project["scripts"] == {"agent-loop": "agent_loop.autonomy.cli:main"}
    classifiers = set(cast("list[str]", project["classifiers"]))
    assert "Programming Language :: Python :: 3.12" in classifiers
    assert "Programming Language :: Python :: 3.13" in classifiers
    assert "Programming Language :: Python :: 3.14" in classifiers


def test_pre_commit_recovers_generic_hooks_with_pinned_revisions() -> None:
    """Generic file, syntax and secret hooks are present and reproducible."""
    configuration = _pre_commit_config()
    identifiers = _hook_ids(configuration)
    assert identifiers >= REQUIRED_GENERIC_HOOKS
    assert identifiers >= REQUIRED_LOCAL_HOOKS
    assert configuration["fail_fast"] is True
    assert configuration["minimum_pre_commit_version"] == "3.7.0"
    for repository in _repositories(configuration):
        if repository["repo"] == "local":
            continue
        assert repository["rev"], f"unpinned repository: {repository['repo']}"


def test_local_pre_commit_hooks_scope_the_staged_files() -> None:
    """Local hooks run on staged paths instead of scanning unrelated files."""
    configuration = _pre_commit_config()
    local = next(repository for repository in _repositories(configuration) if repository["repo"] == "local")
    hooks = {cast("str", hook["id"]): hook for hook in _hooks(local)}
    for identifier in ("ruff", "ruff-format", "mypy-changed-files", "pylint-changed-files", "pytest-changed-files"):
        assert hooks[identifier]["types"] == ["python"]
        assert "files" in hooks[identifier]
        assert hooks[identifier].get("pass_filenames", True) is True
        assert re.search(cast("str", hooks[identifier]["files"]), "conftest.py"), identifier
        assert re.search(cast("str", hooks[identifier]["files"]), "run.py"), identifier
        assert re.search(cast("str", hooks[identifier]["files"]), "agent_loop/domain/work_item.py"), identifier
    assert hooks["architecture-boundaries"]["pass_filenames"] is False
    assert "types" not in hooks["behave-changed-features"]
    assert hooks["behave-changed-features"]["files"] == "^features/.*\\.(feature|py)$"


def test_ci_enforces_precommit_tests_and_package_verification() -> None:
    """CI does not rely on local hooks or an unverified build."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python scripts/run_quality_checks.py" in workflow
    assert "--cov-fail-under=99" in workflow
    assert "pip check" in workflow
    assert "LICENSE" in workflow
    assert 'agent-loop" --help' in workflow
    assert "-m agent_loop.autonomy --help" in workflow
    for version in ('"3.12"', '"3.13"', '"3.14"'):
        assert version in workflow
    assert "--no-verify" not in workflow


def test_readme_presents_an_autonomous_execution_engine() -> None:
    """Publication copy must not reduce the product to a Django-only edition."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "# AgentLoop — Autonomous Execution Engine" in readme
    assert "Django-only" not in readme
    assert "control plane" in readme.lower()
    assert "wired into the autonomous CLI" in readme


def test_documented_entry_points_and_module_paths_exist() -> None:
    """The publication documents real files, not removed legacy modules."""
    documented = (
        "TESTING.md",
        "run.py",
        "agent_loop/autonomy/cli.py",
        "agent_loop/autonomy/__main__.py",
        "agent_loop/application/execution_orchestrator.py",
        "agent_loop/application/lifecycle.py",
        "agent_loop/application/state_graph.py",
        "agent_loop/adapters/opencode_server/runtime.py",
        "agent_loop/ports/telemetry.py",
        "agent_loop/telemetry/recorder.py",
        "docs/ai/S23_TELEMETRY.md",
        "docs/migration/EXCLUDED_LEGACY_CAPABILITIES.md",
    )
    for relative in documented:
        assert (REPO_ROOT / relative).is_file(), relative
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "agent_loop/cli/" not in readme


def test_documentation_does_not_keep_the_neutral_license_attribution() -> None:
    """Every legal note points to the confirmed owner, not a placeholder."""
    for relative in ("README.md", "docs/migration/README.md"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "AgentLoop contributors" not in text, relative
        assert "Javier Marcon" in text, relative


def test_coverage_threshold_is_the_original_99_percent() -> None:
    """The migrated repository keeps the original coverage gate."""
    coverage = cast("dict[str, object]", _pyproject()["tool"])
    report = cast("dict[str, object]", cast("dict[str, object]", coverage["coverage"])["report"])
    assert report["fail_under"] == 99


def test_pre_commit_recovers_behavioral_and_quality_hooks() -> None:
    """Behave and the quality-regression hook are part of the staged-file flow."""
    identifiers = _hook_ids(_pre_commit_config())
    assert "behave-changed-features" in identifiers
    assert "quality-gate-regression" in identifiers
    assert "debug-statements" in identifiers
    assert "check-builtin-literals" in identifiers


def test_ci_runs_the_complete_runner_and_the_full_coverage_suite() -> None:
    """CI must not substitute changed-file analysis for the integral gate."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python scripts/run_quality_checks.py" in workflow
    assert "--cov-fail-under=99" in workflow
    assert "pytest-changed-files" not in workflow
