"""Application bridge: READY WorkItems from a plan → execution requests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_loop.application.execution_request_builder import ExecutionRequestOptions
from agent_loop.application.execution_request_builder import build_execution_request
from agent_loop.application.planned_execution_requests import build_execution_requests_for_ready_work_items
from agent_loop.application.planned_execution_requests import completed_ids_from_work_items
from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.domain.work_item_plan import InvalidWorkItemDagError
from agent_loop.domain.work_item_plan import PlanNodeState
from agent_loop.domain.work_item_plan import plan_work_items
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy

_APP_MODULE = Path(__file__).resolve().parents[3] / "agent_loop" / "application" / "planned_execution_requests.py"
_WORKSPACE = "/workspace"


def _wi(
    work_item_id: str,
    *,
    status: WorkItemStatus = WorkItemStatus.OPEN,
    depends_on: tuple[str, ...] = (),
) -> WorkItem:
    return WorkItem(
        work_item_id=work_item_id,
        objective=f"Do {work_item_id}",
        status=status,
        depends_on=depends_on,
    )


def _options() -> ExecutionRequestOptions:
    return ExecutionRequestOptions(
        strategy=ExecutionStrategy.BUILD,
        working_directory=_WORKSPACE,
        mutation_policy=MutationPolicy(allow_workspace_mutation=False),
        permission_policy=PermissionPolicy(allow_network=False),
    )


def test_completed_ids_come_only_from_done_status() -> None:
    """Completion evidence is explicit DONE status, not requests or inference."""
    items = (
        _wi("open"),
        _wi("blocked", status=WorkItemStatus.BLOCKED),
        _wi("done", status=WorkItemStatus.DONE),
    )
    assert completed_ids_from_work_items(items) == frozenset({"done"})


def test_ready_item_builds_via_existing_builder() -> None:
    """A planned READY item can construct an AgentExecutionRequest via the builder."""
    items = (_wi("a"),)
    plan = plan_work_items(items)
    assert plan.ready_ids == ("a",)
    request = build_execution_request(
        items[0],
        completed_ids=completed_ids_from_work_items(items),
        options=_options(),
    )
    assert request.task == "Do a"
    assert request.working_directory == _WORKSPACE


def test_blocked_item_cannot_build_executable_request() -> None:
    """A planned BLOCKED item is refused by the builder gate."""
    items = (_wi("dep"), _wi("task", depends_on=("dep",)))
    plan = plan_work_items(items)
    task_entry = next(entry for entry in plan.entries if entry.work_item_id == "task")
    assert task_entry.state is PlanNodeState.BLOCKED
    task = next(item for item in items if item.work_item_id == "task")
    with pytest.raises(ValueError, match="not schedulable"):
        build_execution_request(
            task,
            completed_ids=completed_ids_from_work_items(items),
            options=_options(),
        )


def test_bridge_builds_only_ready_requests_in_deterministic_order() -> None:
    """Application bridge emits one request per READY id, sorted, without side effects."""
    items = (
        _wi("c", depends_on=("a",)),
        _wi("b", status=WorkItemStatus.DONE),
        _wi("a"),
        _wi("d", depends_on=("b",)),
    )
    requests = build_execution_requests_for_ready_work_items(items, options=_options())
    assert tuple(request.task for request in requests) == ("Do a", "Do d")


def test_bridge_does_not_import_adapters() -> None:
    """Planning→builder bridge stays free of adapter/script execution coupling."""
    tree = ast.parse(_APP_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for module in imported:
        assert not module.startswith("agent_loop.adapters")
        assert not module.startswith("scripts")
        assert module != "scripts"


def test_bridge_returns_empty_for_empty_plan() -> None:
    """No WorkItems → no execution requests."""
    assert build_execution_requests_for_ready_work_items((), options=_options()) == ()


def test_bridge_omits_done_and_blocked_items() -> None:
    """DONE and semantically BLOCKED items never produce requests."""
    items = (
        _wi("done", status=WorkItemStatus.DONE),
        _wi("blocked", status=WorkItemStatus.BLOCKED),
        _wi("ready"),
    )
    requests = build_execution_requests_for_ready_work_items(items, options=_options())
    assert tuple(request.task for request in requests) == ("Do ready",)


def test_bridge_propagates_invalid_dag_errors() -> None:
    """Invalid graphs fail before any request construction."""
    with pytest.raises(InvalidWorkItemDagError, match="cycle"):
        build_execution_requests_for_ready_work_items(
            (
                _wi("a", depends_on=("b",)),
                _wi("b", depends_on=("a",)),
            ),
            options=_options(),
        )


def test_bridge_does_not_treat_open_dep_as_completed() -> None:
    """Abuse: OPEN dependency must not unlock a dependent request."""
    items = (_wi("dep"), _wi("task", depends_on=("dep",)))
    requests = build_execution_requests_for_ready_work_items(items, options=_options())
    assert tuple(request.task for request in requests) == ("Do dep",)


def test_done_item_cannot_build_via_builder_even_if_listed() -> None:
    """DONE WorkItems remain non-schedulable at the builder gate."""
    items = (_wi("done", status=WorkItemStatus.DONE),)
    with pytest.raises(ValueError, match="not schedulable"):
        build_execution_request(
            items[0],
            completed_ids=completed_ids_from_work_items(items),
            options=_options(),
        )
