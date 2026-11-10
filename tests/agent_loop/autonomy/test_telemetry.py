"""S23 telemetry integration for the autonomous target-repository loop."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

from agent_loop.adapters.telemetry.sinks import InMemoryTelemetrySink
from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.execution.agent import AgentRunResult
from agent_loop.autonomy.execution.loop import AutonomyLoop
from agent_loop.autonomy.execution.loop import AutonomyRequest
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.telemetry import AutonomyTelemetry
from agent_loop.autonomy.tools.base import ToolContext
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.ports.telemetry import TelemetryEvent
from agent_loop.ports.telemetry import TelemetryEventName
from agent_loop.ports.telemetry import TelemetrySink
from tests.agent_loop.autonomy.support import FakeProbe
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_project

if TYPE_CHECKING:
    from pathlib import Path

_ALL_MODULES = ("ruff", "mypy", "pylint", "bandit", "pytest", "behave", "pre_commit")
_ALL_MODULES_TRUE = dict.fromkeys(_ALL_MODULES, True)
_EXPECTED_DURATION_MS = 1500


@dataclass
class _FakeAgent:
    statuses: list[ToolStatus] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str, context: ToolContext) -> AgentRunResult:
        del context
        self.prompts.append(prompt)
        status = self.statuses.pop(0) if self.statuses else ToolStatus.PASS
        return AgentRunResult(
            status=status,
            command=("fake-agent",),
            exit_code=0 if status is ToolStatus.PASS else 1,
            stdout="private agent output",
            stderr="password=hunter2",
            duration_seconds=1.5,
            message="fake agent",
            prompt_transport="stdin",
            prompt_bytes=128,
        )


class _FailingSink:
    def emit(self, event: TelemetryEvent) -> None:
        del event
        message = "telemetry backend unavailable"
        raise RuntimeError(message)


def _request(
    tmp_path: Path,
    *,
    telemetry: TelemetrySink | None,
    execution_id: str = "autonomy-exec-1",
    validate_only: bool = False,
    tool_kinds: tuple[ToolKind, ...] | None = None,
) -> AutonomyRequest:
    root = make_project(tmp_path / "repo")
    return AutonomyRequest(
        workspace=TargetWorkspace.open(root),
        requirement="Add a private health check endpoint for acme-internal",
        config=AutonomyConfig(agent_command=("fake-agent",)),
        tool_kinds=tool_kinds,
        validate_only=validate_only,
        execution_id=execution_id,
        telemetry=telemetry,
    )


def _loop() -> AutonomyLoop:
    return AutonomyLoop(
        AutonomyConfig(agent_command=("fake-agent",)),
        command_runner=RecordingRunner(),
        agent_invoker=_FakeAgent(),
        probe=FakeProbe(_ALL_MODULES_TRUE),
    )


def _names(events: tuple[TelemetryEvent, ...]) -> list[TelemetryEventName]:
    return [event.name for event in events]


def test_validate_only_run_emits_tool_quality_change_and_closure_events(tmp_path: Path) -> None:
    """The autonomy path publishes real tool, quality, change, and final facts."""
    sink = InMemoryTelemetrySink()
    request = _request(
        tmp_path,
        telemetry=sink,
        validate_only=True,
        tool_kinds=(ToolKind.PYTEST,),
    )
    result = _loop().run(request)
    assert result.status is ToolStatus.PASS
    assert _names(sink.events) == [
        TelemetryEventName.WORK_ITEM_RECEIVED,
        TelemetryEventName.TOOL_COMPLETED,
        TelemetryEventName.QUALITY_COMPLETED,
        TelemetryEventName.CHANGES_DETECTED,
        TelemetryEventName.WORK_ITEM_CLOSED,
    ]
    assert all(event.execution_id == "autonomy-exec-1" for event in sink.events)
    assert all(event.target_repository == str(request.workspace.root) for event in sink.events)
    assert dict(sink.events[0].attributes)["requirement_origin"] == "inline"
    assert dict(sink.events[1].attributes)["tool"] == "pytest"
    assert sink.events[2].status == "pass"
    assert sink.events[3].name is TelemetryEventName.CHANGES_DETECTED
    assert sink.events[-1].status == "pass"
    assert dict(sink.events[-1].attributes)["agent_run_count"] == "0"
    assert "acme-internal" not in repr(sink.events)
    assert "hunter2" not in repr(sink.events)


def test_agent_cycle_emits_execution_tool_and_quality_events(tmp_path: Path) -> None:
    """A real agent/validation iteration is correlated with its validation facts."""
    sink = InMemoryTelemetrySink()
    request = _request(tmp_path, telemetry=sink, tool_kinds=(ToolKind.PYTEST,))
    result = _loop().run(request)
    assert result.status is ToolStatus.PASS
    assert _names(sink.events) == [
        TelemetryEventName.WORK_ITEM_RECEIVED,
        TelemetryEventName.EXECUTION_STARTED,
        TelemetryEventName.EXECUTION_COMPLETED,
        TelemetryEventName.TOOL_COMPLETED,
        TelemetryEventName.QUALITY_COMPLETED,
        TelemetryEventName.CHANGES_DETECTED,
        TelemetryEventName.WORK_ITEM_CLOSED,
    ]
    started, completed = sink.events[1], sink.events[2]
    assert started.runtime_id == "agent-command"
    assert started.attempt == 1
    assert completed.status == "pass"
    assert completed.duration_ms == _EXPECTED_DURATION_MS
    assert dict(completed.attributes)["prompt_transport"] == "stdin"
    assert dict(completed.attributes)["prompt_bytes"] == "128"
    assert dict(sink.events[-1].attributes)["agent_run_count"] == "1"
    assert dict(sink.events[-1].attributes)["validation_report_count"] == "1"


def test_failed_agent_execution_is_observable_and_skips_validation(tmp_path: Path) -> None:
    """An agent failure becomes an explicit failed event without tool events."""
    sink = InMemoryTelemetrySink()
    request = _request(tmp_path, telemetry=sink, tool_kinds=(ToolKind.PYTEST,))
    loop = AutonomyLoop(
        AutonomyConfig(agent_command=("fake-agent",)),
        command_runner=RecordingRunner(),
        agent_invoker=_FakeAgent([ToolStatus.FAIL]),
        probe=FakeProbe(_ALL_MODULES_TRUE),
    )
    result = loop.run(request)
    assert result.status is ToolStatus.FAIL
    assert _names(sink.events) == [
        TelemetryEventName.WORK_ITEM_RECEIVED,
        TelemetryEventName.EXECUTION_STARTED,
        TelemetryEventName.EXECUTION_FAILED,
        TelemetryEventName.CHANGES_DETECTED,
        TelemetryEventName.WORK_ITEM_CLOSED,
    ]
    assert sink.events[2].status == "fail"
    assert dict(sink.events[-1].attributes)["validation_report_count"] == "0"


def test_telemetry_disabled_and_failing_sink_do_not_change_autonomy_result(tmp_path: Path) -> None:
    """Observability is optional and best-effort for the autonomy loop."""
    disabled = _request(tmp_path / "disabled", telemetry=None, validate_only=True, tool_kinds=())
    assert _loop().run(disabled).status is ToolStatus.UNKNOWN
    failing = _request(
        tmp_path / "failing",
        telemetry=_FailingSink(),
        validate_only=True,
        tool_kinds=(ToolKind.PYTEST,),
    )
    assert _loop().run(failing).status is ToolStatus.PASS


def test_autonomy_telemetry_event_budget_and_correlation(tmp_path: Path) -> None:
    """The helper exposes its execution id and honors an explicit event budget."""
    sink = InMemoryTelemetrySink()
    helper = AutonomyTelemetry(
        sink,
        target_repository=str(tmp_path),
        execution_id="bounded-exec",
        max_events=1,
    )
    assert helper.execution_id == "bounded-exec"
    helper.run_received("inline")
    helper.agent_started(1)
    assert len(sink.events) == 1
    assert sink.events[0].execution_id == "bounded-exec"
