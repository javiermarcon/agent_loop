"""Behave adapter scope and selection tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_loop.autonomy.tools.adapters import BehaveAdapter
from agent_loop.autonomy.tools.adapters import BehaveSelectionKind
from agent_loop.autonomy.tools.adapters import select_behave_selection
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import CommandOutcome
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_adapter_context

if TYPE_CHECKING:
    from pathlib import Path


def test_behave_full_suite_reports_its_scope(tmp_path: Path) -> None:
    """An explicit full run is not presented as focused coverage."""
    context = make_adapter_context(tmp_path, full=True, features=True)
    runner = RecordingRunner()
    result = BehaveAdapter().run(context, runner)
    assert runner.calls[0][0][-1] == "features"
    assert result.status is ToolStatus.PASS
    assert "full suite: full validation requested" in result.message


def test_behave_without_changed_features_reports_full_suite(tmp_path: Path) -> None:
    """No modified features means the whole suite runs, and the report says so."""
    context = make_adapter_context(tmp_path, changed_files=("apps/sample/models.py",), features=True)
    selection = select_behave_selection(context)
    assert selection.kind is BehaveSelectionKind.FULL_SUITE
    assert selection.targets == ()
    runner = RecordingRunner()
    result = BehaveAdapter().run(context, runner)
    assert runner.calls[0][0][-1] == "features"
    assert "full suite: no changed feature files" in result.message


def test_behave_focused_feature_reports_focused_scope(tmp_path: Path) -> None:
    """Changed feature files are executed individually with an explicit reason."""
    context = make_adapter_context(
        tmp_path,
        changed_files=("features/sample.feature",),
        features=True,
    )
    selection = select_behave_selection(context)
    assert selection.kind is BehaveSelectionKind.FOCUSED
    assert selection.targets == ("features/sample.feature",)
    runner = RecordingRunner()
    result = BehaveAdapter().run(context, runner)
    assert runner.calls[0][0][-1] == "features/sample.feature"
    assert "focused: changed feature files" in result.message


def test_behave_missing_changed_feature_falls_back_and_explains(tmp_path: Path) -> None:
    """A missing changed feature cannot silently narrow the suite."""
    context = make_adapter_context(
        tmp_path,
        changed_files=("features/missing.feature",),
        features=True,
    )
    selection = select_behave_selection(context)
    assert selection.kind is BehaveSelectionKind.FULL_SUITE
    assert "missing" in selection.reason


def test_behave_execution_error_is_preserved(tmp_path: Path) -> None:
    """A Behave failure keeps its exit code and scope message."""
    context = make_adapter_context(tmp_path, full=True, features=True)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("behave",),
                cwd=str(context.workspace.root),
                exit_code=1,
                stdout="",
                stderr="step failed",
                duration_seconds=0.0,
            ),
        ],
    )
    result = BehaveAdapter().run(context, runner)
    assert result.status is ToolStatus.FAIL
    assert result.exit_code == 1
    assert result.stderr == "step failed"
    assert "full suite" in result.message


def test_behave_pending_only_suite_is_neutral(tmp_path: Path) -> None:
    """Preexisting NOT IMPLEMENTED scenarios are not actionable code failures."""
    context = make_adapter_context(tmp_path, full=True, features=True)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("behave",),
                cwd=str(context.workspace.root),
                exit_code=1,
                stdout=(
                    "Execution progress: | PASSED | scenario one\n"
                    "Execution progress: | NOT IMPLEMENTED | scenario two\n"
                ),
                stderr="",
                duration_seconds=0.0,
            ),
        ],
    )
    result = BehaveAdapter().run(context, runner)
    assert result.status is ToolStatus.SKIPPED
    assert "pending scenarios are preexisting" in result.message


def test_behave_pending_plus_failed_is_still_a_failure(tmp_path: Path) -> None:
    """A real failed scenario cannot be hidden by unrelated pending scenarios."""
    context = make_adapter_context(tmp_path, full=True, features=True)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("behave",),
                cwd=str(context.workspace.root),
                exit_code=1,
                stdout=(
                    "Execution progress: | NOT IMPLEMENTED | scenario one\n"
                    "Execution progress: | FAILED | scenario two\n"
                ),
                stderr="",
                duration_seconds=0.0,
            ),
        ],
    )
    result = BehaveAdapter().run(context, runner)
    assert result.status is ToolStatus.FAIL


def test_behave_truncated_pending_summary_is_neutral(tmp_path: Path) -> None:
    """Pending detection survives output truncation that removes progress markers."""
    context = make_adapter_context(tmp_path, full=True, features=True)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("behave",),
                cwd=str(context.workspace.root),
                exit_code=1,
                stdout=(
                    "67 features passed, 0 failed, 5 error, 0 skipped\n"
                    "1063 scenarios passed, 0 failed, 30 error, 0 skipped\n"
                    "raise StepNotImplementedError(u'When el paquete es entregado')\n"
                ),
                stderr="",
                duration_seconds=0.0,
            ),
        ],
    )
    result = BehaveAdapter().run(context, runner)
    assert result.status is ToolStatus.SKIPPED


def test_behave_truncated_summary_with_failure_is_not_neutral(tmp_path: Path) -> None:
    """A nonzero failed count remains a real failure after truncation."""
    context = make_adapter_context(tmp_path, full=True, features=True)
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("behave",),
                cwd=str(context.workspace.root),
                exit_code=1,
                stdout=(
                    "1063 scenarios passed, 2 failed, 30 error, 0 skipped\n"
                    "raise StepNotImplementedError(u'When el paquete es entregado')\n"
                ),
                stderr="",
                duration_seconds=0.0,
            ),
        ],
    )
    result = BehaveAdapter().run(context, runner)
    assert result.status is ToolStatus.FAIL
