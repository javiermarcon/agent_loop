"""Structured discovery and execution reports for the autonomous runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_loop.autonomy.tools.base import ToolStatus

if TYPE_CHECKING:
    from agent_loop.autonomy.discovery.django_project import DjangoProjectInfo
    from agent_loop.autonomy.discovery.git import GitState
    from agent_loop.autonomy.discovery.git_attribution import GitAudit
    from agent_loop.autonomy.discovery.instructions import InstructionSet
    from agent_loop.autonomy.discovery.tools import ToolDiscovery
    from agent_loop.autonomy.discovery.tools import ToolInventory
    from agent_loop.autonomy.execution.agent import AgentRunResult
    from agent_loop.autonomy.execution.runner import ValidationReport
    from agent_loop.autonomy.requirement import RequirementSource


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    """Everything discovered about the target before execution."""

    instructions: InstructionSet
    project: DjangoProjectInfo
    tools: ToolInventory
    git: GitState

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "instructions": [
                {
                    "path": document.relative_path,
                    "kind": document.kind,
                    "scope": document.scope,
                }
                for document in self.instructions.documents
            ],
            "project": self.project.to_dict(),
            "tools": self.tools.to_dict(),
            "git": self.git.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AutonomyResult:
    """Final result of one autonomous invocation."""

    discovery: DiscoveryReport
    prompt: str
    agent_runs: tuple[AgentRunResult, ...]
    validation_reports: tuple[ValidationReport, ...]
    final_git: GitState
    status: ToolStatus
    message: str
    validation_status: ToolStatus | None = None
    git_audit: GitAudit | None = None
    iteration_audits: tuple[GitAudit, ...] = ()
    agent_window_audits: tuple[GitAudit, ...] = ()
    validation_audits: tuple[GitAudit, ...] = ()
    requirement_source: RequirementSource | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "status": self.status.value,
            "validation_status": self.validation_status.value if self.validation_status is not None else None,
            "message": self.message,
            "prompt": self.prompt,
            "requirement_source": self.requirement_source.to_dict() if self.requirement_source is not None else None,
            "discovery": self.discovery.to_dict(),
            "agent_runs": [run.to_dict() for run in self.agent_runs],
            "validation_reports": [report.to_dict() for report in self.validation_reports],
            "final_git": self.final_git.to_dict(),
            "git_audit": self.git_audit.to_dict() if self.git_audit is not None else None,
            "iteration_audits": [audit.to_dict() for audit in self.iteration_audits],
            "agent_window_audits": [audit.to_dict() for audit in self.agent_window_audits],
            "validation_audits": [audit.to_dict() for audit in self.validation_audits],
        }


def to_json(result: AutonomyResult) -> str:
    """Serialize a result as deterministic JSON."""
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)


def render_markdown(result: AutonomyResult) -> str:
    """Render a concise human-readable report."""
    lines = _summary_lines(result)
    lines.extend(_instructions_lines(result))
    lines.extend(_project_lines(result))
    lines.extend(_git_lines(result))
    lines.extend(_tools_lines(result))
    lines.extend(_agent_run_lines(result))
    lines.extend(_validation_section_lines(result))
    lines.extend(_prompt_lines(result))
    return "\n".join(lines) + "\n"


def _summary_lines(result: AutonomyResult) -> list[str]:
    validation = result.validation_status.value if result.validation_status is not None else "not run"
    lines = [
        "# Agent Loop autonomy report",
        "",
        f"- Status: **{result.status.value}**",
        f"- Validation status: **{validation}**",
        f"- Message: {result.message}",
        "",
        "## Requirement",
        "",
    ]
    if result.requirement_source is not None:
        lines.extend(
            [
                f"- Source: {result.requirement_source.label}",
                f"- Text: {_short(result.requirement_source.text.strip(), 500)}",
            ],
        )
    else:
        lines.append("- Source: not recorded")
    return lines


def _instructions_lines(result: AutonomyResult) -> list[str]:
    lines = ["", "## Repository instructions", ""]
    documents = result.discovery.instructions.documents
    if documents:
        lines.extend(
            f"- {document.relative_path} ({document.kind}, scope: {document.scope or '.'})" for document in documents
        )
    else:
        lines.append("- No repository instructions were discovered.")
    return lines


def _project_lines(result: AutonomyResult) -> list[str]:
    project = result.discovery.project
    compose_selection = project.compose_reason or project.compose_error or "n/a"
    return [
        "",
        "## Target project",
        "",
        f"- Django detected: {project.is_django}",
        f"- manage.py: {project.manage_py or 'not found'}",
        f"- settings module: {project.settings_module or 'not found'}",
        f"- apps: {len(project.apps)}",
        f"- Python: {project.python_version or 'unknown'}",
        f"- Django: {project.django_version or 'unknown'}",
        f"- virtualenv: {project.virtualenv or 'not found'}",
        f"- compose files: {', '.join(project.compose_project_files) or 'not found'}",
        f"- compose service: {project.compose_service or 'not found'}",
        f"- compose selection: {compose_selection}",
    ]


def _git_lines(result: AutonomyResult) -> list[str]:
    git = result.final_git
    lines = [
        "",
        "## Git",
        "",
        f"- branch: {git.branch or 'unknown'}",
        f"- HEAD: {git.head or 'unknown'}",
        f"- dirty: {git.dirty}",
        f"- changed files: {len(git.changed_files)}",
    ]
    lines.extend(f"  - {path}" for path in git.changed_files)
    if result.git_audit is not None:
        lines.extend(_git_audit_lines(result.git_audit))
        if result.iteration_audits:
            lines.append(f"- audit passes: {len(result.iteration_audits)}")
        if result.validation_audits:
            lines.append(f"- validator windows: {len(result.validation_audits)}")
    return lines


def _tools_lines(result: AutonomyResult) -> list[str]:
    lines = ["", "## Tools", ""]
    lines.extend(_tool_line(tool) for tool in result.discovery.tools.entries)
    return lines


def _agent_run_lines(result: AutonomyResult) -> list[str]:
    if not result.agent_runs:
        return []
    lines = ["", "## Agent runs", ""]
    lines.extend(
        f"- {run.status.value}: {run.message} (exit {run.exit_code}; "
        f"transport {run.prompt_transport or 'unknown'}; {run.prompt_bytes} bytes)"
        for run in result.agent_runs
    )
    return lines


def _validation_section_lines(result: AutonomyResult) -> list[str]:
    if not result.validation_reports:
        return []
    lines = ["", "## Validations", ""]
    for report in result.validation_reports:
        lines.extend(_validation_lines(report))
    return lines


def _prompt_lines(result: AutonomyResult) -> list[str]:
    if not result.prompt or result.agent_runs:
        return []
    return ["", "## Prompt", "", "```text", _short(result.prompt, 4000), "```"]


def _tool_line(tool: ToolDiscovery) -> str:
    config = ", ".join(tool.config_paths) or "none"
    return f"- **{tool.kind.value}**: {tool.state.value} (config: {config}; {tool.reason})"


def _git_audit_lines(audit: GitAudit) -> list[str]:
    lines = [
        "",
        "## Git audit",
        "",
        f"- baseline HEAD: {audit.baseline.state.head or 'unknown'}",
        f"- final HEAD: {audit.final_state.head or 'unknown'}",
        f"- baseline branch: {audit.baseline.state.branch or 'unknown'}",
        f"- final branch: {audit.final_state.branch or 'unknown'}",
        f"- HEAD changed: {audit.head_changed}",
        f"- branch changed: {audit.branch_changed}",
        f"- committed paths: {len(audit.commit_paths)}",
        f"- execution changes: {len(audit.execution_changes)}",
        f"- validation changes: {len(audit.validation_changes)}",
        f"- preexisting changes: {len(audit.preexisting_changes)}",
        f"- undetermined changes: {len(audit.undetermined_changes)}",
        f"- unexpected paths: {len(audit.unexpected_paths)}",
    ]
    lines.extend(f"  - {change.path} ({change.status}, {change.attribution.value})" for change in audit.changes)
    lines.extend(f"  - committed: {path}" for path in audit.commit_paths)
    lines.extend(f"  - unexpected: {path}" for path in audit.unexpected_paths)
    lines.extend(f"  - warning: {warning}" for warning in audit.warnings)
    return lines


def _validation_lines(report: ValidationReport) -> list[str]:
    lines: list[str] = []
    for tool_result in report.results:
        lines.append(f"- **{tool_result.tool.value}**: {tool_result.status.value} - {tool_result.message}")
        if tool_result.status is ToolStatus.FAIL and tool_result.stderr:
            lines.append(f"  - stderr: {_short(tool_result.stderr, 500)}")
    return lines


def _short(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n...[truncated]"
