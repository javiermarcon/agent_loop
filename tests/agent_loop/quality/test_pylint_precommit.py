"""Contract tests for the Pylint pre-commit scope helper."""

from __future__ import annotations

from scripts import pylint_precommit
from scripts.pylint_precommit import TEST_DISABLES
from scripts.pylint_precommit import is_target
from scripts.pylint_precommit import normalize_path


def test_normalize_path_returns_repo_relative_posix() -> None:
    """Absolute and Windows-style inputs normalize to repository-relative paths."""
    assert normalize_path("./agent_loop/domain/work_item.py") == "agent_loop/domain/work_item.py"
    assert normalize_path("agent_loop\\domain\\work_item.py") == "agent_loop/domain/work_item.py"


def test_is_target_covers_package_tests_and_scripts() -> None:
    """Production, test and repository tooling modules are reviewed by Pylint."""
    assert is_target("agent_loop/domain/work_item.py") is True
    assert is_target("tests/agent_loop/domain/test_work_item.py") is True
    assert is_target("scripts/run_quality_checks.py") is True
    assert is_target("conftest.py") is True
    assert is_target("run.py") is True
    assert is_target("docs/migration/README.md") is False


def test_test_scope_disables_match_repository_wide_pylint_behavior() -> None:
    """The test-only disables cover idioms already accepted by the full run."""
    assert "use-implicit-booleaness-not-comparison" in TEST_DISABLES
    assert "superfluous-parens" in TEST_DISABLES
    assert "protected-access" in TEST_DISABLES


def test_main_without_files_is_success() -> None:
    """No staged Python target means no Pylint process."""
    assert pylint_precommit.main([]) == 0


def test_main_ignores_deleted_files() -> None:
    """A staged deletion must not make Pylint fail on a missing path."""
    assert pylint_precommit.main(["agent_loop/domain/does_not_exist.py"]) == 0
