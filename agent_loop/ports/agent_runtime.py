"""Minimal AgentRuntime port: discover inventory and execute tasks."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from agent_loop.domain.capabilities import CapabilityRequirement
from agent_loop.domain.capabilities import RuntimeInventory
from agent_loop.domain.faults import ExecutionFailure


class ExecutionStrategy(StrEnum):
    """Control-plane strategy for one harness execution."""

    PLAN = "plan"
    BUILD = "build"
    REVIEW = "review"


class AgentExecutionStatus(StrEnum):
    """Normalized terminal status for one AgentRuntime execution."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Stable identity of one AgentRuntime adapter instance."""

    runtime_id: str

    def __post_init__(self) -> None:
        if not self.runtime_id or not self.runtime_id.strip():
            message = "RuntimeIdentity.runtime_id must be a non-empty string"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    """Project/runtime context supplied to capability discovery."""

    working_directory: str
    project_context: str | None = None

    def __post_init__(self) -> None:
        if not self.working_directory or not self.working_directory.strip():
            message = "DiscoveryContext.working_directory must be non-empty"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class MutationPolicy:
    """Control-plane mutation envelope for one execution."""

    allow_workspace_mutation: bool


@dataclass(frozen=True, slots=True)
class PermissionPolicy:
    """Control-plane permission envelope for one execution."""

    allow_network: bool


@dataclass(frozen=True, slots=True)
class ExecutionUsage:
    """Optional token/cost observation from one execution."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_micros: int | None = None


@dataclass(frozen=True, slots=True)
class AgentExecutionRequest:
    """Normalized request handed to an AgentRuntime adapter."""

    task: str
    strategy: ExecutionStrategy
    model: str | None
    required_capabilities: tuple[CapabilityRequirement, ...]
    working_directory: str
    mutation_policy: MutationPolicy
    permission_policy: PermissionPolicy
    structured_output_schema: str | None = None
    session_ref: str | None = None
    context_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            message = "AgentExecutionRequest.task must be a non-empty string"
            raise ValueError(message)
        if not self.working_directory or not self.working_directory.strip():
            message = "AgentExecutionRequest.working_directory must be a non-empty string"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class AgentExecutionResult:
    """Normalized outcome returned by an AgentRuntime adapter."""

    status: AgentExecutionStatus
    runtime_identity: RuntimeIdentity
    model_id: str | None = None
    session_ref: str | None = None
    structured_output: str | None = None
    evidence_refs: tuple[str, ...] = ()
    usage: ExecutionUsage | None = None
    observed_failures: tuple[str, ...] = ()
    harness_metadata: tuple[tuple[str, str], ...] = ()
    failure: ExecutionFailure | None = None


class AgentRuntime(Protocol):
    """Outbound port for harness discovery and task execution.

    Adapters must not expose harness-internal tools (filesystem, shell, MCP,
    LSP) on this contract. The control plane declares required capabilities;
    the harness satisfies them internally.
    """

    @property
    def identity(self) -> RuntimeIdentity:
        """Return the stable runtime identity for this adapter."""

    def discover(self, context: DiscoveryContext) -> RuntimeInventory:
        """Build an immutable capability inventory for ``context``."""

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        """Execute ``request`` and return a normalized result."""
