"""Build ``AgentExecutionRequest`` only from schedulable ``WorkItem`` values.

This application helper is the control-plane gate between domain readiness
(``is_schedulable`` / ``execution_task_text``) and the AgentRuntime port. It
does not invoke adapters, rewrite planners, or change ``TaskAssessment``.

Annotations are evaluated at definition time (no postponed evaluation) so
``typing.get_type_hints`` resolves ``ExecutionRequestOptions`` and
``build_execution_request`` from the module namespace. Domain/port imports do
not form a cycle (application → domain/ports; neither imports application).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from agent_loop.domain.capabilities import CapabilityRequirement
from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import execution_task_text
from agent_loop.domain.work_item import is_schedulable
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy


@dataclass(frozen=True, slots=True)
class ExecutionRequestOptions:
    """Port-side fields supplied when constructing an execution request.

    Capability requirements remain an explicit input (typically from
    ``TaskAssessment``) and are never inferred from ``WorkItem`` fields.
    """

    strategy: ExecutionStrategy
    working_directory: str
    mutation_policy: MutationPolicy
    permission_policy: PermissionPolicy
    required_capabilities: tuple[CapabilityRequirement, ...] = ()
    model: str | None = None
    structured_output_schema: str | None = None
    session_ref: str | None = None


def build_execution_request(
    work_item: WorkItem,
    *,
    completed_ids: Iterable[str] | frozenset[str],
    options: ExecutionRequestOptions,
) -> AgentExecutionRequest:
    """Return an execution request for a schedulable work item only.

    ``completed_ids`` must be an explicit set of already-``DONE`` work-item
    identifiers. Missing completion evidence fails closed via
    ``is_schedulable``.
    """
    if not is_schedulable(work_item, completed_ids=completed_ids):
        message = (
            f"WorkItem {work_item.work_item_id!r} is not schedulable; refusing AgentExecutionRequest construction"
        )
        raise ValueError(message)
    return AgentExecutionRequest(
        task=execution_task_text(work_item),
        strategy=options.strategy,
        model=options.model,
        required_capabilities=options.required_capabilities,
        working_directory=options.working_directory,
        mutation_policy=options.mutation_policy,
        permission_policy=options.permission_policy,
        structured_output_schema=options.structured_output_schema,
        session_ref=options.session_ref,
        context_refs=work_item.context_refs,
    )
