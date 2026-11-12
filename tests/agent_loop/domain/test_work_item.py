"""Contract tests for the domain WorkItem value object."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from typing import get_type_hints

import pytest

from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import WorkItemExternalRef
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.domain.work_item import execution_task_text
from agent_loop.domain.work_item import is_schedulable

if TYPE_CHECKING:
    from collections.abc import Callable


def _item() -> WorkItem:
    return WorkItem(
        work_item_id="wi-001",
        objective="Implement WorkItem contract",
    )


def test_work_item_valid_construction() -> None:
    """A work item carries stable identity and objective without runtime coupling."""
    item = replace(
        _item(),
        external_ref=WorkItemExternalRef(system="notion", reference="page-abc"),
        depends_on=("wi-000",),
        context_refs=("ctx://repo",),
    )
    assert item.work_item_id == "wi-001"
    assert item.objective == "Implement WorkItem contract"
    assert item.status is WorkItemStatus.OPEN
    assert item.external_ref is not None
    assert item.external_ref.system == "notion"
    assert item.depends_on == ("wi-000",)


@pytest.mark.parametrize(
    "builder",
    [
        lambda: replace(_item(), work_item_id=""),
        lambda: replace(_item(), work_item_id="   "),
        lambda: replace(_item(), objective=""),
        lambda: replace(_item(), objective="   "),
    ],
)
def test_work_item_rejects_blank_required_fields(
    builder: Callable[[], WorkItem],
) -> None:
    """Blank identity or objective fail closed."""
    with pytest.raises(ValueError, match=r"work_item_id|objective"):
        builder()


@pytest.mark.parametrize(
    "builder",
    [
        lambda: replace(_item(), work_item_id=" wi-001"),
        lambda: replace(_item(), work_item_id="wi-001 "),
        lambda: replace(_item(), objective=" do work"),
        lambda: replace(_item(), objective="do work "),
    ],
)
def test_work_item_rejects_padded_required_fields(
    builder: Callable[[], WorkItem],
) -> None:
    """Required strings are never silently trimmed."""
    with pytest.raises(ValueError, match="leading or trailing"):
        builder()


def test_external_ref_rejects_blank_or_padded_parts() -> None:
    """External refs stay opaque but still reject empty/padded components."""
    with pytest.raises(ValueError, match="system"):
        WorkItemExternalRef(system="", reference="x")
    with pytest.raises(ValueError, match="reference"):
        WorkItemExternalRef(system="notion", reference=" page ")


def test_depends_on_rejects_blank_padded_or_self_reference() -> None:
    """Dependency ids follow the same identity rules and cannot include self."""
    with pytest.raises(ValueError, match="depends_on"):
        replace(_item(), depends_on=("",))
    with pytest.raises(ValueError, match="depends_on"):
        replace(_item(), depends_on=(" wi-000",))
    with pytest.raises(ValueError, match="self"):
        replace(_item(), depends_on=("wi-001",))


def test_depends_on_rejects_duplicates() -> None:
    """Duplicate dependency ids are an explicit contract violation."""
    with pytest.raises(ValueError, match="duplicate"):
        replace(_item(), depends_on=("wi-000", "wi-000"))


def test_work_item_is_immutable() -> None:
    """WorkItem rejects attribute mutation."""
    item = _item()
    with pytest.raises(AttributeError):
        item.objective = "mutated"  # type: ignore[misc]


def test_equality_is_structural() -> None:
    """Frozen equality compares fields; identity alone is not enough."""
    left = _item()
    same = _item()
    other = replace(_item(), objective="Different objective")
    assert left == same
    assert hash(left) == hash(same)
    assert left != other


def test_absent_optional_fields_have_explicit_defaults() -> None:
    """Optional absences are None/empty tuples, not sentinel strings."""
    item = _item()
    assert item.external_ref is None
    assert item.depends_on == ()
    assert item.context_refs == ()


def test_is_schedulable_requires_open_and_satisfied_dependencies() -> None:
    """Planner readiness uses status plus completed dependency ids."""
    item = replace(_item(), depends_on=("wi-000", "wi-00a"))
    assert is_schedulable(item, completed_ids=frozenset({"wi-000", "wi-00a"})) is True
    assert is_schedulable(item, completed_ids=frozenset({"wi-000"})) is False
    blocked = replace(_item(), status=WorkItemStatus.BLOCKED)
    assert is_schedulable(blocked, completed_ids=frozenset()) is False
    done = replace(_item(), status=WorkItemStatus.DONE)
    assert is_schedulable(done, completed_ids=frozenset()) is False


def test_execution_task_text_uses_objective_for_open_items() -> None:
    """Execution handoff uses objective text without embedding runtime details."""
    item = replace(_item(), objective="Ship WorkItem domain contract")
    assert execution_task_text(item) == "Ship WorkItem domain contract"


def test_execution_task_text_rejects_non_open_items() -> None:
    """DONE/BLOCKED items are not handed to execution as runnable tasks."""
    with pytest.raises(ValueError, match="OPEN"):
        execution_task_text(replace(_item(), status=WorkItemStatus.DONE))
    with pytest.raises(ValueError, match="OPEN"):
        execution_task_text(replace(_item(), status=WorkItemStatus.BLOCKED))


def test_work_item_id_rejects_non_string_types() -> None:
    """work_item_id must be a str at runtime, not merely annotated as one."""
    with pytest.raises(TypeError, match="work_item_id"):
        WorkItem(work_item_id=123, objective="ok")  # type: ignore[arg-type]


def test_objective_rejects_non_string_types() -> None:
    """objective must be a str at runtime."""
    with pytest.raises(TypeError, match="objective"):
        WorkItem(work_item_id="wi-001", objective=None)  # type: ignore[arg-type]


def test_status_rejects_raw_strings_and_incompatible_types() -> None:
    """status accepts WorkItemStatus members only; no silent string coercion."""
    with pytest.raises(TypeError, match="status"):
        WorkItem(
            work_item_id="wi-001",
            objective="ok",
            status="open",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="status"):
        WorkItem(
            work_item_id="wi-001",
            objective="ok",
            status="not-a-status",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="status"):
        WorkItem(
            work_item_id="wi-001",
            objective="ok",
            status=1,  # type: ignore[arg-type]
        )


def test_external_ref_rejects_incompatible_types() -> None:
    """external_ref must be WorkItemExternalRef or None."""
    with pytest.raises(TypeError, match="external_ref"):
        WorkItem(
            work_item_id="wi-001",
            objective="ok",
            external_ref="notion:page",  # type: ignore[arg-type]
        )


def test_depends_on_rejects_mutable_or_non_tuple_containers() -> None:
    """depends_on must be a tuple; lists are rejected without conversion."""
    with pytest.raises(TypeError, match="depends_on"):
        WorkItem(
            work_item_id="wi-001",
            objective="ok",
            depends_on=["wi-000"],  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="depends_on"):
        WorkItem(
            work_item_id="wi-001",
            objective="ok",
            depends_on="wi-000",  # type: ignore[arg-type]
        )


def test_depends_on_rejects_non_string_elements() -> None:
    """Each depends_on entry must be a str identifier."""
    with pytest.raises(TypeError, match="depends_on"):
        WorkItem(
            work_item_id="wi-001",
            objective="ok",
            depends_on=(1,),  # type: ignore[arg-type]
        )


def test_context_refs_rejects_non_tuple_and_non_string_elements() -> None:
    """context_refs must be a tuple of opaque string handles."""
    with pytest.raises(TypeError, match="context_refs"):
        WorkItem(
            work_item_id="wi-001",
            objective="ok",
            context_refs=["ctx"],  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="context_refs"):
        WorkItem(
            work_item_id="wi-001",
            objective="ok",
            context_refs=(None,),  # type: ignore[arg-type]
        )


def test_valid_collections_preserve_equality_hash_and_immutability() -> None:
    """Accepted tuples stay frozen; equal instances share hash."""
    left = replace(
        _item(),
        depends_on=("wi-000",),
        context_refs=("ctx://a",),
        external_ref=WorkItemExternalRef(system="file", reference="req.md"),
    )
    right = replace(
        _item(),
        depends_on=("wi-000",),
        context_refs=("ctx://a",),
        external_ref=WorkItemExternalRef(system="file", reference="req.md"),
    )
    assert left == right
    assert hash(left) == hash(right)
    assert isinstance(left.depends_on, tuple)
    assert isinstance(left.context_refs, tuple)
    with pytest.raises(AttributeError):
        left.depends_on = ("wi-999",)  # type: ignore[misc]
    with pytest.raises(AttributeError):
        left.depends_on.append("x")  # type: ignore[attr-defined]


def test_is_schedulable_accepts_iterable_completed_ids() -> None:
    """completed_ids remains an Iterable[str] by design, not only frozenset."""
    item = replace(_item(), depends_on=("wi-000",))
    assert is_schedulable(item, completed_ids=["wi-000"]) is True
    assert is_schedulable(item, completed_ids=("wi-000",)) is True
    assert is_schedulable(item, completed_ids={"wi-000"}) is True
    assert is_schedulable(item, completed_ids=frozenset({"wi-000"})) is True
    assert is_schedulable(item, completed_ids=iter(["wi-000"])) is True


def test_is_schedulable_rejects_str_or_bytes_as_completed_ids_container() -> None:
    """A bare str/bytes must not be treated as a character iterable of ids."""
    item = replace(_item(), depends_on=("wi-000",))
    with pytest.raises(TypeError, match="completed_ids"):
        # str is Iterable[str] for mypy, but must be rejected at runtime.
        is_schedulable(item, completed_ids="wi-000")
    with pytest.raises(TypeError, match="completed_ids"):
        is_schedulable(item, completed_ids=b"wi-000")  # type: ignore[arg-type]


def test_is_schedulable_rejects_invalid_completed_id_elements() -> None:
    """Each completed id must be a non-empty, unpadded str."""
    item = replace(_item(), depends_on=("wi-000",))
    with pytest.raises(TypeError, match="completed_ids"):
        is_schedulable(item, completed_ids=[1])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="completed_ids"):
        is_schedulable(item, completed_ids=[""])
    with pytest.raises(ValueError, match="completed_ids"):
        is_schedulable(item, completed_ids=["   "])
    with pytest.raises(ValueError, match="completed_ids"):
        is_schedulable(item, completed_ids=[" wi-000"])


def test_is_schedulable_open_without_dependencies() -> None:
    """OPEN items with no depends_on remain schedulable with empty completed_ids."""
    assert is_schedulable(_item(), completed_ids=()) is True
    assert is_schedulable(_item(), completed_ids=frozenset()) is True


def test_is_schedulable_blocked_and_done_ignore_completed_ids() -> None:
    """BLOCKED/DONE stay unschedulable even when completed_ids looks complete."""
    blocked = replace(_item(), status=WorkItemStatus.BLOCKED, depends_on=("wi-000",))
    done = replace(_item(), status=WorkItemStatus.DONE, depends_on=("wi-000",))
    completed = frozenset({"wi-000"})
    assert is_schedulable(blocked, completed_ids=completed) is False
    assert is_schedulable(done, completed_ids=completed) is False


def test_context_refs_allow_duplicate_opaque_handles() -> None:
    """ADR-047 does not require uniqueness for opaque context_refs."""
    item = replace(_item(), context_refs=("ctx://a", "ctx://a"))
    assert item.context_refs == ("ctx://a", "ctx://a")


def test_is_schedulable_annotation_resolves_iterable_at_runtime() -> None:
    """Iterable in is_schedulable annotations must resolve without TYPE_CHECKING."""
    hints = get_type_hints(is_schedulable)
    assert "completed_ids" in hints


def test_work_item_module_has_no_infrastructure_or_notion_sdk_imports() -> None:
    """Domain WorkItem stays free of Notion SDKs, adapters, ports, and IO."""
    path = Path(__file__).resolve().parents[3] / "agent_loop" / "domain" / "work_item.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden_roots = (
        "django",
        "subprocess",
        "requests",
        "httpx",
        "notion",
        "notion_client",
        "agent_loop.adapters",
        "agent_loop.ports",
        "scripts",
    )
    for module in imported:
        for root in forbidden_roots:
            assert module != root
            assert not module.startswith(f"{root}.")
