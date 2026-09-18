"""Canonical sequencing for one agent-loop CLI invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Protocol

from agent_loop.domain.errors import AgentLoopError
from agent_loop.domain.errors import ResumeArgumentError
from agent_loop.domain.exit_codes import INVALID_RESUME_ARGUMENTS_EXIT_CODE

if TYPE_CHECKING:
    import argparse
    from pathlib import Path


@dataclass
class LifecycleInvocation:
    """Keep mutable legacy runner arguments behind explicit lifecycle decisions."""

    args: argparse.Namespace

    @property
    def resume_requested(self) -> bool:
        """Return whether this invocation selects a persisted run."""
        return bool(
            getattr(self.args, "resume_multi_requirement_run", None) or getattr(self.args, "resume_last", False),
        )

    @property
    def lists_resumable_runs(self) -> bool:
        """Return whether the invocation only lists resumable runs."""
        return bool(getattr(self.args, "list_resumable_runs", False))

    @property
    def is_multi_requirement(self) -> bool:
        """Return whether composite orchestration is selected."""
        return bool(getattr(self.args, "multi_requirement", False))

    @property
    def is_preview(self) -> bool:
        """Return whether composite execution is a read-only preview."""
        return self.is_multi_requirement and bool(getattr(self.args, "dry_run", False))

    def select_multi_requirement(self) -> None:
        """Route resume selectors through the composite runner."""
        self.args.multi_requirement = True

    def attach_run_context(self, context: RunContext) -> None:
        """Expose the resolved context to legacy resume and runner code."""
        self.args.agent_loop_run_context = context


class RunContextSource(Protocol):
    """Provenance value selected while resolving a target repository."""

    @property
    def value(self) -> str:
        """Return the stable serialized provenance value."""


class RunContext(Protocol):
    """Repository identities needed by lifecycle decisions and reporting."""

    @property
    def orchestrator_root(self) -> Path:
        """Return the repository that owns the orchestration tooling."""

    @property
    def target_repo_root(self) -> Path | None:
        """Return the optional repository selected for mutation."""

    @property
    def preview_base_dir(self) -> Path:
        """Return the source base used by read-only previews."""

    @property
    def enhancement_repo_root(self) -> Path | None:
        """Return the optional prompt-enhancement repository."""

    @property
    def target_repo_root_source(self) -> RunContextSource | None:
        """Return the provenance of the selected target repository."""


class RunLock(Protocol):
    """Exclusive resume ownership held while a runner executes."""

    def release(self) -> None:
        """Release acquired run ownership."""


@dataclass(frozen=True)
class ResumeConfiguration:
    """Result of restoring resume controls before runner dispatch."""

    exit_code: int | None
    lock: RunLock | None
    run_context: RunContext


class LifecycleOperations(Protocol):
    """Host operations used by the canonical lifecycle coordinator."""

    @property
    def application_error_type(self) -> type[Exception]: ...

    @property
    def resume_argument_error_type(self) -> type[Exception]: ...

    def parse_args(self, argv: tuple[str, ...]) -> LifecycleInvocation: ...

    def validate_resume_cli(self, argv: tuple[str, ...]) -> None: ...

    def resolve_run_context(self, invocation: LifecycleInvocation) -> RunContext: ...

    def validate_multi_requirement_args(self, invocation: LifecycleInvocation) -> None: ...

    def emit_run_context(self, context: RunContext) -> None: ...

    def configure_resume(
        self,
        invocation: LifecycleInvocation,
        context: RunContext,
    ) -> ResumeConfiguration: ...

    def execute_selected_mode(
        self,
        invocation: LifecycleInvocation,
        context: RunContext,
        lock: RunLock | None,
    ) -> int: ...

    def require_target_repo_root(self, context: RunContext) -> Path: ...

    def supports_durable_single(self, repo_root: Path) -> bool: ...

    def prepare_durable_single(
        self,
        invocation: LifecycleInvocation,
        repo_root: Path,
    ) -> None: ...

    def run_single(self, invocation: LifecycleInvocation, repo_root: Path) -> int: ...

    def run_multi(self, invocation: LifecycleInvocation, repo_root: Path) -> int: ...

    def emit(self, message: str) -> None: ...


def execute_selected_mode(
    invocation: LifecycleInvocation,
    run_context: RunContext,
    resume_lock: RunLock | None,
    operations: LifecycleOperations,
) -> int:
    """Select one runner and always release acquired resume ownership."""
    try:
        if invocation.is_preview:
            return operations.run_multi(invocation, run_context.preview_base_dir)
        target_root = operations.require_target_repo_root(run_context)
        if not invocation.is_multi_requirement and not operations.supports_durable_single(target_root):
            return operations.run_single(invocation, target_root)
        if not invocation.is_multi_requirement:
            operations.prepare_durable_single(invocation, target_root)
        return operations.run_multi(invocation, target_root)
    except (AgentLoopError, operations.application_error_type, ValueError) as exc:
        operations.emit(f"[agent-loop] {exc}")
        return 1
    finally:
        if resume_lock is not None:
            resume_lock.release()


def run_lifecycle(argv: tuple[str, ...], operations: LifecycleOperations) -> int:
    """Parse, validate, restore, and dispatch one explicit CLI invocation."""
    invocation = operations.parse_args(argv)
    try:
        operations.validate_resume_cli(argv)
    except (ResumeArgumentError, operations.resume_argument_error_type) as exc:
        operations.emit(str(exc))
        return INVALID_RESUME_ARGUMENTS_EXIT_CODE
    if invocation.resume_requested:
        invocation.select_multi_requirement()
    try:
        run_context = operations.resolve_run_context(invocation)
        if not invocation.lists_resumable_runs:
            operations.validate_multi_requirement_args(invocation)
    except (AgentLoopError, operations.application_error_type) as exc:
        operations.emit(f"[agent-loop] {exc}")
        return 1
    invocation.attach_run_context(run_context)
    operations.emit_run_context(run_context)
    try:
        resume = operations.configure_resume(invocation, run_context)
    except (AgentLoopError, operations.application_error_type) as exc:
        operations.emit(f"[agent-loop] {exc}")
        return 1
    if resume.exit_code is not None:
        return resume.exit_code
    return operations.execute_selected_mode(invocation, resume.run_context, resume.lock)


def render_run_context(context: RunContext) -> tuple[str, ...]:
    """Render stable operator diagnostics for a resolved repository context."""
    target_root = context.target_repo_root
    if target_root is None:
        external_mode = "n/a"
    else:
        external_mode = "yes" if context.orchestrator_root != target_root else "no"
    target_source = context.target_repo_root_source
    target_source_value = target_source.value if target_source is not None else None
    return (
        f"[agent-loop] Orchestrator root: {context.orchestrator_root}",
        f"[agent-loop] Target repository: {target_root}",
        f"[agent-loop] Preview base directory: {context.preview_base_dir}",
        f"[agent-loop] Target source: {target_source_value}",
        f"[agent-loop] Enhancement repository: {context.enhancement_repo_root}",
        f"[agent-loop] External orchestrator mode: {external_mode}",
    )
