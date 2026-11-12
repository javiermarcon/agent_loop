"""Tests for canonical agent-loop invocation lifecycle orchestration."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import pytest

from agent_loop.application.lifecycle import LifecycleInvocation
from agent_loop.application.lifecycle import ResumeConfiguration
from agent_loop.application.lifecycle import RunContext
from agent_loop.application.lifecycle import RunLock
from agent_loop.application.lifecycle import execute_selected_mode
from agent_loop.application.lifecycle import render_run_context
from agent_loop.application.lifecycle import run_lifecycle
from agent_loop.domain.errors import AgentLoopError
from agent_loop.domain.errors import ResumeArgumentError

if TYPE_CHECKING:
    from pathlib import Path

_SINGLE_EXIT = 11
_MULTI_EXIT = 12
_RESUME_EXIT = 7
_RESUME_CONFLICT = "resume conflict"
_INVALID_CONTEXT = "invalid context"
_INVALID_RESUME_STATE = "invalid resume state"
_TARGET_REQUIRED = "target required"
_SINGLE_FAILED = "single failed"
_MULTI_FAILED = "multi failed"


class RepoRootSource(StrEnum):
    """Identify the authoritative source selected for the target worktree."""

    EXPLICIT_REPO_ROOT = "explicit_repo_root"
    ENHANCEMENT_REPO_ROOT = "enhancement_repo_root"
    CWD_GIT_ROOT = "cwd_git_root"


@dataclass(frozen=True)
class AgentLoopRunContext:
    """Repository identities used by the lifecycle rendering contract."""

    orchestrator_root: Path
    target_repo_root: Path | None
    preview_base_dir: Path
    enhancement_repo_root: Path | None
    target_repo_root_source: RepoRootSource | None
    python_executable: Path


@dataclass(frozen=True)
class _Context:
    """Minimal resolved repository context used by lifecycle tests."""

    orchestrator_root: Path
    target_repo_root: Path | None
    preview_base_dir: Path
    enhancement_repo_root: Path | None = None
    target_repo_root_source: None = None


@dataclass
class _Lock:
    """Record ownership release without filesystem side effects."""

    events: list[str]

    def release(self) -> None:
        """Record one release request."""
        self.events.append("release")


class _Operations:
    """Deterministic host boundary for coordinator behavior tests."""

    application_error_type = AgentLoopError
    resume_argument_error_type = ResumeArgumentError

    def __init__(self, tmp_path: Path) -> None:
        self.events: list[str] = []
        self.messages: list[str] = []
        self.argv: tuple[str, ...] = ()
        self.context: RunContext = _Context(tmp_path, tmp_path, tmp_path)
        self.lock: RunLock | None = None
        self.resume_exit: int | None = None
        self.durable_single = False
        self.list_resumable_runs = False
        self.failure: str | None = None

    def parse_args(self, argv: tuple[str, ...]) -> LifecycleInvocation:
        """Create a complete mutable invocation from explicit tokens."""
        self.events.append("parse")
        self.argv = argv
        return LifecycleInvocation(
            argparse.Namespace(
                resume_multi_requirement_run=None,
                resume_last=False,
                list_resumable_runs=self.list_resumable_runs,
                multi_requirement="--multi" in argv,
                dry_run="--dry-run" in argv,
            ),
        )

    def validate_resume_cli(self, argv: tuple[str, ...]) -> None:
        """Record resume validation or raise its dedicated error."""
        self.events.append("validate_resume")
        if self.failure == "resume":
            raise ResumeArgumentError(_RESUME_CONFLICT)

    def resolve_run_context(self, invocation: LifecycleInvocation) -> RunContext:
        """Return the configured context after parsing."""
        del invocation
        self.events.append("resolve_context")
        if self.failure == "context":
            raise AgentLoopError(_INVALID_CONTEXT)
        return self.context

    def validate_multi_requirement_args(self, invocation: LifecycleInvocation) -> None:
        """Record multi-requirement validation."""
        del invocation
        self.events.append("validate_multi")

    def emit_run_context(self, context: RunContext) -> None:
        """Record context reporting after validation."""
        del context
        self.events.append("emit_context")

    def configure_resume(
        self,
        invocation: LifecycleInvocation,
        context: RunContext,
    ) -> ResumeConfiguration:
        """Return the configured terminal status, context, and lock."""
        del invocation
        self.events.append("configure_resume")
        if self.failure == "configure":
            raise AgentLoopError(_INVALID_RESUME_STATE)
        return ResumeConfiguration.from_values(self.resume_exit, self.lock, context)

    def require_target_repo_root(self, context: RunContext) -> Path:
        """Require the execution target for non-preview modes."""
        self.events.append("require_target")
        if context.target_repo_root is None:
            raise AgentLoopError(_TARGET_REQUIRED)
        return context.target_repo_root

    def execute_selected_mode(
        self,
        invocation: LifecycleInvocation,
        context: RunContext,
        lock: RunLock | None,
    ) -> int:
        """Dispatch through the canonical mode selector."""
        return execute_selected_mode(invocation, context, lock, self)

    def supports_durable_single(self, repo_root: Path) -> bool:
        """Return the configured durable-single capability."""
        del repo_root
        self.events.append("supports_durable_single")
        return self.durable_single

    def prepare_durable_single(
        self,
        invocation: LifecycleInvocation,
        repo_root: Path,
    ) -> None:
        """Record conversion of one prompt into a durable unit."""
        del invocation, repo_root
        self.events.append("prepare_durable_single")

    def run_single(self, invocation: LifecycleInvocation, repo_root: Path) -> int:
        """Run only the legacy single runner."""
        del invocation, repo_root
        self.events.append("run_single")
        if self.failure == "runner":
            raise AgentLoopError(_SINGLE_FAILED)
        return _SINGLE_EXIT

    def run_multi(self, invocation: LifecycleInvocation, repo_root: Path) -> int:
        """Run only the multi-requirement runner."""
        del invocation, repo_root
        self.events.append("run_multi")
        if self.failure == "runner":
            raise AgentLoopError(_MULTI_FAILED)
        return _MULTI_EXIT

    def emit(self, message: str) -> None:
        """Capture operator-facing errors."""
        self.messages.append(message)


def test_lifecycle_orders_validation_context_resume_and_dispatch(tmp_path: Path) -> None:
    """The canonical owner sequences a normal single invocation exactly once."""
    operations = _Operations(tmp_path)

    assert run_lifecycle(("requirement",), operations) == _SINGLE_EXIT
    assert operations.argv == ("requirement",)
    assert operations.events == [
        "parse",
        "validate_resume",
        "resolve_context",
        "validate_multi",
        "emit_context",
        "configure_resume",
        "require_target",
        "supports_durable_single",
        "run_single",
    ]


@pytest.mark.parametrize(
    ("argv", "single_mode", "expected_events", "expected_exit"),
    [
        (("--multi", "--dry-run"), "legacy", ["run_multi"], _MULTI_EXIT),
        (("requirement",), "legacy", ["supports_durable_single", "run_single"], _SINGLE_EXIT),
        (
            ("requirement",),
            "durable",
            ["supports_durable_single", "prepare_durable_single", "run_multi"],
            _MULTI_EXIT,
        ),
        (("--multi",), "legacy", ["run_multi"], _MULTI_EXIT),
    ],
)
def test_lifecycle_selects_exactly_one_execution_mode(
    tmp_path: Path,
    argv: tuple[str, ...],
    single_mode: str,
    expected_events: list[str],
    expected_exit: int,
) -> None:
    """Preview, legacy single, durable single, and multi remain exclusive."""
    operations = _Operations(tmp_path)
    operations.durable_single = single_mode == "durable"
    if argv == ("--multi", "--dry-run"):
        preview = tmp_path / "preview"
        operations.context = _Context(tmp_path, None, preview)

    assert run_lifecycle(argv, operations) == expected_exit
    mode_events = [
        event
        for event in operations.events
        if event in {"supports_durable_single", "prepare_durable_single", "run_single", "run_multi"}
    ]
    assert mode_events == expected_events


def test_lifecycle_terminal_resume_exit_skips_dispatch_and_validation_for_listing(
    tmp_path: Path,
) -> None:
    """A terminal resume result returns before any runner is selected."""
    operations = _Operations(tmp_path)
    operations.resume_exit = _RESUME_EXIT
    operations.list_resumable_runs = True

    assert run_lifecycle(("--list-resumable-runs",), operations) == _RESUME_EXIT
    assert "validate_multi" not in operations.events
    assert "run_single" not in operations.events
    assert "run_multi" not in operations.events


def test_resume_configuration_rejects_exit_with_transferred_lock(tmp_path: Path) -> None:
    """Reject the legacy combination that would return without releasing ownership."""
    context = _Context(tmp_path, tmp_path, tmp_path)

    with pytest.raises(ValueError, match=r"terminal exit.*transferred lock"):
        ResumeConfiguration.from_values(
            _RESUME_EXIT,
            _Lock([]),
            context,
        )


@pytest.mark.parametrize(
    ("failure", "expected_exit", "expected_message"),
    [
        ("resume", 2, "resume conflict"),
        ("context", 1, "[agent-loop] invalid context"),
        ("configure", 1, "[agent-loop] invalid resume state"),
        ("runner", 1, "[agent-loop] single failed"),
    ],
)
def test_lifecycle_maps_expected_errors(
    tmp_path: Path,
    failure: str,
    expected_exit: int,
    expected_message: str,
) -> None:
    """Known validation and operational failures retain their CLI contract."""
    operations = _Operations(tmp_path)
    operations.failure = failure

    assert run_lifecycle((), operations) == expected_exit
    assert operations.messages == [expected_message]


@pytest.mark.parametrize("failure", [None, "runner"])
def test_lifecycle_releases_resume_lock_after_runner_success_or_failure(
    tmp_path: Path,
    failure: str | None,
) -> None:
    """Acquired resume ownership is released around every runner outcome."""
    operations = _Operations(tmp_path)
    operations.failure = failure
    operations.lock = _Lock(operations.events)

    expected_exit = _SINGLE_EXIT if failure is None else 1
    assert run_lifecycle((), operations) == expected_exit
    assert operations.events[-1] == "release"
    assert operations.events.count("release") == 1


def test_lifecycle_release_failure_propagates_after_one_attempt(tmp_path: Path) -> None:
    """Preserve cleanup failure as the terminal lifecycle error without retrying."""
    operations = _Operations(tmp_path)
    release_calls = 0

    class _FailingLock:
        def release(self) -> None:
            nonlocal release_calls
            release_calls += 1
            message = "release unavailable"
            raise OSError(message)

    operations.lock = _FailingLock()

    with pytest.raises(OSError, match="release unavailable"):
        run_lifecycle((), operations)
    assert release_calls == 1


def test_lifecycle_uses_explicit_argv_without_mutating_process_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forwarded tokens are authoritative and leave ``sys.argv`` untouched."""
    process_argv = ["agent-loop", "process-value"]
    monkeypatch.setattr("sys.argv", process_argv)
    operations = _Operations(tmp_path)

    assert run_lifecycle(("explicit-value",), operations) == _SINGLE_EXIT
    assert operations.argv == ("explicit-value",)
    assert process_argv == ["agent-loop", "process-value"]


def test_render_run_context_preserves_real_legacy_target_source(tmp_path: Path) -> None:
    """Target provenance renders the real legacy enum value exactly."""
    context = AgentLoopRunContext(
        orchestrator_root=tmp_path,
        target_repo_root=tmp_path,
        preview_base_dir=tmp_path,
        enhancement_repo_root=None,
        target_repo_root_source=RepoRootSource.EXPLICIT_REPO_ROOT,
        python_executable=tmp_path / "python",
    )

    messages = render_run_context(context)

    assert "[agent-loop] Target source: explicit_repo_root" in messages
