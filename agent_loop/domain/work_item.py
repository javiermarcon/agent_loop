"""Canonical control-plane WorkItem contract.

``WorkItem`` is the durable unit of work owned by the agent-loop control plane
(ADR-039). It carries identity, objective text, semantic lifecycle status, and
opaque correlation handles. It does not embed Notion clients, harness commands,
or MultiRunner ``RequirementUnit`` structure.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


def _require_nonempty_unpadded(field_name: str, value: object) -> str:
    """Validate ``value`` is a non-empty, unpadded ``str`` and return it."""
    if not isinstance(value, str):
        message = f"{field_name} must be a str"
        raise TypeError(message)
    if not value or not value.strip():
        message = f"{field_name} must be a non-empty string"
        raise ValueError(message)
    if value != value.strip():
        message = f"{field_name} must not include leading or trailing whitespace"
        raise ValueError(message)
    return value


def _require_tuple(field_name: str, value: object) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        message = f"{field_name} must be a tuple"
        raise TypeError(message)
    return value


class WorkItemStatus(StrEnum):
    """Semantic lifecycle of one control-plane work item.

    Project-specific provider status strings (for example Notion labels) belong
    in adapters/profiles, not in this enum.
    """

    OPEN = "open"
    BLOCKED = "blocked"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class WorkItemExternalRef:
    """Opaque external correlation handle.

    ``system`` and ``reference`` are declared identifiers only. Presence of a
    Notion (or other) ref does not imply API access, authorization, or sync.
    """

    system: str
    reference: str

    def __post_init__(self) -> None:
        _require_nonempty_unpadded("WorkItemExternalRef.system", self.system)
        _require_nonempty_unpadded("WorkItemExternalRef.reference", self.reference)


@dataclass(frozen=True, slots=True)
class WorkItem:
    """Immutable control-plane unit of work.

    ``work_item_id`` is the stable logical identity. ``objective`` is the
    human/runtime-facing description used for planning and execution handoff.
    ``depends_on`` lists other ``work_item_id`` values that must be ``DONE``
    before this item is schedulable. ``context_refs`` are opaque handles for
    later execution envelopes (not filesystem paths validated here).
    """

    work_item_id: str
    objective: str
    status: WorkItemStatus = WorkItemStatus.OPEN
    external_ref: WorkItemExternalRef | None = None
    depends_on: tuple[str, ...] = ()
    context_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty_unpadded("WorkItem.work_item_id", self.work_item_id)
        _require_nonempty_unpadded("WorkItem.objective", self.objective)
        if not isinstance(self.status, WorkItemStatus):
            message = "WorkItem.status must be a WorkItemStatus"
            raise TypeError(message)
        if self.external_ref is not None and not isinstance(
            self.external_ref,
            WorkItemExternalRef,
        ):
            message = "WorkItem.external_ref must be WorkItemExternalRef or None"
            raise TypeError(message)
        _validate_id_tuple(
            "WorkItem.depends_on",
            self.depends_on,
            self_id=self.work_item_id,
            allow_self=False,
            reject_duplicates=True,
        )
        # ADR-047: context_refs are opaque handles; uniqueness is not required.
        _validate_id_tuple(
            "WorkItem.context_refs",
            self.context_refs,
            self_id=None,
            allow_self=True,
            reject_duplicates=False,
        )


def _validate_id_tuple(
    field_name: str,
    values: object,
    *,
    self_id: str | None,
    allow_self: bool,
    reject_duplicates: bool,
) -> None:
    items = _require_tuple(field_name, values)
    seen: set[str] = set()
    for value in items:
        identifier = _require_nonempty_unpadded(field_name, value)
        if not allow_self and self_id is not None and identifier == self_id:
            message = f"{field_name} must not include the work item's own id (self)"
            raise ValueError(message)
        if reject_duplicates and identifier in seen:
            message = f"{field_name} contains duplicate id: {identifier}"
            raise ValueError(message)
        seen.add(identifier)


def _completed_id_set(completed_ids: object) -> frozenset[str]:
    """Materialize ``completed_ids`` as validated work-item identifiers.

    ``str`` / ``bytes`` are rejected as whole containers so a single id string is
    never treated as a character iterable. Each element must be a non-empty,
    unpadded ``str`` (same identifier rules as ``WorkItem.work_item_id``).
    """
    if isinstance(completed_ids, (str, bytes)):
        message = "completed_ids must be an iterable of str ids, not str or bytes"
        raise TypeError(message)
    if not isinstance(completed_ids, Iterable):
        message = "completed_ids must be an iterable of str ids"
        raise TypeError(message)
    return frozenset(_require_nonempty_unpadded("completed_ids", completed_id) for completed_id in completed_ids)


def is_schedulable(work_item: WorkItem, *, completed_ids: Iterable[str] | frozenset[str]) -> bool:
    """Return whether ``work_item`` is OPEN and all dependencies are completed.

    ``completed_ids`` may be any iterable of already-``DONE`` work-item ids
    (list, tuple, set, frozenset, or iterator). BLOCKED and DONE items are never
    schedulable. This helper does not consult runtimes or external systems.
    """
    if work_item.status is not WorkItemStatus.OPEN:
        return False
    completed = _completed_id_set(completed_ids)
    return all(dep_id in completed for dep_id in work_item.depends_on)


def execution_task_text(work_item: WorkItem) -> str:
    """Return objective text suitable for an execution request envelope.

    Only ``OPEN`` items may be handed off. The returned string carries no
    harness/CLI details; adapters remain responsible for runtime invocation.
    """
    if work_item.status is not WorkItemStatus.OPEN:
        message = "execution_task_text requires WorkItemStatus.OPEN"
        raise ValueError(message)
    return work_item.objective
