"""Deterministic WorkItem DAG validation and readiness planning.

Plans a finite set of ``WorkItem`` values as a DAG: validates structure,
classifies each node as READY / BLOCKED / DONE with explicit reasons, and
never mutates inputs or invokes runtimes. Completion evidence is solely
``WorkItemStatus.DONE`` within the same plan (ADR-049).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.domain.work_item import is_schedulable

if TYPE_CHECKING:
    from collections.abc import Iterable

_COLOR_WHITE = 0
_COLOR_GRAY = 1
_COLOR_BLACK = 2


class InvalidWorkItemDagError(ValueError):
    """Raised when a WorkItem collection is not a valid dependency DAG."""


class PlanNodeState(StrEnum):
    """Planning disposition for one WorkItem in a validated DAG."""

    READY = "ready"
    BLOCKED = "blocked"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class WorkItemPlanEntry:
    """Immutable planning outcome for one WorkItem.

    ``pending_dependency_ids`` lists unmet dependency ids in sorted order.
    ``reason`` is a deterministic, data-derived explanation (not LLM text).
    """

    work_item_id: str
    state: PlanNodeState
    pending_dependency_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class WorkItemPlan:
    """Immutable plan over a validated WorkItem DAG.

    ``entries``, ``ready_ids``, ``blocked_ids``, and ``done_ids`` are sorted by
    ``work_item_id``. Equal inputs always yield equal plans regardless of
    iteration order.
    """

    entries: tuple[WorkItemPlanEntry, ...]
    ready_ids: tuple[str, ...]
    blocked_ids: tuple[str, ...]
    done_ids: tuple[str, ...]


def _index_work_items(items: Iterable[WorkItem]) -> dict[str, WorkItem]:
    indexed: dict[str, WorkItem] = {}
    for item in items:
        work_item_id = item.work_item_id
        if work_item_id in indexed:
            message = f"duplicate work_item_id: {work_item_id}"
            raise InvalidWorkItemDagError(message)
        indexed[work_item_id] = item
    return indexed


def _validate_dependency_refs(by_id: dict[str, WorkItem]) -> None:
    for work_item_id, item in sorted(by_id.items()):
        for dependency_id in item.depends_on:
            if dependency_id == work_item_id:
                message = f"self-dependency: {work_item_id}"
                raise InvalidWorkItemDagError(message)
            if dependency_id not in by_id:
                message = f"missing dependency: {dependency_id} referenced by {work_item_id}"
                raise InvalidWorkItemDagError(message)


def _find_cycle(by_id: dict[str, WorkItem]) -> tuple[str, ...] | None:
    """Return one deterministic cycle path (``a -> … -> a``) or ``None``.

    Uses an explicit-stack DFS (not CPython call recursion). Visit order is
    sorted by ``work_item_id`` and sorted ``depends_on``, matching the former
    recursive algorithm. Adjacency lists are sorted once per node before the
    walk. There is no product-level WorkItem depth/count cap; stack depth is
    bounded by graph size on the heap, not ``sys.getrecursionlimit()``.
    """
    adjacency = {work_item_id: tuple(sorted(item.depends_on)) for work_item_id, item in by_id.items()}
    color = dict.fromkeys(by_id, _COLOR_WHITE)
    for start_id in sorted(by_id):
        if color[start_id] != _COLOR_WHITE:
            continue
        # Explicit stack frames carry the node id and the next dependency index.
        frames: list[tuple[str, int]] = [(start_id, 0)]
        path: list[str] = [start_id]
        color[start_id] = _COLOR_GRAY
        while frames:
            node_id, dep_index = frames[-1]
            dependencies = adjacency[node_id]
            if dep_index < len(dependencies):
                frames[-1] = (node_id, dep_index + 1)
                dependency_id = dependencies[dep_index]
                dep_color = color[dependency_id]
                if dep_color == _COLOR_GRAY:
                    cycle_start = path.index(dependency_id)
                    return (*path[cycle_start:], dependency_id)
                if dep_color == _COLOR_WHITE:
                    color[dependency_id] = _COLOR_GRAY
                    path.append(dependency_id)
                    frames.append((dependency_id, 0))
                continue
            frames.pop()
            path.pop()
            color[node_id] = _COLOR_BLACK
    return None


def validate_work_item_dag(items: Iterable[WorkItem]) -> dict[str, WorkItem]:
    """Validate ``items`` as a DAG and return an id→item index.

    Rejects duplicate ids, missing dependency targets, self-dependencies, and
    cycles (direct or indirect). Does not classify readiness.
    """
    by_id = _index_work_items(items)
    _validate_dependency_refs(by_id)
    cycle = _find_cycle(by_id)
    if cycle is not None:
        message = f"cycle detected: {' -> '.join(cycle)}"
        raise InvalidWorkItemDagError(message)
    return by_id


def _pending_dependency_ids(
    item: WorkItem,
    *,
    completed_ids: frozenset[str],
) -> tuple[str, ...]:
    return tuple(sorted(dep_id for dep_id in item.depends_on if dep_id not in completed_ids))


def _classify_item(
    item: WorkItem,
    *,
    completed_ids: frozenset[str],
) -> WorkItemPlanEntry:
    work_item_id = item.work_item_id
    pending = _pending_dependency_ids(item, completed_ids=completed_ids)
    if item.status is WorkItemStatus.DONE:
        return WorkItemPlanEntry(
            work_item_id=work_item_id,
            state=PlanNodeState.DONE,
            pending_dependency_ids=(),
            reason="DONE: WorkItem status is DONE",
        )
    if item.status is WorkItemStatus.BLOCKED:
        return WorkItemPlanEntry(
            work_item_id=work_item_id,
            state=PlanNodeState.BLOCKED,
            pending_dependency_ids=pending,
            reason="BLOCKED: WorkItem status is BLOCKED",
        )
    if item.status is WorkItemStatus.OPEN:
        if pending:
            pending_text = ", ".join(pending)
            return WorkItemPlanEntry(
                work_item_id=work_item_id,
                state=PlanNodeState.BLOCKED,
                pending_dependency_ids=pending,
                reason=f"BLOCKED: pending dependencies: {pending_text}",
            )
        if not is_schedulable(item, completed_ids=completed_ids):
            message = f"internal planning error: OPEN item {work_item_id!r} failed is_schedulable"
            raise RuntimeError(message)
        return WorkItemPlanEntry(
            work_item_id=work_item_id,
            state=PlanNodeState.READY,
            pending_dependency_ids=(),
            reason="READY: OPEN with all dependencies completed",
        )
    message = f"unsupported WorkItemStatus: {item.status!r}"
    raise TypeError(message)


def plan_work_items(items: Iterable[WorkItem]) -> WorkItemPlan:
    """Validate ``items`` and return a deterministic readiness plan.

    Completion evidence is the set of ``work_item_id`` values whose status is
    ``DONE`` in this collection. Does not mutate items, call adapters, or
    construct execution requests.
    """
    by_id = validate_work_item_dag(items)
    completed_ids = frozenset(
        work_item_id for work_item_id, item in by_id.items() if item.status is WorkItemStatus.DONE
    )
    entries = tuple(_classify_item(by_id[work_item_id], completed_ids=completed_ids) for work_item_id in sorted(by_id))
    ready_ids = tuple(entry.work_item_id for entry in entries if entry.state is PlanNodeState.READY)
    blocked_ids = tuple(entry.work_item_id for entry in entries if entry.state is PlanNodeState.BLOCKED)
    done_ids = tuple(entry.work_item_id for entry in entries if entry.state is PlanNodeState.DONE)
    return WorkItemPlan(
        entries=entries,
        ready_ids=ready_ids,
        blocked_ids=blocked_ids,
        done_ids=done_ids,
    )
