"""Behavior tests for WorkItem → AgentExecutionRequest gating."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from typing import get_args
from typing import get_origin
from typing import get_type_hints

import pytest

from agent_loop.application.execution_request_builder import ExecutionRequestOptions
from agent_loop.application.execution_request_builder import build_execution_request
from agent_loop.domain.capabilities import CapabilityAxis
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilityRequirement
from agent_loop.domain.task_assessment import AcceptanceClarity
from agent_loop.domain.task_assessment import TaskAssessmentRequest
from agent_loop.domain.task_assessment import TaskComplexity
from agent_loop.domain.task_assessment import TaskRisk
from agent_loop.domain.task_assessment import TaskUncertainty
from agent_loop.domain.task_assessment import assess_task
from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy
from agent_loop.ports.agent_runtime import RuntimeIdentity
from tests.agent_loop.application.execution_request_ast_inventory import PRODUCTION_REQUEST_CTORS
from tests.agent_loop.application.execution_request_ast_inventory import REPO_ROOT
from tests.agent_loop.application.execution_request_ast_inventory import TEST_REQUEST_CTORS
from tests.agent_loop.application.execution_request_ast_inventory import agent_execution_request_ctor_paths
from tests.agent_loop.application.execution_request_ast_inventory import application_modules_importing_builder
from tests.agent_loop.application.execution_request_ast_inventory import application_modules_invoking_builder

_WORKSPACE = "/workspace"


def _work_item(
    *,
    work_item_id: str = "wi-001",
    objective: str = "Implement execution request builder",
    status: WorkItemStatus = WorkItemStatus.OPEN,
    depends_on: tuple[str, ...] = (),
    context_refs: tuple[str, ...] = (),
) -> WorkItem:
    return WorkItem(
        work_item_id=work_item_id,
        objective=objective,
        status=status,
        depends_on=depends_on,
        context_refs=context_refs,
    )


def _default_options() -> ExecutionRequestOptions:
    return ExecutionRequestOptions(
        strategy=ExecutionStrategy.BUILD,
        working_directory=_WORKSPACE,
        mutation_policy=MutationPolicy(allow_workspace_mutation=False),
        permission_policy=PermissionPolicy(allow_network=False),
    )


def _build(
    work_item: WorkItem,
    *,
    completed_ids: tuple[str, ...] | frozenset[str] | list[str] = (),
    options: ExecutionRequestOptions | None = None,
) -> AgentExecutionRequest:
    return build_execution_request(
        work_item,
        completed_ids=completed_ids,
        options=options if options is not None else _default_options(),
    )


def _attempt_isolated_hand_off(
    work_item: WorkItem,
    *,
    completed_ids: tuple[str, ...] = (),
) -> AgentExecutionRequest | None:
    """Gate then hand off to a stand-in runtime (isolation only, not product wiring)."""
    execute_calls: list[AgentExecutionRequest] = []

    class _RecordingRuntime:
        @property
        def identity(self) -> RuntimeIdentity:
            return RuntimeIdentity(runtime_id="recording")

        def discover(self, context: object) -> object:
            del context
            message = "discover must not run during builder tests"
            raise AssertionError(message)

        def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
            execute_calls.append(request)
            return AgentExecutionResult(
                status=AgentExecutionStatus.SUCCEEDED,
                runtime_identity=self.identity,
            )

    runtime = _RecordingRuntime()
    try:
        request = _build(work_item, completed_ids=completed_ids)
    except ValueError:
        assert execute_calls == []
        return None
    runtime.execute(request)
    assert len(execute_calls) == 1
    return execute_calls[0]


def test_open_work_item_without_dependencies_builds_request() -> None:
    """OPEN items with no deps produce a request from the objective text."""
    item = _work_item()
    request = _build(item, completed_ids=())
    assert request.task == item.objective
    assert request.strategy is ExecutionStrategy.BUILD
    assert request.working_directory == _WORKSPACE
    assert request.context_refs == ()
    assert request.model is None
    assert request.required_capabilities == ()


def test_open_work_item_with_satisfied_dependencies_builds_request() -> None:
    """OPEN items advance when every depends_on id is in completed_ids."""
    item = _work_item(depends_on=("wi-000", "wi-00a"))
    request = _build(item, completed_ids=("wi-000", "wi-00a"))
    assert request.task == item.objective


def test_open_work_item_with_pending_dependency_does_not_build_request() -> None:
    """A pending dependency must not produce an executable request."""
    item = _work_item(depends_on=("wi-000", "wi-00a"))
    with pytest.raises(ValueError, match="not schedulable"):
        _build(item, completed_ids=("wi-000",))


def test_blocked_work_item_does_not_build_request() -> None:
    """BLOCKED items never produce an executable request."""
    item = _work_item(status=WorkItemStatus.BLOCKED)
    with pytest.raises(ValueError, match="not schedulable"):
        _build(item, completed_ids=())


def test_done_work_item_does_not_build_request() -> None:
    """DONE items cannot be re-executed as pending work."""
    item = _work_item(status=WorkItemStatus.DONE)
    with pytest.raises(ValueError, match="not schedulable"):
        _build(item, completed_ids=())


def test_missing_completion_information_fails_closed() -> None:
    """Absence of completed dependency evidence does not enable execution."""
    item = _work_item(depends_on=("wi-000",))
    with pytest.raises(ValueError, match="not schedulable"):
        _build(item, completed_ids=())


def test_planned_or_dispatched_ids_are_not_treated_as_completed() -> None:
    """Planning or prior dispatch alone does not satisfy depends_on."""
    item = _work_item(depends_on=("wi-planned", "wi-dispatched"))
    with pytest.raises(ValueError, match="not schedulable"):
        _build(item, completed_ids=())


def test_ineligible_work_item_never_reaches_isolated_execute() -> None:
    """Isolation: builder refusal prevents stand-in execute (not product wiring)."""
    blocked = _work_item(status=WorkItemStatus.BLOCKED)
    pending = _work_item(depends_on=("wi-000",))
    done = _work_item(status=WorkItemStatus.DONE)
    assert _attempt_isolated_hand_off(blocked) is None
    assert _attempt_isolated_hand_off(pending) is None
    assert _attempt_isolated_hand_off(done) is None


def test_eligible_work_item_can_reach_isolated_execute() -> None:
    """Isolation: schedulable items may hand off after construction (not product wiring)."""
    item = _work_item(objective="Ship the gate")
    dispatched = _attempt_isolated_hand_off(item, completed_ids=())
    assert dispatched is not None
    assert dispatched.task == "Ship the gate"


def test_execution_request_options_type_hints_resolve_at_runtime() -> None:
    """ExecutionRequestOptions annotations resolve via get_type_hints."""
    hints = get_type_hints(ExecutionRequestOptions)
    assert hints["strategy"] is ExecutionStrategy
    assert hints["working_directory"] is str
    assert hints["mutation_policy"] is MutationPolicy
    assert hints["permission_policy"] is PermissionPolicy
    assert hints["model"] == (str | None)
    assert hints["structured_output_schema"] == (str | None)
    assert hints["session_ref"] == (str | None)
    caps = hints["required_capabilities"]
    assert get_origin(caps) is tuple
    assert get_args(caps)[0] is CapabilityRequirement


def test_build_execution_request_type_hints_resolve_at_runtime() -> None:
    """build_execution_request annotations resolve via get_type_hints."""
    hints = get_type_hints(build_execution_request)
    assert hints["work_item"] is WorkItem
    assert hints["options"] is ExecutionRequestOptions
    assert hints["return"] is AgentExecutionRequest
    assert hints["completed_ids"] == (Iterable[str] | frozenset[str])


def test_production_agent_execution_request_ctors_are_only_the_builder() -> None:
    """Inventory: production constructs AgentExecutionRequest only in the builder.

    Detects direct names, import aliases from ``agent_runtime``, and attribute
    constructors. Does not claim coverage of assignment aliases or dynamic calls.
    """
    found = agent_execution_request_ctor_paths()
    production = {path for path in found if not path.startswith("tests/")}
    assert production == PRODUCTION_REQUEST_CTORS
    assert found <= (PRODUCTION_REQUEST_CTORS | TEST_REQUEST_CTORS)


def test_application_builder_consumer_is_planning_bridge_only() -> None:
    """S17 bridge is the sole application consumer of the builder.

    Separate checks: import ≠ invocation. Inventory does not analyze dataflow
    to ``AgentRuntime.execute``; planning→builder is not runtime dispatch
    (ADR-048 remains PARTIAL).
    """
    expected = ["agent_loop/application/planned_execution_requests.py"]
    assert application_modules_importing_builder() == expected
    assert application_modules_invoking_builder() == expected


def test_context_refs_and_capabilities_are_copied_explicitly() -> None:
    """Builder preserves opaque context refs and assessment-derived capabilities."""
    requirement = CapabilityRequirement(
        capability_id=CapabilityId(value="filesystem.read"),
        mandatory_axes=frozenset({CapabilityAxis.SUPPORTED, CapabilityAxis.AVAILABLE}),
    )
    item = _work_item(context_refs=("ctx://repo", "ctx://docs"))
    options = ExecutionRequestOptions(
        strategy=ExecutionStrategy.PLAN,
        working_directory=_WORKSPACE,
        mutation_policy=MutationPolicy(allow_workspace_mutation=True),
        permission_policy=PermissionPolicy(allow_network=True),
        required_capabilities=(requirement,),
        model="anthropic/claude-sonnet-4",
        session_ref="sess-1",
    )
    request = _build(item, completed_ids=(), options=options)
    assert request.context_refs == ("ctx://repo", "ctx://docs")
    assert request.required_capabilities == (requirement,)
    assert request.model == "anthropic/claude-sonnet-4"
    assert request.session_ref == "sess-1"
    assert request.mutation_policy.allow_workspace_mutation is True


def test_task_assessment_remains_independent_input() -> None:
    """TaskAssessment is not rewritten; capabilities are passed in explicitly."""
    assessment = assess_task(
        TaskAssessmentRequest(
            baseline_complexity=TaskComplexity.MECHANICAL,
            baseline_risk=TaskRisk.LOW,
            baseline_uncertainty=TaskUncertainty.LOW,
            acceptance_clarity=AcceptanceClarity.CLEAR,
        ),
    )
    item = _work_item(objective="Assessed mechanical task")
    options = ExecutionRequestOptions(
        strategy=ExecutionStrategy.BUILD,
        working_directory=_WORKSPACE,
        mutation_policy=MutationPolicy(allow_workspace_mutation=False),
        permission_policy=PermissionPolicy(allow_network=False),
        required_capabilities=assessment.required_capabilities,
    )
    request = _build(item, completed_ids=(), options=options)
    assert request.required_capabilities == assessment.required_capabilities
    assert request.task == item.objective


def test_builder_does_not_import_adapters_or_scripts() -> None:
    """Integration stops at request construction; adapters are not imported."""
    path = REPO_ROOT / "agent_loop" / "application" / "execution_request_builder.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for module in imported:
        assert not module.startswith("agent_loop.adapters")
        assert module != "scripts"
        assert not module.startswith("scripts.")
