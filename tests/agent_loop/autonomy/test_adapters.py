"""Tool adapter command construction and validation aggregation tests."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.tools import ToolDiscovery
from agent_loop.autonomy.discovery.tools import ToolInventory
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.discovery.tools import ToolState
from agent_loop.autonomy.execution.runner import ValidationRunner
from agent_loop.autonomy.tools.adapters import BanditAdapter
from agent_loop.autonomy.tools.adapters import PreCommitAdapter
from agent_loop.autonomy.tools.adapters import PytestAdapter
from agent_loop.autonomy.tools.adapters import PytestSelectionKind
from agent_loop.autonomy.tools.adapters import RuffAdapter
from agent_loop.autonomy.tools.adapters import default_registry
from agent_loop.autonomy.tools.adapters import select_pytest_selection
from agent_loop.autonomy.tools.adapters import select_pytest_targets
from agent_loop.autonomy.tools.base import ToolContext
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import CommandOutcome
from agent_loop.autonomy.tools.registry import ToolRegistry
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_adapter_context
from tests.agent_loop.autonomy.support import make_context
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

_PYTEST_NO_TESTS_EXIT = 5

if TYPE_CHECKING:
    from pathlib import Path


def test_ruff_uses_target_interpreter_and_focused_paths(tmp_path: Path) -> None:
    """Ruff runs as a module in the target venv over changed files."""
    context = make_adapter_context(tmp_path)
    runner = RecordingRunner()
    result = RuffAdapter().run(context, runner)
    command, cwd, env, timeout = runner.calls[0]
    assert command == (str(context.environment.python_executable), "-m", "ruff", "check", "apps/sample/models.py")
    assert cwd == context.workspace.root
    assert env["VIRTUAL_ENV"] == str(context.workspace.root / ".venv")
    assert timeout == context.config.command_timeout_seconds
    assert result.status is ToolStatus.PASS


def test_ruff_full_mode_uses_project_root(tmp_path: Path) -> None:
    """Full validation runs Ruff against the project root."""
    context = make_adapter_context(tmp_path, full=True)
    runner = RecordingRunner()
    RuffAdapter().run(context, runner)
    assert runner.calls[0][0][-1] == "."


def test_ruff_skips_when_no_changed_python_files(tmp_path: Path) -> None:
    """Focused validation does not run without a Python target."""
    context = make_adapter_context(tmp_path, changed_files=("README.md",))
    runner = RecordingRunner()
    result = RuffAdapter().run(context, runner)
    assert result.status is ToolStatus.SKIPPED
    assert runner.calls == []


def test_pytest_maps_changed_module_to_mirrored_test(tmp_path: Path) -> None:
    """A non-app production module maps to tests/test_<stem>.py when present."""
    context = make_adapter_context(tmp_path, changed_files=("lib/models.py",))
    write(context.workspace.root / "lib" / "models.py", "class Model:\n    pass\n")
    assert select_pytest_targets(context) == ("tests/test_models.py",)
    runner = RecordingRunner()
    PytestAdapter().run(context, runner)
    assert runner.calls[0][0][-1] == "tests/test_models.py"


def test_pytest_selects_changed_test_file_directly(tmp_path: Path) -> None:
    """A changed test file is executed directly."""
    context = make_adapter_context(tmp_path, changed_files=("tests/test_sample.py",))
    assert select_pytest_targets(context) == ("tests/test_sample.py",)


def test_pytest_falls_back_to_full_suite_without_mapping(tmp_path: Path) -> None:
    """An unmapped production change selects the project's default suite."""
    context = make_adapter_context(tmp_path, changed_files=("apps/sample/unmapped.py",))
    assert select_pytest_targets(context) == ()
    runner = RecordingRunner()
    PytestAdapter().run(context, runner)
    assert runner.calls[0][0][-1] == "pytest"


def test_pre_commit_uses_files_in_focused_mode_and_all_files_in_full_mode(tmp_path: Path) -> None:
    """Pre-commit receives explicit changed files or the full project set."""
    focused = make_adapter_context(tmp_path)
    runner = RecordingRunner()
    PreCommitAdapter().run(focused, runner)
    assert runner.calls[0][0][-3:] == ("run", "--files", "apps/sample/models.py")
    full = make_adapter_context(tmp_path / "full", full=True)
    PreCommitAdapter().run(full, runner)
    assert runner.calls[1][0][-2:] == ("run", "--all-files")


def test_bandit_uses_dot_bandit_config_when_present(tmp_path: Path) -> None:
    """Bandit receives the target configuration file when it exists."""
    root = make_project(tmp_path / "repo")
    write(root / ".bandit", "skips: []\n")
    context = make_context(root, full=True)
    runner = RecordingRunner()
    BanditAdapter().run(context, runner)
    command = runner.calls[0][0]
    assert "-c" in command
    assert str(context.workspace.root / ".bandit") in command


def test_validation_runner_preserves_not_configured_unavailable_and_unknown(tmp_path: Path) -> None:
    """Aggregation never converts missing or unknown tools into PASS."""
    context = make_adapter_context(tmp_path)
    inventory = ToolInventory(
        entries=(
            ToolDiscovery(kind=ToolKind.RUFF, state=ToolState.NOT_CONFIGURED, reason="not configured"),
            ToolDiscovery(kind=ToolKind.MYPY, state=ToolState.CONFIGURED_UNAVAILABLE, reason="missing module"),
            ToolDiscovery(kind=ToolKind.PYLINT, state=ToolState.UNKNOWN, reason="probe failed"),
        ),
    )
    context = ToolContext(
        workspace=context.workspace,
        project=context.project,
        environment=context.environment,
        inventory=inventory,
        changed_files=context.changed_files,
        full=context.full,
        config=context.config,
    )
    registry = ToolRegistry()
    registry.register(RuffAdapter())
    report = ValidationRunner(registry, RecordingRunner()).run(
        context,
        (ToolKind.RUFF, ToolKind.MYPY, ToolKind.PYLINT),
    )
    assert [result.status for result in report.results] == [
        ToolStatus.SKIPPED,
        ToolStatus.UNAVAILABLE,
        ToolStatus.UNKNOWN,
    ]
    assert report.status is ToolStatus.UNKNOWN


def test_default_registry_contains_django_slice_adapters() -> None:
    """The default registry is deterministic and excludes read-only Git."""
    kinds = default_registry().kinds()
    assert kinds == (
        ToolKind.GIT,
        ToolKind.PRE_COMMIT,
        ToolKind.RUFF,
        ToolKind.MYPY,
        ToolKind.PYLINT,
        ToolKind.BANDIT,
        ToolKind.PYTEST,
        ToolKind.BEHAVE,
        ToolKind.DOCKER_COMPOSE,
    )


def test_ruff_adapter_prefers_target_virtualenv_binary(tmp_path: Path) -> None:
    """Ruff is executed as a binary when the target venv provides one."""
    root = make_project(tmp_path / "repo")
    ruff_bin = root / ".venv" / "bin" / "ruff"
    ruff_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ruff_bin.chmod(0o755)
    context = make_context(root, full=True)
    runner = RecordingRunner()
    RuffAdapter().run(context, runner)
    assert runner.calls[0][0] == (str(ruff_bin), "check", ".")


def test_pytest_maps_django_app_tests_directory(tmp_path: Path) -> None:
    """Django app tests directories are considered for focused selection."""
    context = make_adapter_context(tmp_path)
    write(context.workspace.root / "apps" / "sample" / "tests" / "test_models.py", "def test_models():\n    pass\n")
    targets = select_pytest_targets(context)
    assert "apps/sample/tests/test_models.py" in targets


def test_pytest_maps_sibling_tests_module(tmp_path: Path) -> None:
    """A Django app sibling tests.py is considered for focused selection."""
    context = make_adapter_context(tmp_path)
    write(context.workspace.root / "apps" / "sample" / "tests.py", "def test_models():\n    pass\n")
    targets = select_pytest_targets(context)
    assert "apps/sample/tests.py" in targets


def test_pytest_maps_nested_tests_tree(tmp_path: Path) -> None:
    """Nested tests directories are considered for focused selection."""
    context = make_adapter_context(tmp_path, changed_files=("lib/models.py",))
    write(context.workspace.root / "lib" / "models.py", "value = 1\n")
    (context.workspace.root / "tests" / "test_models.py").unlink()
    write(context.workspace.root / "tests" / "unit" / "test_models.py", "def test_models():\n    pass\n")
    targets = select_pytest_targets(context)
    assert targets == ("tests/unit/test_models.py",)


def test_pytest_no_tests_collected_is_skipped(tmp_path: Path) -> None:
    """Pytest exit 5 is an explicit skip, not a failure or success."""
    context = make_adapter_context(tmp_path, full=True)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("pytest",),
                cwd=str(context.workspace.root),
                exit_code=5,
                stdout="no tests ran",
                stderr="",
                duration_seconds=0.0,
            ),
        ],
    )
    result = PytestAdapter().run(context, runner)
    assert result.status is ToolStatus.SKIPPED
    assert result.exit_code == _PYTEST_NO_TESTS_EXIT
    assert "collected no tests" in result.message


def test_pytest_real_failure_is_fail(tmp_path: Path) -> None:
    """A real pytest failure remains a proven failure."""
    context = make_adapter_context(tmp_path, full=True)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("pytest",),
                cwd=str(context.workspace.root),
                exit_code=1,
                stdout="1 failed",
                stderr="",
                duration_seconds=0.0,
            ),
        ],
    )
    result = PytestAdapter().run(context, runner)
    assert result.status is ToolStatus.FAIL


def test_pytest_ignores_external_changed_paths(tmp_path: Path) -> None:
    """External paths are not selected as pytest targets."""
    context = make_adapter_context(tmp_path, changed_files=("../outside.py",))
    assert select_pytest_targets(context) == ()


def test_validation_runner_excludes_git_from_default_validation(tmp_path: Path) -> None:
    """Git is available explicitly but is not counted as a quality validation."""
    context = make_adapter_context(tmp_path)
    registry = default_registry()
    default_results = ValidationRunner(registry, RecordingRunner()).run(context)
    assert ToolKind.GIT not in {result.tool for result in default_results.results}
    explicit_results = ValidationRunner(registry, RecordingRunner()).run(context, (ToolKind.GIT,))
    assert [result.tool for result in explicit_results.results] == [ToolKind.GIT]


def test_pytest_missing_changed_file_falls_back_to_full_suite(tmp_path: Path) -> None:
    """A changed path that no longer exists does not become a pytest target."""
    context = make_adapter_context(tmp_path, changed_files=("apps/sample/missing.py",))
    assert select_pytest_targets(context) == ()
    runner = RecordingRunner()
    PytestAdapter().run(context, runner)
    assert runner.calls[0][0][-1] == "pytest"


def test_pytest_app_local_match_wins_over_global_homonym(tmp_path: Path) -> None:
    """An app-local test is preferred over an unrelated global homonym."""
    context = make_adapter_context(tmp_path)
    write(context.workspace.root / "apps" / "sample" / "tests" / "test_models.py", "def test_models():\n    pass\n")
    assert select_pytest_targets(context) == ("apps/sample/tests/test_models.py",)


def test_pytest_rejects_global_homonym_for_app_module(tmp_path: Path) -> None:
    """A global test named after a model is not evidence for another Django app."""
    context = make_adapter_context(tmp_path)
    write(context.workspace.root / "apps" / "b" / "models.py", "value = 1\n")
    write(
        context.workspace.root / "tests" / "test_models.py",
        "from apps.b.models import Model\n\ndef test_models():\n    assert Model\n",
    )
    selection = select_pytest_selection(context)
    assert selection.kind is PytestSelectionKind.FULL_SUITE
    assert selection.targets == ()
    assert "no deterministic test mapping" in selection.reason


def test_pytest_app_local_testpath_is_a_deterministic_association(tmp_path: Path) -> None:
    """An app-local test root remains a valid focused association."""
    context = make_adapter_context(tmp_path)
    context = replace(context, project=replace(context.project, pytest_testpaths=("apps/sample/specs",)))
    write(context.workspace.root / "apps" / "sample" / "specs" / "test_models.py", "def test_models():\n    pass\n")
    selection = select_pytest_selection(context)
    assert selection.kind is PytestSelectionKind.FOCUSED
    assert selection.targets == ("apps/sample/specs/test_models.py",)


def test_pytest_special_paths_are_preserved_for_focused_selection(tmp_path: Path) -> None:
    """Spaces and non-ASCII names are not dropped from pytest targets."""
    context = make_adapter_context(tmp_path, changed_files=("tests/test_café.py", "tests/test_with space.py"))
    write(context.workspace.root / "tests" / "test_café.py", "def test_cafe():\n    pass\n")
    write(context.workspace.root / "tests" / "test_with space.py", "def test_space():\n    pass\n")
    assert select_pytest_targets(context) == ("tests/test_café.py", "tests/test_with space.py")


def test_ruff_and_pre_commit_receive_special_paths(tmp_path: Path) -> None:
    """Focused adapters keep decoded Git paths instead of silently skipping them."""
    root = make_project(tmp_path / "repo")
    write(root / "apps" / "sample" / "models.py", "class Sample:\n    pass\n")
    write(root / "with space.py", "value = 1\n")
    write(root / "café.py", "value = 2\n")
    context = make_context(root, changed_files=("apps/sample/models.py", "with space.py", "café.py"))
    runner = RecordingRunner()
    RuffAdapter().run(context, runner)
    assert runner.calls[0][0][-3:] == ("apps/sample/models.py", "café.py", "with space.py")
    PreCommitAdapter().run(context, runner)
    assert runner.calls[1][0][-5:] == (
        "run",
        "--files",
        "apps/sample/models.py",
        "café.py",
        "with space.py",
    )


def test_pytest_ambiguous_global_homonyms_use_full_suite(tmp_path: Path) -> None:
    """Two unrelated homonymous tests are ambiguous and trigger the full suite."""
    context = make_adapter_context(tmp_path, changed_files=("lib/models.py",))
    (context.workspace.root / "tests" / "test_models.py").unlink()
    write(context.workspace.root / "lib" / "models.py", "value = 1\n")
    write(context.workspace.root / "tests" / "a" / "test_models.py", "def test_a():\n    pass\n")
    write(context.workspace.root / "tests" / "b" / "test_models.py", "def test_b():\n    pass\n")
    selection = select_pytest_selection(context)
    assert selection.kind is PytestSelectionKind.FULL_SUITE
    assert selection.targets == ()
    assert "ambiguous" in selection.reason
    runner = RecordingRunner()
    result = PytestAdapter().run(context, runner)
    assert runner.calls[0][0][-1] == "pytest"
    assert "full suite" in result.message


def test_pytest_uses_configured_testpaths(tmp_path: Path) -> None:
    """Configured testpaths participate in deterministic target discovery."""
    context = make_adapter_context(tmp_path, changed_files=("lib/views.py",))
    write(context.workspace.root / "lib" / "views.py", "value = 1\n")
    context = replace(context, project=replace(context.project, pytest_testpaths=("specs",)))
    write(context.workspace.root / "specs" / "test_views.py", "def test_views():\n    pass\n")
    selection = select_pytest_selection(context)
    assert selection.kind is PytestSelectionKind.FOCUSED
    assert selection.targets == ("specs/test_views.py",)


def test_pytest_testpaths_outside_workspace_are_ignored(tmp_path: Path) -> None:
    """Configured testpaths cannot point outside the workspace."""
    context = make_adapter_context(tmp_path, changed_files=("lib/models.py",))
    (context.workspace.root / "tests" / "test_models.py").unlink()
    write(context.workspace.root / "lib" / "models.py", "value = 1\n")
    context = replace(context, project=replace(context.project, pytest_testpaths=("../outside",)))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "test_models.py").write_text("def test_outside():\n    pass\n", encoding="utf-8")
    assert select_pytest_targets(context) == ()


def test_default_registry_covers_every_tool_kind() -> None:
    """Every discovered tool family has a registered adapter contract."""
    assert set(default_registry().kinds()) == set(ToolKind)


def test_all_available_inventory_tools_have_adapters(tmp_path: Path) -> None:
    """An available inventory entry always has a registered adapter."""
    context = make_adapter_context(tmp_path)
    registry = default_registry()
    missing = [entry.kind for entry in context.inventory.available() if registry.get(entry.kind) is None]
    assert missing == []


def test_available_tool_without_adapter_is_not_executable(tmp_path: Path) -> None:
    """Inventory availability alone does not make a tool executable."""
    context = make_adapter_context(tmp_path)
    inventory = ToolInventory(
        entries=(ToolDiscovery(kind=ToolKind.RUFF, state=ToolState.CONFIGURED_AVAILABLE),),
    )
    context = replace(context, inventory=inventory)
    report = ValidationRunner(ToolRegistry(), RecordingRunner()).run(context, (ToolKind.RUFF,))
    assert report.status is ToolStatus.UNKNOWN
    assert "no adapter registered" in report.results[0].message
