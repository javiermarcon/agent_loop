"""Build ``AgentExecutionRequest`` values for READY WorkItems from a DAG plan.

Coordinates domain ``plan_work_items`` with the S16 builder. Does not dispatch
adapters, mutate WorkItems, or claim full orchestration (ADR-049 / ADR-048
PARTIAL).
"""

from collections.abc import Iterable

from agent_loop.application.execution_request_builder import ExecutionRequestOptions
from agent_loop.application.execution_request_builder import build_execution_request
from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.domain.work_item_plan import plan_work_items
from agent_loop.ports.agent_runtime import AgentExecutionRequest


def completed_ids_from_work_items(items: Iterable[WorkItem]) -> frozenset[str]:
    """Return ids whose ``WorkItemStatus`` is ``DONE`` (explicit completion only)."""
    return frozenset(item.work_item_id for item in items if item.status is WorkItemStatus.DONE)


def build_execution_requests_for_ready_work_items(
    items: Iterable[WorkItem],
    *,
    options: ExecutionRequestOptions,
) -> tuple[AgentExecutionRequest, ...]:
    """Plan ``items`` and build requests for each READY id in sorted order.

    BLOCKED and DONE items are omitted. Completion evidence for the builder is
    derived from DONE statuses in the same collection.
    """
    materialized = tuple(items)
    plan = plan_work_items(materialized)
    by_id = {item.work_item_id: item for item in materialized}
    completed_ids = completed_ids_from_work_items(materialized)
    return tuple(
        build_execution_request(
            by_id[work_item_id],
            completed_ids=completed_ids,
            options=options,
        )
        for work_item_id in plan.ready_ids
    )
