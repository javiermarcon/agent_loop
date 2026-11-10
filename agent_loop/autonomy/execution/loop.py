"""Autonomous discovery, agent invocation and validation loop."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.discovery.git import capture_git_baseline
from agent_loop.autonomy.discovery.git import discover_git
from agent_loop.autonomy.discovery.git_attribution import GitAudit
from agent_loop.autonomy.discovery.git_attribution import GitBaseline
from agent_loop.autonomy.discovery.git_attribution import audit_git_changes
from agent_loop.autonomy.discovery.git_attribution import merge_window_attribution
from agent_loop.autonomy.discovery.instructions import discover_instructions
from agent_loop.autonomy.discovery.tools import ModuleProbe
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.discovery.tools import discover_tools
from agent_loop.autonomy.errors import ConfigurationError
from agent_loop.autonomy.errors import DiscoveryError
from agent_loop.autonomy.execution.agent import AgentInvoker
from agent_loop.autonomy.execution.agent import AgentRunResult
from agent_loop.autonomy.execution.agent import CommandAgentInvoker
from agent_loop.autonomy.execution.environment import resolve_environment
from agent_loop.autonomy.execution.runner import RetryDecision
from agent_loop.autonomy.execution.runner import ValidationReport
from agent_loop.autonomy.execution.runner import ValidationRunner
from agent_loop.autonomy.report import AutonomyResult
from agent_loop.autonomy.report import DiscoveryReport
from agent_loop.autonomy.report import to_json
from agent_loop.autonomy.requirement import RequirementSource
from agent_loop.autonomy.telemetry import AutonomyTelemetry
from agent_loop.autonomy.tools.adapters import default_registry
from agent_loop.autonomy.tools.base import ToolContext
from agent_loop.autonomy.tools.base import ToolResult
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import CommandRunner
from agent_loop.autonomy.tools.command import SubprocessCommandRunner

if TYPE_CHECKING:
    from pathlib import Path

    from agent_loop.autonomy.config import AutonomyConfig
    from agent_loop.autonomy.discovery.django_project import DjangoProjectInfo
    from agent_loop.autonomy.security import TargetWorkspace
    from agent_loop.autonomy.tools.registry import ToolRegistry
    from agent_loop.ports.telemetry import TelemetrySink


@dataclass(frozen=True, slots=True)
class AutonomyRequest:
    """Inputs for one autonomous invocation."""

    workspace: TargetWorkspace
    requirement: str
    config: AutonomyConfig
    tool_kinds: tuple[ToolKind, ...] | None = None
    full_validation: bool = False
    dry_run: bool = False
    discover_only: bool = False
    validate_only: bool = False
    target_paths: tuple[str, ...] = ()
    requirement_source: RequirementSource | None = None
    execution_id: str | None = None
    telemetry: TelemetrySink | None = None

    def __post_init__(self) -> None:
        if not self.requirement.strip():
            message = "requirement must be non-empty"
            raise ConfigurationError(message)
        source = self.requirement_source or RequirementSource.inline(self.requirement)
        if not source.text.strip():
            message = "requirement source must be non-empty"
            raise ConfigurationError(message)
        if source.text != self.requirement:
            message = "requirement text does not match its recorded source"
            raise ConfigurationError(message)
        object.__setattr__(self, "requirement_source", source)
        normalized = _normalize_target_paths(self.workspace, self.target_paths)
        object.__setattr__(self, "target_paths", normalized)


@dataclass(frozen=True, slots=True)
class _CycleSetup:
    """Immutable discovery/context inputs shared by one agent cycle."""

    discovery: DiscoveryReport
    context: ToolContext
    prompt: str
    baseline: GitBaseline


@dataclass(slots=True)
class _CycleState:
    """Mutable bookkeeping for one bounded agent/validation cycle."""

    context: ToolContext
    prompt: str
    window_baseline: GitBaseline
    agent_runs: list[AgentRunResult] = field(default_factory=list)
    validation_reports: list[ValidationReport] = field(default_factory=list)
    iteration_audits: list[GitAudit] = field(default_factory=list)
    agent_window_audits: list[GitAudit] = field(default_factory=list)
    validation_audits: list[GitAudit] = field(default_factory=list)
    status: ToolStatus = ToolStatus.UNKNOWN
    validation_status: ToolStatus | None = None
    message: str = "agent cycle did not complete"


class AutonomyLoop:
    """Coordinate discovery, external agent execution and validation."""

    def __init__(
        self,
        config: AutonomyConfig,
        *,
        registry: ToolRegistry | None = None,
        command_runner: CommandRunner | None = None,
        agent_invoker: AgentInvoker | None = None,
        probe: ModuleProbe | None = None,
    ) -> None:
        self._config = config
        self._runner = command_runner or SubprocessCommandRunner(max_output_chars=config.max_output_chars)
        self._registry = registry or default_registry()
        self._agent = agent_invoker or CommandAgentInvoker(
            config.agent_command,
            self._runner,
            transport=config.agent_prompt_transport,
        )
        self._validation = ValidationRunner(self._registry, self._runner)
        self._probe = probe

    def run(self, request: AutonomyRequest) -> AutonomyResult:
        """Run discovery and, unless disabled, the agent/validation cycle."""
        telemetry = self._telemetry_for(request)
        if telemetry is not None:
            origin = "unspecified" if request.requirement_source is None else request.requirement_source.origin.value
            telemetry.run_received(origin)
        discovery = self._discover(request.workspace)
        context = self._build_context(request, discovery)
        if request.discover_only:
            return self._finish(request, self._discover_only_result(discovery), telemetry)
        if not discovery.project.is_django:
            message = "target repository does not look like a Django project"
            raise DiscoveryError(message)
        prompt = build_prompt(request, discovery)
        if request.dry_run:
            return self._finish(request, self._dry_run_result(discovery, prompt), telemetry)
        if request.validate_only:
            result = self._validate_only_result(
                request,
                discovery,
                context,
                prompt,
                telemetry=telemetry,
            )
            return self._finish(request, result, telemetry)
        if not request.config.agent_command:
            return self._finish(request, self._missing_agent_result(discovery, prompt), telemetry)
        baseline = capture_git_baseline(request.workspace)
        setup = _CycleSetup(
            discovery=discovery,
            context=context,
            prompt=prompt,
            baseline=baseline,
        )
        return self._finish(request, self._run_agent_cycle(request, setup, telemetry), telemetry)

    def _telemetry_for(self, request: AutonomyRequest) -> AutonomyTelemetry | None:
        if request.telemetry is None:
            return None
        return AutonomyTelemetry(
            request.telemetry,
            target_repository=str(request.workspace.root),
            execution_id=request.execution_id,
        )

    def _build_context(self, request: AutonomyRequest, discovery: DiscoveryReport) -> ToolContext:
        environment = resolve_environment(request.workspace, discovery.project, request.config)
        return ToolContext(
            workspace=request.workspace,
            project=discovery.project,
            environment=environment,
            inventory=discovery.tools,
            changed_files=discovery.git.changed_files,
            full=request.full_validation,
            config=request.config,
        )

    def _discover_only_result(self, discovery: DiscoveryReport) -> AutonomyResult:
        return AutonomyResult(
            discovery=discovery,
            prompt="",
            agent_runs=(),
            validation_reports=(),
            final_git=discovery.git,
            status=ToolStatus.PASS,
            validation_status=None,
            message="discovery complete",
        )

    def _dry_run_result(self, discovery: DiscoveryReport, prompt: str) -> AutonomyResult:
        return AutonomyResult(
            discovery=discovery,
            prompt=prompt,
            agent_runs=(),
            validation_reports=(),
            final_git=discovery.git,
            status=ToolStatus.PASS,
            validation_status=None,
            message="dry run: no agent executed",
        )

    def _missing_agent_result(self, discovery: DiscoveryReport, prompt: str) -> AutonomyResult:
        return AutonomyResult(
            discovery=discovery,
            prompt=prompt,
            agent_runs=(),
            validation_reports=(),
            final_git=discovery.git,
            status=ToolStatus.UNAVAILABLE,
            validation_status=None,
            message="no agent command configured; use --agent-command or --validate-only",
        )

    def _validate_only_result(
        self,
        request: AutonomyRequest,
        discovery: DiscoveryReport,
        context: ToolContext,
        prompt: str,
        *,
        telemetry: AutonomyTelemetry | None,
    ) -> AutonomyResult:
        baseline = capture_git_baseline(request.workspace)
        report = self._validation.run(context, request.tool_kinds)
        _record_validation(telemetry, 1, report)
        final_git = discover_git(request.workspace)
        audit = audit_git_changes(request.workspace, baseline, final_git, scope_paths=request.target_paths)
        audit = merge_window_attribution(audit, validation_audits=(audit,))
        if telemetry is not None:
            telemetry.changes_detected(audit)
        status, guard_message = _apply_git_audit_guard(_overall_status((), (report,)), audit)
        message = _validation_message(report)
        if guard_message is not None:
            message = f"{message}; {guard_message}"
        return AutonomyResult(
            discovery=discovery,
            prompt=prompt,
            agent_runs=(),
            validation_reports=(report,),
            final_git=final_git,
            status=status,
            validation_status=report.status,
            message=message,
            git_audit=audit,
            validation_audits=(audit,),
        )

    def _run_agent_cycle(
        self,
        request: AutonomyRequest,
        setup: _CycleSetup,
        telemetry: AutonomyTelemetry | None,
    ) -> AutonomyResult:
        state = _CycleState(context=setup.context, prompt=setup.prompt, window_baseline=setup.baseline)
        for iteration in range(request.config.max_iterations):
            if not self._run_iteration(request, setup.baseline, state, iteration, telemetry=telemetry):
                break
        return self._finalize_cycle(request, setup, state, telemetry)

    def _run_iteration(
        self,
        request: AutonomyRequest,
        baseline: GitBaseline,
        state: _CycleState,
        iteration: int,
        *,
        telemetry: AutonomyTelemetry | None,
    ) -> bool:
        if telemetry is not None:
            telemetry.agent_started(iteration + 1)
        run = self._agent.invoke(state.prompt, state.context)
        if telemetry is not None:
            telemetry.agent_finished(iteration + 1, run)
        state.agent_runs.append(run)
        after_agent = discover_git(request.workspace)
        state.iteration_audits.append(
            audit_git_changes(request.workspace, baseline, after_agent, scope_paths=request.target_paths),
        )
        state.agent_window_audits.append(
            audit_git_changes(
                request.workspace,
                state.window_baseline,
                after_agent,
                scope_paths=request.target_paths,
            ),
        )
        state.context = replace(state.context, changed_files=after_agent.changed_files)
        if not run.succeeded:
            state.status = run.status if run.status is not ToolStatus.PASS else ToolStatus.FAIL
            state.message = f"agent invocation failed on iteration {iteration + 1}: {run.message}"
            return False
        return self._run_validation_iteration(request, state, iteration, telemetry)

    def _run_validation_iteration(
        self,
        request: AutonomyRequest,
        state: _CycleState,
        iteration: int,
        telemetry: AutonomyTelemetry | None,
    ) -> bool:
        validation_baseline = capture_git_baseline(request.workspace)
        report = self._validation.run(state.context, request.tool_kinds)
        _record_validation(telemetry, iteration + 1, report)
        state.validation_reports.append(report)
        after_validation = discover_git(request.workspace)
        state.validation_audits.append(
            audit_git_changes(
                request.workspace,
                validation_baseline,
                after_validation,
                scope_paths=request.target_paths,
            ),
        )
        state.context = replace(state.context, changed_files=after_validation.changed_files)
        state.window_baseline = capture_git_baseline(request.workspace)
        state.status = report.status
        state.validation_status = report.status
        decision = report.retry_decision
        if decision is RetryDecision.STOP_SUCCESS:
            state.message = f"validation passed in {iteration + 1} iteration(s)"
            return False
        if decision is not RetryDecision.RETRY_FAILURE:
            state.message = f"validation stopped with {decision.value} on iteration {iteration + 1}"
            return False
        state.message = f"validation failed on iteration {iteration + 1}"
        if iteration + 1 >= request.config.max_iterations:
            return False
        state.prompt = build_correction_prompt(request, report, iteration + 2)
        return True

    def _finalize_cycle(
        self,
        request: AutonomyRequest,
        setup: _CycleSetup,
        state: _CycleState,
        telemetry: AutonomyTelemetry | None,
    ) -> AutonomyResult:
        final_git = discover_git(request.workspace)
        audit = audit_git_changes(request.workspace, setup.baseline, final_git, scope_paths=request.target_paths)
        audit = merge_window_attribution(
            audit,
            agent_audits=tuple(state.agent_window_audits),
            validation_audits=tuple(state.validation_audits),
        )
        if telemetry is not None:
            telemetry.changes_detected(audit)
        status, guard_message = _apply_git_audit_guard(state.status, audit)
        message = state.message
        if guard_message is not None:
            message = f"{message}; {guard_message}"
        return AutonomyResult(
            discovery=setup.discovery,
            prompt=setup.prompt,
            agent_runs=tuple(state.agent_runs),
            validation_reports=tuple(state.validation_reports),
            final_git=final_git,
            status=status,
            validation_status=state.validation_status,
            message=message,
            git_audit=audit,
            iteration_audits=tuple(state.iteration_audits),
            agent_window_audits=tuple(state.agent_window_audits),
            validation_audits=tuple(state.validation_audits),
        )

    def _discover(self, workspace: TargetWorkspace) -> DiscoveryReport:
        instructions = discover_instructions(workspace)
        project = discover_django_project(workspace)
        tools = discover_tools(workspace, project, probe=self._probe)
        git = discover_git(workspace)
        return DiscoveryReport(instructions=instructions, project=project, tools=tools, git=git)

    def _finish(
        self,
        request: AutonomyRequest,
        result: AutonomyResult,
        telemetry: AutonomyTelemetry | None,
    ) -> AutonomyResult:
        if result.requirement_source is None:
            result = replace(result, requirement_source=request.requirement_source)
        if telemetry is not None:
            telemetry.run_closed(result)
        if self._config.log_dir is not None:
            _persist(self._config.log_dir, result)
        return result


def _record_validation(
    telemetry: AutonomyTelemetry | None,
    attempt: int,
    report: ValidationReport,
) -> None:
    if telemetry is None:
        return
    for result in report.results:
        telemetry.tool_finished(attempt, result)
    telemetry.quality_finished(attempt, report)


def _normalize_target_paths(workspace: TargetWorkspace, paths: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in paths:
        text = str(raw).strip()
        if not text or text == ".":
            continue
        relative = workspace.relative(text)
        if relative in {"", "."}:
            continue
        normalized.append(relative)
    return tuple(dict.fromkeys(normalized))


def _apply_git_audit_guard(status: ToolStatus, audit: GitAudit) -> tuple[ToolStatus, str | None]:
    reasons: list[str] = []
    if audit.baseline.state.error:
        reasons.append(f"baseline Git audit failed: {audit.baseline.state.error}")
    if audit.final_state.error:
        reasons.append(f"final Git audit failed: {audit.final_state.error}")
    if audit.head_changed or audit.branch_changed or audit.warnings:
        detail = "; ".join(audit.warnings) or "Git history changed without attributable evidence"
        reasons.append(detail)
    if audit.unexpected_paths:
        reasons.append(f"execution changed paths outside the authorized scope: {', '.join(audit.unexpected_paths)}")
    if audit.undetermined_changes:
        paths = ", ".join(change.path for change in audit.undetermined_changes)
        reasons.append(f"change attribution is undetermined for: {paths}")
    if not reasons:
        return status, None
    if status is ToolStatus.PASS:
        status = ToolStatus.UNKNOWN
    return status, f"git attribution requires review: {'; '.join(reasons)}"


def build_prompt(request: AutonomyRequest, discovery: DiscoveryReport) -> str:
    """Build the initial implementation prompt from discovered context."""
    instructions = discovery.instructions.render(request.target_paths)
    tools = _render_tools(discovery)
    project = discovery.project
    return (
        "# Agent Loop autonomous task\n\n"
        "You are an autonomous coding agent operating on an external Django repository.\n\n"
        "## Requirement\n\n"
        f"{request.requirement.strip()}\n\n"
        f"Requirement source: {request.requirement_source.label if request.requirement_source else 'unspecified'}\n\n"
        "## Target repository\n\n"
        f"- Root: {request.workspace.root}\n"
        f"- Django: {project.django_version or 'unknown'}\n"
        f"- Python: {project.python_version or 'unknown'}\n"
        f"- manage.py: {project.manage_py or 'not found'}\n"
        f"- settings module: {project.settings_module or 'not resolved'}\n"
        f"- settings candidates: {', '.join(project.settings_candidates) or 'none'}\n"
        f"- settings evidence: {project.settings_reason or 'resolved deterministically'}\n"
        f"- apps: {', '.join(project.apps) or 'not detected'}\n"
        f"- local apps: {', '.join(project.local_apps) or 'none'}\n"
        f"- third-party apps: {', '.join(project.third_party_apps) or 'none'}\n"
        f"- framework apps: {', '.join(project.framework_apps) or 'none'}\n"
        f"- virtualenv: {project.virtualenv or 'not detected'}\n"
        f"- Docker Compose files: {', '.join(project.compose_project_files) or 'not configured'}\n"
        f"- Docker Compose selection: {project.compose_reason or project.compose_error or 'none'}\n"
        f"- Docker Compose service: {project.compose_service or 'not resolved'}\n"
        f"- Docker service evidence: {project.compose_service_reason or 'none'}\n\n"
        "## Target execution environment\n\n"
        f"{_render_execution_environment(project)}\n\n"
        "## Applicable repository instructions\n\n"
        f"{instructions or 'No repository instructions were discovered.'}\n\n"
        "## Tooling discovered in the target\n\n"
        f"{tools}\n\n"
        "## Execution rules\n\n"
        "- Repository instructions govern development style and architecture, but Agent Loop "
        "security rules below always take precedence.\n"
        "- Modify only files inside the target repository root.\n"
        "- Do not run destructive Git operations (commit, push, reset, checkout, restore, clean).\n"
        "- Do not overwrite unrelated pre-existing changes.\n"
        "- Use the target project's documented execution environment and commands. Never use Agent "
        "Loop's interpreter or global tool versions.\n"
        "- If the project runs tools inside Docker Compose, use the discovered service and the "
        "official project commands; do not start production stacks.\n"
        "- Implement the requirement, run focused validations, and report what you changed.\n"
        "- First produce a short implementation plan, then execute it.\n"
        "- If a tool is unavailable, report it explicitly; never present it as a successful validation.\n"
        "- Do not create commits or change branches.\n\n"
        "## Expected output\n\n"
        "Return a concise summary of the changes, the validations you ran, and any unresolved risk.\n"
    )


def _render_execution_environment(project: DjangoProjectInfo) -> str:
    lines: list[str] = []
    if project.virtualenv:
        lines.append(f"- Local virtualenv: {project.virtualenv} ({project.python_executable})")
    if project.compose_service and project.compose_project_files:
        files = " ".join(f"-f {name}" for name in project.compose_project_files)
        lines.append(f"- Container command prefix: docker compose {files} exec -T {project.compose_service}")
    if not lines:
        lines.append("- No validated target execution environment was discovered.")
    for command in project.documented_commands:
        rendered = " ".join(('"' + part + '"') if " " in part else part for part in command.args)
        lines.append(f"- Official {command.tool} command ({command.source}): {rendered}")
    return "\n".join(lines)


def build_correction_prompt(request: AutonomyRequest, report: ValidationReport, iteration: int) -> str:
    """Build a bounded correction prompt from actionable validation failures."""
    failures = report.actionable_failures
    non_actionable = report.non_actionable_results
    blocks = [_render_failure(result) for result in failures]
    joined = "\n\n".join(blocks) or "No detailed failure output was captured."
    non_actionable_text = _render_non_actionable(non_actionable)
    return (
        f"# Agent Loop correction iteration {iteration}\n\n"
        "The previous implementation did not pass the target project's validations.\n\n"
        "## Original requirement\n\n"
        f"{request.requirement.strip()}\n\n"
        f"Requirement source: {request.requirement_source.label if request.requirement_source else 'unspecified'}\n\n"
        "## Failed validations\n\n"
        f"{joined}\n\n"
        "## Non-actionable results\n\n"
        f"{non_actionable_text}\n\n"
        "## Rules\n\n"
        "- Fix only the proven failures above and keep the change inside the target repository.\n"
        "- Do not treat skipped, unavailable or unknown results as implementation failures.\n"
        "- Do not retry usage, configuration, infrastructure or execution errors as if they were code defects.\n"
        "- Preserve the original diagnostic instead of masking an environment failure.\n"
        "- Do not commit, push, reset, checkout, restore or clean.\n"
        "- Re-run the relevant validations and report the result.\n"
    )


def _render_tools(discovery: DiscoveryReport) -> str:
    lines: list[str] = []
    for tool in discovery.tools.entries:
        config = ", ".join(tool.config_paths) or "none"
        container = f"; container: {tool.container_service}" if tool.container_service else ""
        lines.append(f"- {tool.kind.value}: {tool.state.value} (config: {config}{container}; {tool.reason})")
    return "\n".join(lines)


def _render_failure(result: ToolResult) -> str:
    output = result.stderr or result.stdout
    return f"### {result.tool.value}: {result.status.value}\n{result.message}\n```text\n{_short(output, 3000)}\n```"


def _render_non_actionable(results: tuple[ToolResult, ...]) -> str:
    if not results:
        return "None."
    lines: list[str] = []
    for result in results:
        lines.append(f"- {result.tool.value}: {result.status.value} - {result.message}")
        output = result.stderr or result.stdout
        if result.status is ToolStatus.FAIL and output:
            lines.append(f"  ```text\n{_short(output, 1000)}\n  ```")
    return "\n".join(lines)


def _short(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n...[truncated]"


def _overall_status(
    agent_runs: tuple[AgentRunResult, ...],
    validation_reports: tuple[ValidationReport, ...],
) -> ToolStatus:
    statuses = {run.status for run in agent_runs}
    if ToolStatus.FAIL in statuses:
        return ToolStatus.FAIL
    if ToolStatus.UNKNOWN in statuses:
        return ToolStatus.UNKNOWN
    if ToolStatus.UNAVAILABLE in statuses:
        return ToolStatus.UNAVAILABLE
    if not validation_reports:
        return ToolStatus.UNKNOWN
    return validation_reports[-1].status


def _validation_message(report: ValidationReport) -> str:
    if report.status is ToolStatus.PASS:
        return "validation passed"
    if report.status is ToolStatus.SKIPPED:
        return "validation skipped; no effective evidence"
    return f"validation status: {report.status.value}"


def _persist(log_dir: Path, result: AutonomyResult) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = log_dir / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(result.prompt, encoding="utf-8")
    (run_dir / "result.json").write_text(to_json(result), encoding="utf-8")
