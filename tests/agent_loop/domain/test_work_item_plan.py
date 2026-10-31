"""Domain tests for deterministic WorkItem DAG planning (S17)."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

import agent_loop.domain.work_item_plan as work_item_plan_mod
from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.domain.work_item_plan import InvalidWorkItemDagError
from agent_loop.domain.work_item_plan import PlanNodeState
from agent_loop.domain.work_item_plan import plan_work_items
from agent_loop.domain.work_item_plan import validate_work_item_dag

_DOMAIN_PLAN = Path(__file__).resolve().parents[3] / "agent_loop" / "domain" / "work_item_plan.py"


def _wi(
    work_item_id: str,
    *,
    status: WorkItemStatus = WorkItemStatus.OPEN,
    depends_on: tuple[str, ...] = (),
    objective: str | None = None,
) -> WorkItem:
    return WorkItem(
        work_item_id=work_item_id,
        objective=objective if objective is not None else f"Objective for {work_item_id}",
        status=status,
        depends_on=depends_on,
    )


def test_single_open_item_without_deps_is_ready() -> None:
    """An OPEN WorkItem with no dependencies is READY."""
    plan = plan_work_items([_wi("a")])
    assert plan.ready_ids == ("a",)
    assert plan.blocked_ids == ()
    assert plan.done_ids == ()
    entry = plan.entries[0]
    assert entry.state is PlanNodeState.READY
    assert entry.pending_dependency_ids == ()
    assert "READY" in entry.reason or "ready" in entry.reason.lower()


def test_completed_dependency_makes_dependent_ready() -> None:
    """A DONE dependency allows an OPEN dependent to become READY."""
    plan = plan_work_items(
        [
            _wi("dep", status=WorkItemStatus.DONE),
            _wi("task", depends_on=("dep",)),
        ],
    )
    assert plan.ready_ids == ("task",)
    assert plan.done_ids == ("dep",)
    assert plan.blocked_ids == ()


def test_pending_dependency_keeps_dependent_blocked() -> None:
    """An unmet OPEN dependency keeps the dependent BLOCKED in the plan."""
    plan = plan_work_items(
        [
            _wi("dep"),
            _wi("task", depends_on=("dep",)),
        ],
    )
    assert plan.ready_ids == ("dep",)
    assert plan.blocked_ids == ("task",)
    task = next(entry for entry in plan.entries if entry.work_item_id == "task")
    assert task.state is PlanNodeState.BLOCKED
    assert task.pending_dependency_ids == ("dep",)


def test_any_pending_dependency_keeps_blocked() -> None:
    """One unmet dependency among several is enough to keep BLOCKED."""
    plan = plan_work_items(
        [
            _wi("a", status=WorkItemStatus.DONE),
            _wi("b"),
            _wi("task", depends_on=("a", "b")),
        ],
    )
    assert plan.blocked_ids == ("task",)
    task = next(entry for entry in plan.entries if entry.work_item_id == "task")
    assert task.pending_dependency_ids == ("b",)


def test_shared_dependency_unblocks_multiple_dependents() -> None:
    """A shared DONE dependency unblocks every OPEN dependent."""
    plan = plan_work_items(
        [
            _wi("shared", status=WorkItemStatus.DONE),
            _wi("left", depends_on=("shared",)),
            _wi("right", depends_on=("shared",)),
        ],
    )
    assert plan.ready_ids == ("left", "right")
    assert plan.done_ids == ("shared",)


def test_multi_level_dependency_chain() -> None:
    """Only the frontier of a chain is READY; deeper nodes stay BLOCKED."""
    plan = plan_work_items(
        [
            _wi("a"),
            _wi("b", depends_on=("a",)),
            _wi("c", depends_on=("b",)),
        ],
    )
    assert plan.ready_ids == ("a",)
    assert plan.blocked_ids == ("b", "c")
    c_entry = next(entry for entry in plan.entries if entry.work_item_id == "c")
    assert c_entry.pending_dependency_ids == ("b",)


def test_independent_open_items_are_both_ready() -> None:
    """Independent OPEN items are all READY."""
    plan = plan_work_items([_wi("x"), _wi("y")])
    assert plan.ready_ids == ("x", "y")
    assert plan.blocked_ids == ()


def test_done_item_is_not_ready() -> None:
    """DONE items are reported as DONE, never READY."""
    plan = plan_work_items([_wi("done", status=WorkItemStatus.DONE)])
    assert plan.done_ids == ("done",)
    assert plan.ready_ids == ()
    assert plan.entries[0].state is PlanNodeState.DONE


def test_status_blocked_is_not_ready_even_with_met_deps() -> None:
    """Semantic BLOCKED status never becomes READY by inference."""
    plan = plan_work_items(
        [
            _wi("dep", status=WorkItemStatus.DONE),
            _wi("task", status=WorkItemStatus.BLOCKED, depends_on=("dep",)),
        ],
    )
    assert plan.ready_ids == ()
    assert plan.blocked_ids == ("task",)
    task = next(entry for entry in plan.entries if entry.work_item_id == "task")
    assert task.state is PlanNodeState.BLOCKED
    assert "BLOCKED" in task.reason or "blocked" in task.reason.lower()


def test_missing_dependency_rejected() -> None:
    """References to ids absent from the plan fail closed."""
    with pytest.raises(InvalidWorkItemDagError, match="missing dependency"):
        plan_work_items([_wi("task", depends_on=("ghost",))])


def test_self_dependency_rejected_at_construction() -> None:
    """Autodependency is rejected by the WorkItem contract itself."""
    with pytest.raises(ValueError, match="self"):
        _wi("a", depends_on=("a",))


def test_direct_cycle_rejected() -> None:
    """A direct cycle A↔B is rejected explicitly."""
    with pytest.raises(InvalidWorkItemDagError, match="cycle"):
        plan_work_items(
            [
                _wi("a", depends_on=("b",)),
                _wi("b", depends_on=("a",)),
            ],
        )


def test_indirect_cycle_rejected() -> None:
    """An indirect cycle A→B→C→A is rejected explicitly."""
    with pytest.raises(InvalidWorkItemDagError, match="cycle"):
        plan_work_items(
            [
                _wi("a", depends_on=("c",)),
                _wi("b", depends_on=("a",)),
                _wi("c", depends_on=("b",)),
            ],
        )


def test_duplicate_ids_rejected() -> None:
    """Duplicate work_item_id values in one plan are rejected."""
    with pytest.raises(InvalidWorkItemDagError, match="duplicate"):
        plan_work_items([_wi("a"), replace(_wi("a"), objective="Other objective")])


def test_empty_dag_is_valid() -> None:
    """An empty item collection yields an empty deterministic plan."""
    plan = plan_work_items([])
    assert plan.entries == ()
    assert plan.ready_ids == ()
    assert plan.blocked_ids == ()
    assert plan.done_ids == ()


def test_plan_is_deterministic_across_input_orders() -> None:
    """Input order must not change plan entries, ids, or reasons."""
    items_a = (
        _wi("c", depends_on=("a",)),
        _wi("b", status=WorkItemStatus.DONE),
        _wi("a"),
        _wi("d", depends_on=("b",)),
    )
    items_b = tuple(reversed(items_a))
    plan_a = plan_work_items(items_a)
    plan_b = plan_work_items(items_b)
    assert plan_a == plan_b
    assert plan_a.ready_ids == ("a", "d")
    assert plan_a.blocked_ids == ("c",)
    assert plan_a.done_ids == ("b",)


def test_pending_dependency_traceability_is_sorted() -> None:
    """Pending dependency ids are stable and sorted for explanation."""
    plan = plan_work_items(
        [
            _wi("z"),
            _wi("m"),
            _wi("task", depends_on=("z", "m")),
        ],
    )
    task = next(entry for entry in plan.entries if entry.work_item_id == "task")
    assert task.pending_dependency_ids == ("m", "z")
    assert "m" in task.reason
    assert "z" in task.reason


def test_planner_does_not_mutate_work_items() -> None:
    """Planning is pure: WorkItem values and their fields stay unchanged."""
    original = (
        _wi("a"),
        _wi("b", depends_on=("a",)),
    )
    snapshot = tuple((item.work_item_id, item.status, item.depends_on, item.objective) for item in original)
    plan_work_items(original)
    assert tuple((item.work_item_id, item.status, item.depends_on, item.objective) for item in original) == snapshot


def test_planner_module_does_not_import_adapters_or_scripts() -> None:
    """Domain planner stays free of adapter/script coupling."""
    tree = ast.parse(_DOMAIN_PLAN.read_text(encoding="utf-8"))
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


def _bypass_work_item(
    *,
    work_item_id: str,
    objective: str = "bypass",
    status: object = WorkItemStatus.OPEN,
    depends_on: tuple[str, ...] = (),
) -> WorkItem:
    """Construct a WorkItem without ``__post_init__`` (abuse / defense tests only)."""
    item = object.__new__(WorkItem)
    object.__setattr__(item, "work_item_id", work_item_id)
    object.__setattr__(item, "objective", objective)
    object.__setattr__(item, "status", status)
    object.__setattr__(item, "external_ref", None)
    object.__setattr__(item, "depends_on", depends_on)
    object.__setattr__(item, "context_refs", ())
    return item


def test_validate_work_item_dag_returns_index_for_valid_graph() -> None:
    """Public validator indexes items without classifying readiness."""
    items = (_wi("b", depends_on=("a",)), _wi("a"))
    indexed = validate_work_item_dag(items)
    assert set(indexed) == {"a", "b"}
    assert indexed["a"].work_item_id == "a"


def test_planner_rejects_self_dependency_when_work_item_guard_bypassed() -> None:
    """Defense in depth: planner still rejects self-deps if construction is bypassed."""
    with pytest.raises(InvalidWorkItemDagError, match="self-dependency"):
        plan_work_items([_bypass_work_item(work_item_id="a", depends_on=("a",))])


def test_planner_fails_closed_when_is_schedulable_disagrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If OPEN+no-pending somehow fails ``is_schedulable``, planning raises RuntimeError."""

    def _never_schedulable(work_item: WorkItem, *, completed_ids: object) -> bool:
        del work_item, completed_ids
        return False

    monkeypatch.setattr(work_item_plan_mod, "is_schedulable", _never_schedulable)
    with pytest.raises(RuntimeError, match="internal planning error"):
        plan_work_items([_wi("a")])


def test_planner_rejects_unsupported_status_object() -> None:
    """Forged non-enum status values are rejected explicitly."""
    with pytest.raises(TypeError, match="unsupported WorkItemStatus"):
        plan_work_items([_bypass_work_item(work_item_id="a", status="forged-open")])


def test_done_item_stays_done_even_if_dependency_still_open() -> None:
    """DONE is terminal in the plan; unmet deps do not re-open it."""
    plan = plan_work_items(
        [
            _wi("dep"),
            _wi("finished", status=WorkItemStatus.DONE, depends_on=("dep",)),
        ],
    )
    assert plan.done_ids == ("finished",)
    assert plan.ready_ids == ("dep",)
    finished = next(entry for entry in plan.entries if entry.work_item_id == "finished")
    assert finished.state is PlanNodeState.DONE
    assert finished.pending_dependency_ids == ()


def test_status_blocked_reports_pending_deps_for_traceability() -> None:
    """Semantic BLOCKED still surfaces unmet dependency ids for explanation."""
    plan = plan_work_items(
        [
            _wi("dep"),
            _wi("task", status=WorkItemStatus.BLOCKED, depends_on=("dep",)),
        ],
    )
    task = next(entry for entry in plan.entries if entry.work_item_id == "task")
    assert task.state is PlanNodeState.BLOCKED
    assert task.pending_dependency_ids == ("dep",)
    assert "BLOCKED" in task.reason


def test_diamond_dependency_graph() -> None:
    """Shared mid-nodes in a diamond do not create cycles or unstable order."""
    plan = plan_work_items(
        [
            _wi("root", status=WorkItemStatus.DONE),
            _wi("left", depends_on=("root",)),
            _wi("right", depends_on=("root",)),
            _wi("tip", depends_on=("left", "right")),
        ],
    )
    assert plan.ready_ids == ("left", "right")
    assert plan.blocked_ids == ("tip",)
    tip = next(entry for entry in plan.entries if entry.work_item_id == "tip")
    assert tip.pending_dependency_ids == ("left", "right")


def test_hub_with_many_unsorted_dependencies_plans_deterministically() -> None:
    """A hub with many direct deps keeps sorted ready/pending order (acyclic).

    Dependency ids are supplied in reverse lexical order so the planner must
    normalize them; the graph stays valid and cycle-free.
    """
    dep_count = 40
    unsorted_dep_ids = tuple(f"d{index:02d}" for index in range(dep_count, 0, -1))
    expected_sorted_deps = tuple(sorted(unsorted_dep_ids))
    items = [_wi(dep_id) for dep_id in unsorted_dep_ids]
    items.append(_wi("hub", depends_on=unsorted_dep_ids))
    plan_a = plan_work_items(items)
    plan_b = plan_work_items(tuple(reversed(items)))
    assert plan_a == plan_b
    assert plan_a.ready_ids == expected_sorted_deps
    assert plan_a.blocked_ids == ("hub",)
    hub = next(entry for entry in plan_a.entries if entry.work_item_id == "hub")
    assert hub.state is PlanNodeState.BLOCKED
    assert hub.pending_dependency_ids == expected_sorted_deps


def test_cycle_error_message_includes_deterministic_path() -> None:
    """Cycle errors expose an explicit path string for operators."""
    with pytest.raises(InvalidWorkItemDagError, match=r"cycle detected: a -> b -> a") as exc_info:
        plan_work_items(
            [
                _wi("a", depends_on=("b",)),
                _wi("b", depends_on=("a",)),
            ],
        )
    assert "->" in str(exc_info.value)


def test_request_construction_or_partial_progress_is_not_completion_evidence() -> None:
    """OPEN deps remain unmet even when other items look 'in progress'."""
    plan = plan_work_items(
        [
            _wi("dep"),  # OPEN — not DONE
            _wi("consumer", depends_on=("dep",)),
        ],
    )
    assert plan.blocked_ids == ("consumer",)
    consumer = next(entry for entry in plan.entries if entry.work_item_id == "consumer")
    assert consumer.pending_dependency_ids == ("dep",)


_DEEP_CHAIN_LENGTH = 1500


def test_deep_acyclic_chain_plans_without_call_recursion_limit() -> None:
    """Long dependency chains must not hit CPython ``sys.getrecursionlimit()``."""
    items = [
        _wi(
            f"n{index:04d}",
            depends_on=() if index == 0 else (f"n{index - 1:04d}",),
        )
        for index in range(_DEEP_CHAIN_LENGTH)
    ]
    plan = plan_work_items(items)
    assert plan.ready_ids == ("n0000",)
    assert len(plan.blocked_ids) == _DEEP_CHAIN_LENGTH - 1
    assert plan.done_ids == ()


def test_deep_cycle_detected_without_call_recursion_limit() -> None:
    """Cycles closed at depth beyond typical recursion limits are still rejected."""
    items = [
        _wi(
            f"n{index:04d}",
            depends_on=(f"n{(index + 1) % _DEEP_CHAIN_LENGTH:04d}",),
        )
        for index in range(_DEEP_CHAIN_LENGTH)
    ]
    with pytest.raises(InvalidWorkItemDagError, match="cycle detected"):
        plan_work_items(items)


def test_cycle_at_moderate_depth_keeps_explicit_path_message() -> None:
    """Iterative DFS preserves deterministic cycle path formatting."""
    depth = 40
    items = [
        _wi(
            f"n{index:02d}",
            depends_on=(f"n{(index + 1) % depth:02d}",),
        )
        for index in range(depth)
    ]
    with pytest.raises(InvalidWorkItemDagError, match=r"cycle detected: n00 -> n01") as exc_info:
        plan_work_items(items)
    message = str(exc_info.value)
    assert message.startswith("cycle detected: ")
    assert message.endswith("-> n00")
    assert message.count("->") == depth
