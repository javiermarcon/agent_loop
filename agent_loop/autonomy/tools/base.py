"""Shared tool contracts for structured execution and interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from typing import Protocol

if TYPE_CHECKING:
    from agent_loop.autonomy.config import AutonomyConfig
    from agent_loop.autonomy.discovery.django_project import DjangoProjectInfo
    from agent_loop.autonomy.discovery.tools import ToolInventory
    from agent_loop.autonomy.discovery.tools import ToolKind
    from agent_loop.autonomy.execution.environment import TargetEnvironment
    from agent_loop.autonomy.security import TargetWorkspace
    from agent_loop.autonomy.tools.command import CommandRunner


class ToolStatus(StrEnum):
    """Normalized outcome of one tool execution."""

    PASS = "pass"  # noqa: S105 - status literal, not a credential
    FAIL = "fail"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class FailureKind(StrEnum):
    """Retry-relevant classification of an executed failure."""

    NONE = "none"
    IMPLEMENTATION = "implementation"
    USAGE = "usage"
    CONFIGURATION = "configuration"
    INFRASTRUCTURE = "infrastructure"
    EXECUTION = "execution"

    @property
    def retryable(self) -> bool:
        """Return whether a code change can plausibly resolve the failure."""
        return self is FailureKind.IMPLEMENTATION


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Everything needed to reproduce one tool execution."""

    tool: ToolKind
    command: tuple[str, ...]
    cwd: str
    interpreter: str | None = None
    environment_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Structured result that the control plane can reason about."""

    tool: ToolKind
    status: ToolStatus
    message: str
    invocation: ToolInvocation | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    failure_kind: FailureKind = FailureKind.NONE

    @property
    def succeeded(self) -> bool:
        """Return whether the tool completed successfully."""
        return self.status is ToolStatus.PASS

    @property
    def retryable(self) -> bool:
        """Return whether a retry can plausibly repair this result."""
        return self.status is ToolStatus.FAIL and self.failure_kind.retryable

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        invocation = None
        if self.invocation is not None:
            invocation = {
                "tool": self.invocation.tool.value,
                "command": list(self.invocation.command),
                "cwd": self.invocation.cwd,
                "interpreter": self.invocation.interpreter,
                "environment_keys": list(self.invocation.environment_keys),
            }
        return {
            "tool": self.tool.value,
            "status": self.status.value,
            "message": self.message,
            "invocation": invocation,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "failure_kind": self.failure_kind.value,
        }


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Target-repository context shared by every adapter."""

    workspace: TargetWorkspace
    project: DjangoProjectInfo
    environment: TargetEnvironment
    inventory: ToolInventory
    changed_files: tuple[str, ...]
    full: bool
    config: AutonomyConfig


class ToolAdapter(Protocol):
    """Adapter contract for one tool family."""

    kind: ToolKind

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        """Execute the tool and return a structured result."""
