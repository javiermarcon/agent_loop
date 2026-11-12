"""Step definitions for requirement-bundle planning and execution E2E scenarios.

The deterministic planning provider is a test double at the AgentRuntime port.
It receives the complete requirement text and returns a structured plan. The
domain planner, request builder, execution orchestrator, recovery policy and
the autonomous validation flow are the real implementations.
"""

# behave decorators mutate the registry; Pylint misreads them as non-callable.
# pylint: disable=not-callable

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from contextlib import redirect_stdout
from dataclasses import replace
from typing import TYPE_CHECKING

from behave import given
from behave import then
from behave import when

from agent_loop.application.execution_orchestrator import ExecutionRunOptions
from agent_loop.application.execution_orchestrator import ExecutionRunStatus
from agent_loop.application.execution_orchestrator import RoutingInputs
from agent_loop.application.execution_orchestrator import execute_work_item_with_recovery
from agent_loop.application.execution_request_builder import ExecutionRequestOptions
from agent_loop.application.planned_execution_requests import build_execution_requests_for_ready_work_items
from agent_loop.application.recovery_policy import RecoveryLimits
from agent_loop.autonomy.cli import main
from agent_loop.domain.faults import ExecutionFailure
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.domain.work_item_plan import InvalidWorkItemDagError
from agent_loop.domain.work_item_plan import plan_work_items
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy
from agent_loop.ports.agent_runtime import RuntimeIdentity
from features.steps.autonomy_e2e_steps import _target
from features.steps.autonomy_e2e_steps import _write
from tests.agent_loop.application.orchestrator_support import assessment
from tests.agent_loop.application.orchestrator_support import failure_result
from tests.agent_loop.application.orchestrator_support import inventory
from tests.agent_loop.application.orchestrator_support import profile
from tests.agent_loop.application.orchestrator_support import routing
from tests.agent_loop.application.orchestrator_support import success_result

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import behave

    from agent_loop.domain.capabilities import RuntimeInventory
    from agent_loop.ports.agent_runtime import DiscoveryContext

_RUNTIME_ID = "fake-provider"
_QUALITY_TASK = "Run the full pytest quality gate"
_DEVELOPMENT_BUNDLE = (
    "Create a Price value object in apps/shop/models.py.\n"
    "Implement apply_discount in apps/shop/service.py and use Price.\n"
    "Add validate_discount in apps/shop/validation.py and reject negative values.\n"
    "Add pytest tests for valid and invalid discounts.\n"
    "Run the full pytest quality gate.\n"
)
_DEVELOPMENT_PLAN = {
    "tasks": [
        {
            "id": "price-entity",
            "instruction": "Create the Price value object in apps/shop/models.py",
            "depends_on": [],
        },
        {
            "id": "discount-operation",
            "instruction": "Implement apply_discount in apps/shop/service.py using Price",
            "depends_on": ["price-entity"],
        },
        {
            "id": "negative-validation",
            "instruction": "Add validate_discount in apps/shop/validation.py rejecting negative values",
            "depends_on": ["discount-operation"],
        },
        {
            "id": "tests",
            "instruction": "Add pytest tests for valid and invalid discounts",
            "depends_on": ["discount-operation", "negative-validation"],
        },
        {
            "id": "quality-gate",
            "instruction": _QUALITY_TASK,
            "depends_on": ["tests"],
        },
    ],
}
_INDEPENDENT_BUNDLE = "Read the model. Write the model.\n"
_INDEPENDENT_PLAN = {
    "tasks": [
        {"id": "read-model", "instruction": "Read the model", "depends_on": []},
        {"id": "write-model", "instruction": "Write the model", "depends_on": []},
    ],
}
_FAILING_BUNDLE = "Run the base task. Run its dependent. Run the independent task.\n"
_FAILING_PLAN = {
    "tasks": [
        {"id": "base", "instruction": "base task that fails", "depends_on": []},
        {"id": "dependent", "instruction": "dependent task", "depends_on": ["base"]},
        {"id": "independent", "instruction": "independent task", "depends_on": []},
    ],
}
_INCOMPLETE_BUNDLE = "Do the thing.\n"
_INCOMPLETE_PLAN = {
    "tasks": [
        {"id": "incomplete", "instruction": "   ", "depends_on": []},
    ],
}


class _DeterministicPlanningProvider:
    """Fake AI interpretation boundary returning a controlled structured plan."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self._identity = RuntimeIdentity("fake-planning-provider")
        self.requests: list[AgentExecutionRequest] = []

    @property
    def identity(self) -> RuntimeIdentity:
        return self._identity

    def discover(self, context: DiscoveryContext) -> RuntimeInventory:
        del context
        return inventory(self._identity.runtime_id)

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        return AgentExecutionResult(
            status=AgentExecutionStatus.SUCCEEDED,
            runtime_identity=self._identity,
            structured_output=json.dumps(self._payload),
            evidence_refs=("plan://deterministic-provider",),
        )


class _WorkspaceRuntime:
    """Deterministic execution runtime that writes real files into the target."""

    def __init__(
        self,
        root: Path,
        context: behave.Context,
        *,
        actions: dict[str, Callable[[], object]],
        failing_tasks: frozenset[str] = frozenset(),
    ) -> None:
        self._root = root
        self._context = context
        self._actions = actions
        self._failing_tasks = failing_tasks
        self._identity = RuntimeIdentity(_RUNTIME_ID)
        self.requests: list[AgentExecutionRequest] = []

    @property
    def identity(self) -> RuntimeIdentity:
        return self._identity

    def discover(self, context: DiscoveryContext) -> RuntimeInventory:
        del context
        return inventory(self._identity.runtime_id)

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        if request.task == _QUALITY_TASK:
            return self._run_quality_gate(request)
        if request.task in self._failing_tasks:
            return failure_result(
                _RUNTIME_ID,
                FailureCategory.EXECUTION_FAILURE,
                ExecutionState.NOT_STARTED,
            )
        action = self._actions.get(request.task)
        if action is not None:
            action()
        return success_result(_RUNTIME_ID, text=request.task, evidence=("execution://workspace",))

    def _run_quality_gate(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        code, payload = _run_validation(self._root, "pytest")
        self._context.validation_reports.append(payload)
        if code == 0 and payload.get("validation_status") == "pass":
            return success_result(_RUNTIME_ID, text=request.task, evidence=("validation://pytest/pass",))
        detail = str(payload.get("message") or "target pytest validation failed")
        return AgentExecutionResult(
            status=AgentExecutionStatus.FAILED,
            runtime_identity=self._identity,
            evidence_refs=("validation://pytest/fail",),
            observed_failures=(detail,),
            failure=ExecutionFailure(
                category=FailureCategory.QUALITY_FAILURE,
                execution_state=ExecutionState.COMPLETED,
                evidence=("validation://pytest/fail",),
                detail=detail,
            ),
        )


def _real_target(context: behave.Context) -> Path:
    root = _target(context)
    fake_pytest = root / ".venv" / "bin" / "pytest"
    fake_pytest.unlink(missing_ok=True)
    _write(root / "apps" / "__init__.py", "")
    _write(root / "apps" / "shop" / "__init__.py", "")
    return root


def _run_validation(root: Path, tool: str) -> tuple[int, dict[str, object]]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = 0
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "--repo",
                    str(root),
                    "--requirement",
                    "validate the planned implementation",
                    "--validate-only",
                    "--full",
                    "--tool",
                    tool,
                    "--json",
                ],
            )
    except SystemExit as exc:
        code = int(exc.code or 0)
    payload: dict[str, object] = {}
    if stdout.getvalue().strip().startswith("{"):
        payload = json.loads(stdout.getvalue())
    return code, payload


def _work_items(payload: dict[str, object]) -> tuple[WorkItem, ...]:
    raw_tasks = payload["tasks"]
    assert isinstance(raw_tasks, list)
    return tuple(
        WorkItem(
            work_item_id=str(task["id"]),
            objective=str(task["instruction"]),
            depends_on=tuple(str(dependency) for dependency in task["depends_on"]),
        )
        for task in raw_tasks
        if isinstance(task, dict)
    )


def _objectives(payload: dict[str, object]) -> dict[str, str]:
    raw_tasks = payload["tasks"]
    assert isinstance(raw_tasks, list)
    return {str(task["id"]): str(task["instruction"]) for task in raw_tasks if isinstance(task, dict)}


def _request_options(root: Path) -> ExecutionRequestOptions:
    return ExecutionRequestOptions(
        strategy=ExecutionStrategy.BUILD,
        working_directory=str(root),
        mutation_policy=MutationPolicy(allow_workspace_mutation=True),
        permission_policy=PermissionPolicy(allow_network=False),
    )


def _run_options(root: Path, *, completed_ids: frozenset[str], mutation: bool) -> ExecutionRunOptions:
    return ExecutionRunOptions(
        working_directory=str(root),
        mutation_policy=MutationPolicy(allow_workspace_mutation=mutation),
        permission_policy=PermissionPolicy(allow_network=False),
        completed_ids=completed_ids,
        limits=RecoveryLimits(max_attempts_per_combination=1, max_total_attempts=1),
    )


def _routing() -> RoutingInputs:
    return routing((_RUNTIME_ID, profile("fake-profile", "model-a")))


def _interpret(context: behave.Context, payload: dict[str, object]) -> None:
    root = _real_target(context)
    provider = _DeterministicPlanningProvider(payload)
    result = provider.execute(
        AgentExecutionRequest(
            task=context.requirement_text,
            strategy=ExecutionStrategy.PLAN,
            model="fake/model",
            required_capabilities=(),
            working_directory=str(root),
            mutation_policy=MutationPolicy(allow_workspace_mutation=False),
            permission_policy=PermissionPolicy(allow_network=False),
        ),
    )
    context.planning_provider = provider
    context.planning_result = result
    assert provider.requests[0].task == context.requirement_text
    assert provider.requests[0].strategy is ExecutionStrategy.PLAN
    assert result.status is AgentExecutionStatus.SUCCEEDED
    assert result.structured_output is not None
    context.planning_payload = json.loads(result.structured_output)
    try:
        items = _work_items(context.planning_payload)
        context.work_items = items
        context.plan = plan_work_items(items)
        context.planning_error = None
    except (InvalidWorkItemDagError, KeyError, TypeError, ValueError) as error:
        context.work_items = ()
        context.plan = None
        context.planning_error = str(error)


def _execute(context: behave.Context, *, once: bool) -> None:
    root = _real_target(context)
    objectives = _objectives(context.planning_payload)
    actions = _development_actions(root)
    failing_tasks = frozenset(objective for work_item_id, objective in objectives.items() if work_item_id == "base")
    runtime = _WorkspaceRuntime(root, context, actions=actions, failing_tasks=failing_tasks)
    context.runtime = runtime
    context.requests = []
    context.outcomes = []
    context.validation_reports = []
    items = tuple(context.work_items)
    final_status = ExecutionRunStatus.SUCCEEDED
    while True:
        plan = plan_work_items(items)
        if not plan.ready_ids:
            break
        completed_ids = frozenset(item.work_item_id for item in items if item.status is WorkItemStatus.DONE)
        requests = build_execution_requests_for_ready_work_items(items, options=_request_options(root))
        by_id = {item.work_item_id: item for item in items}
        for work_item_id, request in zip(plan.ready_ids, requests, strict=True):
            context.requests.append(request)
            outcome = execute_work_item_with_recovery(
                by_id[work_item_id],
                assessment(),
                options=_run_options(root, completed_ids=completed_ids, mutation=work_item_id != "base"),
                routing=_routing(),
                runtimes=(runtime,),
            )
            context.outcomes.append(outcome)
            if outcome.status is not ExecutionRunStatus.SUCCEEDED:
                final_status = outcome.status
                context.execution_failure_reason = outcome.final_reason
                break
            items = tuple(
                replace(item, status=WorkItemStatus.DONE) if item.work_item_id == work_item_id else item
                for item in items
            )
            completed_ids = frozenset(item.work_item_id for item in items if item.status is WorkItemStatus.DONE)
        if once or final_status is not ExecutionRunStatus.SUCCEEDED:
            break
    context.work_items = items
    context.final_plan = plan_work_items(items)
    context.final_status = final_status


def _development_actions(root: Path) -> dict[str, Callable[[], object]]:
    return {
        "Create the Price value object in apps/shop/models.py": lambda: _write(
            root / "apps" / "shop" / "models.py",
            "from dataclasses import dataclass\n\n\n"
            "@dataclass(frozen=True)\n"
            "class Price:\n"
            "    amount: int\n"
            "    currency: str = 'USD'\n",
        ),
        "Implement apply_discount in apps/shop/service.py using Price": lambda: _write(
            root / "apps" / "shop" / "service.py",
            "from apps.shop.models import Price\n"
            "from apps.shop.validation import validate_discount\n\n\n"
            "def apply_discount(price: Price, percent: int) -> Price:\n"
            "    validate_discount(percent)\n"
            "    return Price(amount=price.amount - (price.amount * percent // 100), currency=price.currency)\n",
        ),
        "Add validate_discount in apps/shop/validation.py rejecting negative values": lambda: _write(
            root / "apps" / "shop" / "validation.py",
            "def validate_discount(percent: int) -> None:\n"
            "    if percent < 0:\n"
            "        raise ValueError('discount must be non-negative')\n"
            "    if percent > 100:\n"
            "        raise ValueError('discount must not exceed 100')\n",
        ),
        "Add pytest tests for valid and invalid discounts": lambda: _write(
            root / "tests" / "test_shop.py",
            "import pytest\n\n"
            "from apps.shop.models import Price\n"
            "from apps.shop.service import apply_discount\n\n\n"
            "def test_apply_discount():\n"
            "    assert apply_discount(Price(100), 10).amount == 90\n\n\n"
            "def test_rejects_negative_discount():\n"
            "    with pytest.raises(ValueError):\n"
            "        apply_discount(Price(100), -1)\n",
        ),
        "base task that fails": lambda: None,
        "dependent task": lambda: None,
        "independent task": lambda: None,
    }


def _comma_values(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


@given("a target Django repository with a real pytest environment")
def step_real_target(context: behave.Context) -> None:
    _real_target(context)


@given("a requirement bundle with related instructions")
def step_related_bundle(context: behave.Context) -> None:
    context.requirement_text = _DEVELOPMENT_BUNDLE
    context.requirement_file = _write(context.root / "requirement-bundle.md", _DEVELOPMENT_BUNDLE)
    context.planning_payload_source = _DEVELOPMENT_PLAN


@given("a requirement bundle with two independent tasks")
def step_independent_bundle(context: behave.Context) -> None:
    context.requirement_text = _INDEPENDENT_BUNDLE
    context.requirement_file = _write(context.root / "requirement-bundle.md", _INDEPENDENT_BUNDLE)
    context.planning_payload_source = _INDEPENDENT_PLAN


@given("a requirement bundle whose base task fails")
def step_failing_bundle(context: behave.Context) -> None:
    context.requirement_text = _FAILING_BUNDLE
    context.requirement_file = _write(context.root / "requirement-bundle.md", _FAILING_BUNDLE)
    context.planning_payload_source = _FAILING_PLAN


@given("a requirement bundle with an incomplete instruction")
def step_incomplete_bundle(context: behave.Context) -> None:
    context.requirement_text = _INCOMPLETE_BUNDLE
    context.requirement_file = _write(context.root / "requirement-bundle.md", _INCOMPLETE_BUNDLE)
    context.planning_payload_source = _INCOMPLETE_PLAN


@when("AgentLoop interprets the requirement bundle with a deterministic provider")
def step_interpret(context: behave.Context) -> None:
    _interpret(context, context.planning_payload_source)


@when("AgentLoop executes the planned tasks in dependency order")
def step_execute_all(context: behave.Context) -> None:
    _execute(context, once=False)


@when("AgentLoop executes the ready frontier once")
def step_execute_once(context: behave.Context) -> None:
    _execute(context, once=True)


@then('the generated DAG has tasks "{ids}"')
def step_dag_tasks(context: behave.Context, ids: str) -> None:
    assert context.plan is not None, context.planning_error
    generated = tuple(item.work_item_id for item in context.work_items)
    assert sorted(generated) == sorted(_comma_values(ids)), generated


@then('the DAG dependency edges are "{edges}"')
def step_dag_edges(context: behave.Context, edges: str) -> None:
    assert context.plan is not None, context.planning_error
    generated = {f"{dependency}->{item.work_item_id}" for item in context.work_items for dependency in item.depends_on}
    assert generated == set(_comma_values(edges)), generated


@then('the first ready frontier is "{ids}"')
def step_ready_frontier(context: behave.Context, ids: str) -> None:
    assert context.plan is not None, context.planning_error
    assert context.plan.ready_ids == _comma_values(ids), context.plan.ready_ids


@then('the blocked frontier is "{ids}"')
def step_blocked_frontier(context: behave.Context, ids: str) -> None:
    assert context.plan is not None, context.planning_error
    assert context.plan.blocked_ids == _comma_values(ids), context.plan.blocked_ids


@then('the final execution status is "{status}"')
def step_final_status(context: behave.Context, status: str) -> None:
    assert context.final_status.value == status, context.final_status


@then("the repository contains the generated implementation")
def step_implementation(context: behave.Context) -> None:
    root = context.target
    assert "class Price" in (root / "apps" / "shop" / "models.py").read_text(encoding="utf-8")
    assert "def apply_discount" in (root / "apps" / "shop" / "service.py").read_text(encoding="utf-8")
    assert "def validate_discount" in (root / "apps" / "shop" / "validation.py").read_text(
        encoding="utf-8",
    )
    assert (root / "tests" / "test_shop.py").is_file()


@then('the target pytest quality gate status is "{status}"')
def step_quality_status(context: behave.Context, status: str) -> None:
    root = _target(context)
    assert context.validation_reports, "the quality gate was not executed"
    payload = context.validation_reports[-1]
    assert payload["validation_status"] == status, payload
    reports = payload["validation_reports"]
    assert isinstance(reports, list)
    assert reports
    first = reports[0]
    assert isinstance(first, dict)
    results = first["results"]
    assert isinstance(results, list)
    assert results
    pytest_result = next(result for result in results if isinstance(result, dict) and result["tool"] == "pytest")
    assert pytest_result["status"] == "pass", pytest_result
    invocation = pytest_result["invocation"]
    assert isinstance(invocation, dict)
    assert invocation["cwd"] == str(root), invocation
    stdout = str(pytest_result["stdout"])
    assert "3 passed" in stdout, pytest_result
    assert "agent_loop" not in stdout, pytest_result


@then("the final plan marks every task done")
def step_all_done(context: behave.Context) -> None:
    assert context.final_plan is not None
    expected = tuple(sorted(item.work_item_id for item in context.work_items))
    assert context.final_plan.done_ids == expected, context.final_plan.done_ids
    assert context.final_plan.ready_ids == ()
    assert context.final_plan.blocked_ids == ()


@then("the final trace contains the requirement, plan, requests, outcomes and validation reports")
def step_trace(context: behave.Context) -> None:
    assert context.requirement_text
    assert context.planning_result.structured_output
    assert context.plan is not None
    assert context.requests
    assert context.outcomes
    assert context.validation_reports
    assert all(outcome.attempts for outcome in context.outcomes)


@then('the base task execution status is "{status}"')
def step_base_status(context: behave.Context, status: str) -> None:
    base = next(outcome for outcome in context.outcomes if outcome.work_item_id == "base")
    assert base.status.value == status, base.final_reason
    assert context.execution_failure_reason


@then("the dependent task remains blocked after replanning")
def step_dependent_blocked(context: behave.Context) -> None:
    assert "dependent" in context.final_plan.blocked_ids, context.final_plan.blocked_ids


@then("the dependent task was never requested")
def step_dependent_not_requested(context: behave.Context) -> None:
    assert all("dependent task" not in request.task for request in context.requests)


@then('requirement planning is rejected with "{message}"')
def step_planning_rejected(context: behave.Context, message: str) -> None:
    assert context.planning_error is not None
    assert message in context.planning_error, context.planning_error


@then("no task was executed")
def step_no_task_executed(context: behave.Context) -> None:
    assert context.work_items == ()
    assert getattr(context, "outcomes", []) == []
