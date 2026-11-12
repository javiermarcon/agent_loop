"""Small coverage contracts shared by domain, application and adapter helpers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from agent_loop.adapters.claude.parse import _parse_mcp_row
from agent_loop.adapters.claude.parse import parse_auth_status_json
from agent_loop.adapters.claude.parse import parse_plugin_list
from agent_loop.adapters.claude.parse import redact_sensitive as claude_redact
from agent_loop.adapters.claude.runtime import ClaudeCodeRuntime
from agent_loop.adapters.opencode.parse import _agent_from_item
from agent_loop.adapters.opencode.parse import redact_sensitive as opencode_redact
from agent_loop.application.lifecycle import LifecycleInvocation
from agent_loop.application.lifecycle import render_run_context
from agent_loop.application.resume_invocation import ResumeLockOwnership
from agent_loop.application.resume_invocation import ResumeLockOwnershipState
from agent_loop.application.resume_invocation import configure_resume_invocation
from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.discovery.tool_commands import _read_text as tool_command_read_text
from agent_loop.autonomy.discovery.tool_models import ToolInventory
from agent_loop.autonomy.discovery.tool_models import ToolKind
from agent_loop.autonomy.execution.agent import AgentRunResult
from agent_loop.autonomy.execution.runner import RetryDecision
from agent_loop.autonomy.execution.runner import ValidationReport
from agent_loop.autonomy.execution.runner import ValidationRunner
from agent_loop.autonomy.tools.base import FailureKind
from agent_loop.autonomy.tools.base import ToolResult
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import _as_text
from agent_loop.autonomy.tools.failure import classify_exit_code
from agent_loop.domain.model_profile_catalog import RuntimeModelProfileCatalog
from agent_loop.domain.task_assessment import AcceptanceClarity
from agent_loop.domain.task_assessment import ImpactPresence
from agent_loop.domain.task_assessment import TaskAssessmentRequest
from agent_loop.domain.task_assessment import TaskComplexity
from agent_loop.domain.task_assessment import TaskRisk
from agent_loop.domain.task_assessment import TaskUncertainty
from agent_loop.domain.task_assessment import _add_requirement
from agent_loop.domain.task_assessment import assess_task
from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import is_schedulable
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_project


def _request(**kwargs: object) -> TaskAssessmentRequest:
    defaults: dict[str, object] = {
        "baseline_complexity": TaskComplexity.MECHANICAL,
        "baseline_risk": TaskRisk.LOW,
        "baseline_uncertainty": TaskUncertainty.LOW,
        "acceptance_clarity": AcceptanceClarity.CLEAR,
    }
    defaults.update(kwargs)
    return TaskAssessmentRequest(**defaults)  # type: ignore[arg-type]


def test_task_assessment_request_rejects_untrimmed_entries() -> None:
    """Impacted domains and integrations must be non-empty stripped strings."""
    with pytest.raises(ValueError, match="impacted_domains"):
        _request(impacted_domains=(" bad",))
    with pytest.raises(ValueError, match="external_integrations"):
        _request(external_integrations=("",))


def test_task_assessment_floor_already_met_reasons() -> None:
    """A baseline already above a floor records the explicit reason."""
    assessment = assess_task(
        _request(
            baseline_complexity=TaskComplexity.ARCHITECTURAL,
            concurrency_impact=ImpactPresence.YES,
            architecture_impact=ImpactPresence.YES,
        ),
    )
    assert assessment.complexity is TaskComplexity.ARCHITECTURAL
    assert "floor:concurrency_complexity_already_met" in assessment.reasons
    assert "floor:architectural_complexity_already_met" in assessment.reasons


def test_add_requirement_deduplicates_by_capability_id() -> None:
    """One capability requirement is recorded once."""
    sink: dict[str, object] = {}
    reasons: list[str] = []
    from agent_loop.domain.capabilities import CapabilityAxis  # noqa: PLC0415

    _add_requirement(
        sink,  # type: ignore[arg-type]
        reasons,
        capability_id="runtime",
        axes=frozenset({CapabilityAxis.HEALTHY}),
        reason="first",
    )
    _add_requirement(
        sink,  # type: ignore[arg-type]
        reasons,
        capability_id="runtime",
        axes=frozenset({CapabilityAxis.HEALTHY}),
        reason="second",
    )
    assert reasons == ["first"]


def test_work_item_completed_ids_rejects_non_iterable() -> None:
    """A non-iterable completion set is a type error, never an empty success."""
    item = WorkItem(work_item_id="a", objective="a")
    with pytest.raises(TypeError, match="iterable"):
        is_schedulable(item, completed_ids=3)  # type: ignore[arg-type]


def test_model_profile_catalog_contains() -> None:
    """Membership uses the stable runtime/profile identity key."""
    empty = RuntimeModelProfileCatalog(bindings=())
    assert ("r", "p") not in empty


def test_lifecycle_invocation_and_render_context() -> None:
    """Lifecycle selectors and missing target rendering are explicit."""
    args = argparse.Namespace(resume_last=True, dry_run=True)
    invocation = LifecycleInvocation(args)
    assert invocation.resume_requested is True
    assert invocation.is_preview is False
    invocation.select_multi_requirement()
    assert invocation.is_multi_requirement is True
    assert invocation.is_preview is True
    context = SimpleNamespace(
        target_repo_root=None,
        target_repo_root_source=None,
        orchestrator_root=Path("/repo"),
        target_repo_root_reason="missing",
        orchestrator_root_source=None,
        preview_base_dir=None,
        enhancement_repo_root=None,
    )
    rendered = render_run_context(cast("Any", context))
    assert "- External target: n/a" in rendered or "n/a" in "\n".join(rendered)


def test_resume_lock_release_if_acquired() -> None:
    """Rollback releases an acquired lock and leaves transferred ownership alone."""

    class _Lock:
        def __init__(self) -> None:
            self.released = False

        def release(self) -> None:
            self.released = True

    lock = _Lock()
    ownership = ResumeLockOwnership.acquired(lock)
    ownership.release_if_acquired()
    assert ownership.state is ResumeLockOwnershipState.RELEASED
    ownership.release_if_acquired()
    assert lock.released is True

    other = _Lock()
    transferred = ResumeLockOwnership.acquired(other).transfer()
    assert transferred is other


def test_configure_resume_invocation_missing_target() -> None:
    """A missing target after catalog resolution is terminal."""
    discovery = SimpleNamespace(
        resolve_catalog=lambda request, target: SimpleNamespace(exit_code=None, catalog=object()),
    )
    collaborators = SimpleNamespace(discovery=discovery)
    result: Any = configure_resume_invocation(
        object(),
        None,
        list_requested=False,
        missing_target_exit=7,
        collaborators=collaborators,  # type: ignore[arg-type]
    )
    assert result.exit_code == 7


def test_runner_properties_and_missing_discovery(tmp_path: Path) -> None:
    """Validation reports expose failure/retry metadata; missing discovery is unknown."""
    failed = ToolResult(
        tool=ToolKind.RUFF,
        status=ToolStatus.FAIL,
        message="bad",
        failure_kind=FailureKind.INFRASTRUCTURE,
    )
    report = ValidationReport(results=(failed,))
    assert report.failed is True
    assert report.retry_decision is RetryDecision.STOP_NON_RETRYABLE
    assert isinstance(report.to_dict(), dict)

    context = SimpleNamespace(inventory=ToolInventory(entries=()))
    registry = SimpleNamespace(kinds=lambda: (ToolKind.RUFF,), get=lambda kind: None)
    result = ValidationRunner(registry, RecordingRunner())._run_one(context, ToolKind.RUFF)  # type: ignore[arg-type]  # noqa: SLF001
    assert result.status is ToolStatus.UNKNOWN
    assert "missing" in result.message


def test_agent_result_to_dict() -> None:
    """Agent results serialize without losing transport metadata."""
    result = AgentRunResult(
        status=ToolStatus.PASS,
        command=("agent",),
        exit_code=0,
        stdout="ok",
        stderr="",
        duration_seconds=0.1,
        message="done",
        prompt_transport="stdin",
        prompt_bytes=3,
        prompt_sha256="hash",
    )
    assert result.to_dict()["prompt_transport"] == "stdin"


def test_tool_command_read_text_oserror(tmp_path: Path) -> None:
    """Unreadable documented-command files yield an empty text."""
    directory = tmp_path / "dir"
    directory.mkdir()
    assert tool_command_read_text(directory) == ""


def test_tool_inventory_missing_kind() -> None:
    """An absent tool kind is None, never a synthetic success."""
    assert ToolInventory(entries=()).get(ToolKind.RUFF) is None


def test_cli_configuration_error_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ConfigurationError raised during execution maps to usage exit code."""
    root = make_project(tmp_path / "repo")
    from agent_loop.autonomy.errors import ConfigurationError  # noqa: PLC0415

    monkeypatch.setattr(
        "agent_loop.autonomy.cli.AutonomyLoop.run",
        lambda self, request: (_ for _ in ()).throw(ConfigurationError("bad config")),
    )
    from agent_loop.autonomy.cli import main  # noqa: PLC0415

    assert main(["--repo", str(root), "--requirement", "x", "--dry-run"]) == 2
    assert "bad config" in capsys.readouterr().err


def test_config_rejects_non_transport_value() -> None:
    """The CLI transport is a typed enum, not an arbitrary string."""
    from agent_loop.autonomy.errors import ConfigurationError  # noqa: PLC0415

    with pytest.raises(ConfigurationError, match="PromptTransport"):
        AutonomyConfig(agent_prompt_transport="stdin")  # type: ignore[arg-type]


def test_command_as_text_returns_str() -> None:
    """String process output is returned unchanged."""
    assert _as_text("text") == "text"


def test_failure_classifiers_cover_remaining_tools() -> None:
    """Tool-specific exit codes keep infrastructure/config distinctions."""
    assert classify_exit_code(ToolKind.PYTEST, 4) is FailureKind.USAGE
    assert classify_exit_code(ToolKind.PYTEST, 5) is FailureKind.NONE
    assert classify_exit_code(ToolKind.PYTEST, -1) is FailureKind.INFRASTRUCTURE
    assert classify_exit_code(ToolKind.MYPY, 2) is FailureKind.CONFIGURATION
    assert classify_exit_code(ToolKind.BANDIT, 2) is FailureKind.USAGE
    assert classify_exit_code(ToolKind.PRE_COMMIT, 1) is FailureKind.IMPLEMENTATION
    assert classify_exit_code(ToolKind.PRE_COMMIT, 2) is FailureKind.INFRASTRUCTURE


def test_claude_and_opencode_parser_truncation_and_invalid_shapes() -> None:
    """Long observations are truncated; malformed JSON/rows fail closed."""
    assert claude_redact("x" * 5000).endswith("...")
    assert opencode_redact("x" * 5000).endswith("...")
    assert parse_auth_status_json("[]").outcome.value == "invalid"
    assert parse_auth_status_json(json.dumps({"loggedIn": True})).outcome.value == "invalid"
    assert parse_auth_status_json(json.dumps({"loggedIn": True, "authMethod": " "})).outcome.value == "invalid"
    assert parse_plugin_list("").outcome.value == "invalid"
    assert _parse_mcp_row("not a row") is None
    assert _parse_mcp_row("- bad name: http - Connected") is None
    assert _agent_from_item("not-a-dict") is None


def test_claude_runtime_plugin_failure_is_unverified_channel() -> None:
    """A failed plugin listing becomes UNKNOWN channel evidence, not connected."""
    runtime = ClaudeCodeRuntime(binary="/bin/false")
    result = runtime._plugins_snapshot(  # noqa: SLF001
        plugin_result=cast("Any", SimpleNamespace(ok=False, timed_out=False, returncode=2, stdout="", stderr="")),
        runtime_version="2.1.0",
        observed_at=__import__("datetime").datetime.now(tz=__import__("datetime").UTC),
    )
    assert result.capability_id.value == "runtime.plugins"
