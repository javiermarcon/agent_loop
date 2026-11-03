"""Structured report rendering tests."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.discovery.git import ChangeAttribution
from agent_loop.autonomy.discovery.git import GitAudit
from agent_loop.autonomy.discovery.git import GitBaseline
from agent_loop.autonomy.discovery.git import GitChange
from agent_loop.autonomy.discovery.git import GitState
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.execution.agent import AgentRunResult
from agent_loop.autonomy.execution.loop import AutonomyLoop
from agent_loop.autonomy.execution.loop import AutonomyRequest
from agent_loop.autonomy.execution.runner import ValidationReport
from agent_loop.autonomy.report import AutonomyResult
from agent_loop.autonomy.report import render_markdown
from agent_loop.autonomy.report import to_json
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.tools.base import ToolResult
from agent_loop.autonomy.tools.base import ToolStatus
from tests.agent_loop.autonomy.support import FakeProbe
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    from pathlib import Path

_MODULES = ("ruff", "mypy", "pylint", "bandit", "pytest", "behave", "pre_commit")


def _dry_run_result(tmp_path: Path) -> AutonomyResult:
    root = make_project(tmp_path / "repo")
    write(root / "AGENTS.md", "root rules\n")
    workspace = TargetWorkspace.open(root)
    config = AutonomyConfig(agent_command=("fake-agent",))
    request = AutonomyRequest(
        workspace=workspace,
        requirement="Add health check",
        config=config,
        dry_run=True,
    )
    return AutonomyLoop(
        config,
        command_runner=RecordingRunner(),
        probe=FakeProbe(dict.fromkeys(_MODULES, True)),
    ).run(request)


def test_markdown_report_contains_project_git_tools_and_prompt(tmp_path: Path) -> None:
    """Markdown summarizes the target and includes the dry-run prompt."""
    result = _dry_run_result(tmp_path)
    rendered = render_markdown(result)
    assert "# Agent Loop autonomy report" in rendered
    assert "- Status: **pass**" in rendered
    assert "Django detected: True" in rendered
    assert "## Tools" in rendered
    assert "## Prompt" in rendered
    assert "Add health check" in rendered


def test_json_report_is_sorted_and_structured(tmp_path: Path) -> None:
    """JSON output is stable and exposes discovery, tools and git."""
    result = _dry_run_result(tmp_path)
    payload = json.loads(to_json(result))
    assert payload["status"] == "pass"
    assert payload["discovery"]["project"]["manage_py"] == "manage.py"
    assert payload["discovery"]["project"]["settings_module"] == "config.settings"
    assert isinstance(payload["discovery"]["tools"], list)
    assert payload["final_git"]["is_repository"] is True
    assert "Add health check" in payload["prompt"]


def _git_state(head: str) -> GitState:
    return GitState(
        is_repository=True,
        branch="main",
        head=head,
        changed_files=("apps/sample/models.py",),
    )


def test_markdown_renders_execution_validation_and_audit_sections(tmp_path: Path) -> None:
    """Optional report sections preserve their documented order and content."""
    result = _dry_run_result(tmp_path)
    run = AgentRunResult(
        status=ToolStatus.PASS,
        command=("fake-agent",),
        exit_code=0,
        stdout="",
        stderr="",
        duration_seconds=0.1,
        message="agent finished",
        prompt_transport="argv",
        prompt_bytes=42,
    )
    validation = ValidationReport(
        results=(
            ToolResult(
                tool=ToolKind.PYTEST,
                status=ToolStatus.FAIL,
                message="tests failed",
                stderr="boom",
            ),
        ),
    )
    audit = GitAudit(
        baseline=GitBaseline(state=_git_state("baseline-head"), file_hashes=()),
        final_state=_git_state("final-head"),
        changes=(
            GitChange(
                path="apps/sample/models.py",
                status="M",
                attribution=ChangeAttribution.EXECUTION,
            ),
        ),
        unexpected_paths=("apps/sample/models.py",),
    )
    enriched = replace(
        result,
        agent_runs=(run,),
        validation_reports=(validation,),
        git_audit=audit,
    )
    rendered = render_markdown(enriched)
    assert "- audit passes: 0" not in rendered
    assert "## Git audit" in rendered
    assert "- baseline HEAD: baseline-head" in rendered
    assert "- final HEAD: final-head" in rendered
    assert "apps/sample/models.py (M, execution)" in rendered
    assert "## Agent runs" in rendered
    assert "- pass: agent finished (exit 0; transport argv; 42 bytes)" in rendered
    assert "## Validations" in rendered
    assert "- **pytest**: fail - tests failed" in rendered
    assert "stderr: boom" in rendered
    assert "## Prompt" not in rendered
    order = [
        "## Git audit",
        "## Tools",
        "## Agent runs",
        "## Validations",
    ]
    positions = [rendered.index(section) for section in order]
    assert positions == sorted(positions)


def test_markdown_reports_missing_requirement_source(tmp_path: Path) -> None:
    """A result without requirement provenance renders the explicit fallback."""
    result = replace(_dry_run_result(tmp_path), requirement_source=None)
    assert "- Source: not recorded" in render_markdown(result)


def test_markdown_reports_when_no_instructions_were_discovered(tmp_path: Path) -> None:
    """An empty instruction set renders an explicit absence message."""
    result = _dry_run_result(tmp_path)
    discovery = replace(
        result.discovery,
        instructions=replace(result.discovery.instructions, documents=()),
    )
    rendered = render_markdown(replace(result, discovery=discovery))
    assert "- No repository instructions were discovered." in rendered


def test_markdown_counts_audit_passes_and_validator_windows(tmp_path: Path) -> None:
    """Audit windows are summarized only when a git audit exists."""
    result = _dry_run_result(tmp_path)
    audit = GitAudit(
        baseline=GitBaseline(state=_git_state("baseline-head"), file_hashes=()),
        final_state=_git_state("final-head"),
        changes=(),
        unexpected_paths=(),
    )
    enriched = replace(
        result,
        git_audit=audit,
        iteration_audits=(audit,),
        validation_audits=(audit,),
    )
    rendered = render_markdown(enriched)
    assert "- audit passes: 1" in rendered
    assert "- validator windows: 1" in rendered


def test_markdown_truncates_long_prompt_with_marker(tmp_path: Path) -> None:
    """Long optional content preserves the truncation marker contract."""
    prompt = "x" * 5000
    result = replace(_dry_run_result(tmp_path), prompt=prompt)
    rendered = render_markdown(result)
    assert prompt not in rendered
    assert "...[truncated]" in rendered
