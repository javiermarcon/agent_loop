"""Concrete Django-slice tool adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.errors import SecurityError
from agent_loop.autonomy.security import git_environment
from agent_loop.autonomy.security import resolve_system_executable
from agent_loop.autonomy.security import resolve_workspace_executable
from agent_loop.autonomy.tools.base import ToolContext
from agent_loop.autonomy.tools.base import ToolInvocation
from agent_loop.autonomy.tools.base import ToolResult
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.failure import classify_outcome
from agent_loop.autonomy.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agent_loop.autonomy.discovery.tool_commands import DocumentedCommand
    from agent_loop.autonomy.discovery.tools import ToolDiscovery
    from agent_loop.autonomy.tools.command import CommandOutcome
    from agent_loop.autonomy.tools.command import CommandRunner

_PYTEST_NO_TESTS_EXIT_CODE = 5
_MIN_APP_PATH_PARTS = 3
_IMPLEMENTATION_EXIT_CODE = 1
_PENDING_MARKER = re.compile(r"\|\s*NOT IMPLEMENTED\s*\|")
_FAILED_MARKER = re.compile(r"\|\s*(?:FAILED|ERROR|ERRORED)\s*\|")
_PENDING_SUMMARY = re.compile(r"StepNotImplementedError|Not implemented scenarios:")
_FAILED_SUMMARY = re.compile(r"\b([1-9]\d*) failed\b")
_SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "vendor",
    },
)


class GitAdapter:
    """Inspect Git state through read-only commands only."""

    kind = ToolKind.GIT

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        """Run a read-only Git status inspection."""
        discovery = context.inventory.get(self.kind)
        executable = discovery.executable if discovery is not None else None
        resolved = resolve_system_executable(executable) if executable is not None else None
        if resolved is None or context.workspace.is_within(resolved):
            return _unavailable(self.kind, "git executable is not a trusted system binary outside the workspace")
        command = (str(resolved), "status", "--porcelain", "--untracked-files=all")
        return _execute_external(
            context,
            runner,
            self.kind,
            command,
            environment=git_environment(context.environment.variables),
        )


class PreCommitAdapter:
    """Run the target repository's configured pre-commit hooks."""

    kind = ToolKind.PRE_COMMIT

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        """Execute pre-commit with explicit files or the full configured set."""
        files = _safe_changed_files(context)
        if not context.full and not files:
            return _skipped(self.kind, "no changed files to validate")
        discovery = context.inventory.get(self.kind)
        if (
            discovery is not None
            and discovery.container_service is not None
            and not _container_git_available(context, runner, discovery)
        ):
            return _skipped(
                self.kind,
                "pre-commit requires Git metadata that is not available inside the container; "
                "run pre-commit on the host",
            )
        command = _tool_command(context, self.kind, "pre_commit", _precommit_args(context))
        if command is None:
            return _unavailable(self.kind, "target interpreter unavailable")
        return _execute(context, runner, self.kind, command)


class RuffAdapter:
    """Run Ruff using the target project's configuration and interpreter."""

    kind = ToolKind.RUFF

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        """Execute ``ruff check`` on focused paths or the whole project."""
        if context.full:
            paths: tuple[str, ...] = (".",)
        else:
            paths = _python_paths(context)
            if not paths:
                return _skipped(self.kind, "no changed Python files to validate")
        command = _tool_command(context, self.kind, "ruff", ("check", *paths))
        if command is None:
            return _unavailable(self.kind, "target interpreter unavailable")
        return _execute(context, runner, self.kind, command)


class MypyAdapter:
    """Run Mypy using the target project's configuration and interpreter."""

    kind = ToolKind.MYPY

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        """Execute Mypy on focused Python files or the project root."""
        if context.full:
            paths: tuple[str, ...] = (".",)
        else:
            paths = _python_paths(context)
            if not paths:
                return _skipped(self.kind, "no changed Python files to validate")
        command = _tool_command(context, self.kind, "mypy", paths)
        if command is None:
            return _unavailable(self.kind, "target interpreter unavailable")
        return _execute(context, runner, self.kind, command)


class PylintAdapter:
    """Run Pylint on changed Python files only."""

    kind = ToolKind.PYLINT

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        """Execute Pylint on the focused Python file set."""
        paths = _python_paths(context)
        if not paths:
            return _skipped(self.kind, "no changed Python files to validate")
        command = _tool_command(context, self.kind, "pylint", paths)
        if command is None:
            return _unavailable(self.kind, "target interpreter unavailable")
        return _execute(context, runner, self.kind, command)


class BanditAdapter:
    """Run Bandit with the target configuration when available."""

    kind = ToolKind.BANDIT

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        """Execute Bandit on focused paths or the project root."""
        if context.full:
            paths: tuple[str, ...] = (".",)
        else:
            paths = _python_paths(context)
            if not paths:
                return _skipped(self.kind, "no changed Python files to validate")
        args: list[str] = ["-q", "-r", *paths]
        try:
            bandit_config = context.workspace.resolve(".bandit")
        except SecurityError:
            bandit_config = None
        if bandit_config is not None and bandit_config.is_file():
            args.extend(("-c", str(bandit_config)))
        command = _tool_command(context, self.kind, "bandit", tuple(args))
        if command is None:
            return _unavailable(self.kind, "target interpreter unavailable")
        return _execute(context, runner, self.kind, command)


class PytestSelectionKind(StrEnum):
    """How pytest targets were selected."""

    FOCUSED = "focused"
    FULL_SUITE = "full_suite"


@dataclass(frozen=True, slots=True)
class PytestSelection:
    """Deterministic pytest selection with an explicit reason."""

    targets: tuple[str, ...]
    kind: PytestSelectionKind
    reason: str


class PytestAdapter:
    """Run pytest with a focused target set or the project's default suite."""

    kind = ToolKind.PYTEST

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        """Execute pytest in the target environment."""
        selection = select_pytest_selection(context)
        command = _tool_command(context, self.kind, "pytest", selection.targets)
        if command is None:
            return _unavailable(self.kind, "target interpreter unavailable")
        outcome = runner.run(
            command,
            cwd=context.workspace.root,
            env=context.environment.variables,
            timeout_seconds=context.config.command_timeout_seconds,
        )
        result = _result_from_outcome(
            context,
            self.kind,
            command,
            outcome,
            interpreter=context.environment.python_executable,
        )
        if result.status is ToolStatus.FAIL and result.exit_code == _PYTEST_NO_TESTS_EXIT_CODE:
            message = "pytest collected no tests"
            if selection.kind is PytestSelectionKind.FULL_SUITE:
                message = f"{message}; full suite: {selection.reason}"
            return replace(result, status=ToolStatus.SKIPPED, message=message)
        if selection.kind is PytestSelectionKind.FULL_SUITE:
            return replace(result, message=f"{result.message}; full suite: {selection.reason}")
        return result


class BehaveSelectionKind(StrEnum):
    """How Behave targets were selected."""

    FOCUSED = "focused"
    FULL_SUITE = "full_suite"


@dataclass(frozen=True, slots=True)
class BehaveSelection:
    """Deterministic Behave selection with an explicit scope reason."""

    targets: tuple[str, ...]
    kind: BehaveSelectionKind
    reason: str


class BehaveAdapter:
    """Run Behave when the target project configures a features directory."""

    kind = ToolKind.BEHAVE

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        """Execute Behave with focused feature files or the features directory."""
        try:
            features = context.workspace.resolve("features")
        except SecurityError:
            return _skipped(self.kind, "target features directory is outside the authorized workspace")
        if not features.is_dir():
            return _skipped(self.kind, "target project does not use Behave")
        selection = select_behave_selection(context)
        command = _tool_command(context, self.kind, "behave", selection.targets or ("features",))
        if command is None:
            return _unavailable(self.kind, "target interpreter unavailable")
        result = _execute(context, runner, self.kind, command)
        if selection.kind is BehaveSelectionKind.FULL_SUITE:
            result = replace(result, message=f"{result.message}; full suite: {selection.reason}")
        else:
            result = replace(result, message=f"{result.message}; focused: {selection.reason}")
        return _pending_only_as_skipped(result)


def _pending_only_as_skipped(result: ToolResult) -> ToolResult:
    """Treat preexisting not-implemented scenarios as neutral, not code defects."""
    if result.status is not ToolStatus.FAIL or result.exit_code != _IMPLEMENTATION_EXIT_CODE:
        return result
    output = f"{result.stdout}\n{result.stderr}"
    pending = _PENDING_MARKER.search(output) or _PENDING_SUMMARY.search(output)
    failed = _FAILED_MARKER.search(output) or _FAILED_SUMMARY.search(output)
    if not pending or failed:
        return result
    return replace(
        result,
        status=ToolStatus.SKIPPED,
        message=f"{result.message}; pending scenarios are preexisting and not implementation failures",
    )


class DockerComposeAdapter:
    """Validate Docker Compose configuration without starting services."""

    kind = ToolKind.DOCKER_COMPOSE

    def run(self, context: ToolContext, runner: CommandRunner) -> ToolResult:
        """Run the discovered Compose command against the selected compose files."""
        files = context.project.compose_project_files or context.project.compose_files
        if context.project.compose_error:
            return _unavailable(self.kind, context.project.compose_error)
        if not files:
            return _skipped(self.kind, "target project has no Docker Compose file")
        discovery = context.inventory.get(self.kind)
        if discovery is None or not discovery.command_prefix:
            return _unavailable(self.kind, "Docker Compose command was not discovered")
        executable = resolve_system_executable(discovery.command_prefix[0])
        if executable is None or context.workspace.is_within(executable):
            return _unavailable(
                self.kind,
                "Docker Compose executable is not a trusted system binary outside the workspace",
            )
        command_parts: list[str] = [str(executable), *discovery.command_prefix[1:]]
        for compose_file in files:
            command_parts.extend(("-f", compose_file))
        command_parts.extend(("config", "--quiet"))
        return _execute_external(context, runner, self.kind, tuple(command_parts))


def default_registry() -> ToolRegistry:
    """Build the default Django-slice adapter registry."""
    registry = ToolRegistry()
    for adapter in (
        GitAdapter(),
        PreCommitAdapter(),
        RuffAdapter(),
        MypyAdapter(),
        PylintAdapter(),
        BanditAdapter(),
        PytestAdapter(),
        BehaveAdapter(),
        DockerComposeAdapter(),
    ):
        registry.register(adapter)
    return registry


@dataclass(frozen=True, slots=True)
class _TestMatch:
    targets: tuple[str, ...]
    ambiguous: bool = False


def select_pytest_selection(context: ToolContext) -> PytestSelection:
    """Select pytest targets with an explicit reason and ambiguity policy."""
    if context.full:
        return PytestSelection((), PytestSelectionKind.FULL_SUITE, "full validation requested")
    changed = _python_paths(context)
    if not changed:
        return PytestSelection((), PytestSelectionKind.FULL_SUITE, "no changed Python files")
    targets: set[str] = set()
    for relative in changed:
        if _is_test_file(relative):
            targets.add(relative)
            continue
        match = _test_matches(context, relative)
        if match.ambiguous:
            return PytestSelection((), PytestSelectionKind.FULL_SUITE, f"ambiguous test mapping for {relative}")
        if not match.targets:
            return PytestSelection((), PytestSelectionKind.FULL_SUITE, f"no deterministic test mapping for {relative}")
        targets.update(match.targets)
    if not targets:
        return PytestSelection((), PytestSelectionKind.FULL_SUITE, "no test targets selected")
    return PytestSelection(tuple(sorted(targets)), PytestSelectionKind.FOCUSED, "focused test targets")


def select_pytest_targets(context: ToolContext) -> tuple[str, ...]:
    """Return only the selected pytest targets for compatibility."""
    return select_pytest_selection(context).targets


def _is_test_file(relative: str) -> bool:
    name = Path(relative).name
    return name.startswith("test_") or name.endswith("_test.py")


def _test_matches(context: ToolContext, relative: str) -> _TestMatch:
    changed = Path(relative)
    stem = changed.stem
    app_root = _app_root(changed)
    if app_root is not None:
        # A global homonym is not evidence that the test belongs to this app.
        # Only app-local tests provide a deterministic association.
        return _TestMatch(tuple(sorted(_matches_under(context, app_root, stem))))
    roots = _candidate_test_roots(context, changed)
    matches: set[str] = set()
    for root in roots:
        matches.update(_matches_under(context, root, stem))
    if len(matches) > 1:
        return _TestMatch((), ambiguous=True)
    return _TestMatch(tuple(sorted(matches)))


def _candidate_test_roots(context: ToolContext, changed: Path) -> tuple[Path, ...]:
    roots: set[Path] = {Path("tests"), changed.parent}
    for value in context.project.pytest_testpaths:
        normalized = _normalize_test_root(context, value)
        if normalized is not None:
            roots.add(normalized)
    return tuple(sorted(roots))


def _normalize_test_root(context: ToolContext, value: str) -> Path | None:
    path = Path(value)
    if path.is_absolute():
        try:
            return path.relative_to(context.workspace.root)
        except ValueError:
            return None
    if ".." in path.parts:
        return None
    return path


def _app_root(changed: Path) -> Path | None:
    parts = changed.parts
    if len(parts) >= _MIN_APP_PATH_PARTS and parts[0] == "apps":
        return Path(parts[0]) / parts[1]
    return None


def _matches_under(context: ToolContext, root: Path, stem: str) -> set[str]:
    matches: set[str] = set()
    for path in context.workspace.iter_files(skip_directories=_SKIP_DIRECTORIES):
        if path.name not in {f"test_{stem}.py", f"{stem}_test.py"}:
            continue
        candidate = path.relative_to(context.workspace.root)
        if _is_under(candidate, root):
            matches.add(candidate.as_posix())
    try:
        tests_py = context.workspace.resolve(context.workspace.root / root / "tests.py")
    except SecurityError:
        return matches
    if tests_py.is_file():
        matches.add(tests_py.relative_to(context.workspace.root).as_posix())
    return matches


def _is_under(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def select_behave_selection(context: ToolContext) -> BehaveSelection:
    """Select Behave targets and report whether the full suite will run."""
    if context.full:
        return BehaveSelection((), BehaveSelectionKind.FULL_SUITE, "full validation requested")
    features: list[str] = []
    missing: list[str] = []
    for path in context.changed_files:
        if not path.startswith("features/") or not path.endswith(".feature"):
            continue
        try:
            candidate = context.workspace.resolve(path)
        except SecurityError:
            missing.append(path)
            continue
        if candidate.is_file():
            features.append(_safe_argument(path))
        else:
            missing.append(path)
    if features:
        return BehaveSelection(
            tuple(sorted(features)),
            BehaveSelectionKind.FOCUSED,
            "changed feature files",
        )
    reason = "no changed feature files"
    if missing:
        reason = f"changed feature files are missing: {', '.join(sorted(missing))}"
    return BehaveSelection((), BehaveSelectionKind.FULL_SUITE, reason)


def _precommit_args(context: ToolContext) -> tuple[str, ...]:
    files = _safe_changed_files(context)
    if context.full or not files:
        return ("run", "--all-files")
    return ("run", "--files", *files)


def _container_git_available(
    context: ToolContext,
    runner: CommandRunner,
    discovery: ToolDiscovery,
) -> bool:
    """Check read-only whether Git metadata is reachable inside the container."""
    outcome = runner.run(
        (*discovery.command_prefix, "git", "rev-parse", "--is-inside-work-tree"),
        cwd=context.workspace.root,
        env=context.environment.variables,
        timeout_seconds=context.config.command_timeout_seconds,
    )
    return outcome.error is None and outcome.exit_code == 0


def _tool_command(
    context: ToolContext,
    kind: ToolKind,
    module: str,
    args: Sequence[str],
) -> tuple[str, ...] | None:
    discovery = context.inventory.get(kind)
    if discovery is None or not discovery.is_available:
        return None
    if discovery.container_service is not None and discovery.command_prefix:
        return _container_tool_command(context, discovery, kind, module, args)
    return _local_tool_command(context, discovery, module, args)


def _local_tool_command(
    context: ToolContext,
    discovery: ToolDiscovery,
    module: str,
    args: Sequence[str],
) -> tuple[str, ...] | None:
    python = context.environment.python_executable
    executable = Path(discovery.executable) if discovery.executable else None
    if executable is not None and python is not None and executable == python:
        return (str(python), "-m", module, *args)
    if executable is not None:
        candidate = _validated_tool_executable(context, executable)
        if candidate is None:
            return None
        return (str(candidate), *args)
    if python is None:
        return None
    return (str(python), "-m", module, *args)


def _container_tool_command(
    context: ToolContext,
    discovery: ToolDiscovery,
    kind: ToolKind,
    module: str,
    args: Sequence[str],
) -> tuple[str, ...]:
    documented = _documented_command(context, kind)
    if documented is not None:
        if kind is ToolKind.MYPY:
            return (*discovery.command_prefix, *documented.args)
        return (*discovery.command_prefix, *documented.args, *args)
    executable = discovery.executable or "python"
    if executable == "python":
        return (*discovery.command_prefix, executable, "-m", module, *args)
    return (*discovery.command_prefix, executable, *args)


def _documented_command(context: ToolContext, kind: ToolKind) -> DocumentedCommand | None:
    for command in context.project.documented_commands:
        if command.tool == kind.value:
            return command
    return None


def _validated_tool_executable(context: ToolContext, executable: Path) -> Path | None:
    try:
        context.workspace.member_path(executable)
    except SecurityError:
        return resolve_system_executable(executable)
    return resolve_workspace_executable(context.workspace, executable)


def _python_paths(context: ToolContext) -> tuple[str, ...]:
    return tuple(path for path in _safe_changed_files(context) if path.endswith(".py"))


def _safe_changed_files(context: ToolContext) -> tuple[str, ...]:
    paths: list[str] = []
    for relative in context.changed_files:
        try:
            candidate = context.workspace.resolve(relative)
            normalized = context.workspace.relative(candidate)
        except (SecurityError, ValueError):
            continue
        if candidate.is_file():
            paths.append(_safe_argument(normalized))
    return tuple(sorted(dict.fromkeys(paths)))


def _safe_argument(path: str) -> str:
    """Return one argv path that cannot be interpreted as a command option."""
    if "\x00" in path:
        message = f"path contains a NUL byte: {path!r}"
        raise SecurityError(message)
    if path.startswith("-"):
        return f"./{path}"
    return path


def _execute(
    context: ToolContext,
    runner: CommandRunner,
    kind: ToolKind,
    command: tuple[str, ...],
) -> ToolResult:
    outcome = runner.run(
        command,
        cwd=context.workspace.root,
        env=context.environment.variables,
        timeout_seconds=context.config.command_timeout_seconds,
    )
    return _result_from_outcome(context, kind, command, outcome, interpreter=context.environment.python_executable)


def _execute_external(
    context: ToolContext,
    runner: CommandRunner,
    kind: ToolKind,
    command: tuple[str, ...],
    *,
    environment: dict[str, str] | None = None,
) -> ToolResult:
    outcome = runner.run(
        command,
        cwd=context.workspace.root,
        env=environment or context.environment.variables,
        timeout_seconds=context.config.command_timeout_seconds,
    )
    result = _result_from_outcome(context, kind, command, outcome, interpreter=None)
    if environment is not None and result.invocation is not None:
        return replace(
            result,
            invocation=replace(result.invocation, environment_keys=tuple(sorted(environment))),
        )
    return result


def _result_from_outcome(
    context: ToolContext,
    kind: ToolKind,
    command: tuple[str, ...],
    outcome: CommandOutcome,
    *,
    interpreter: Path | None,
) -> ToolResult:
    invocation = ToolInvocation(
        tool=kind,
        command=command,
        cwd=str(context.workspace.root),
        interpreter=str(interpreter) if interpreter is not None else None,
        environment_keys=tuple(sorted(context.environment.variables)),
    )
    failure_kind = classify_outcome(kind, outcome)
    if outcome.error is not None:
        status = ToolStatus.UNKNOWN if outcome.timed_out else ToolStatus.UNAVAILABLE
        return ToolResult(
            tool=kind,
            status=status,
            message=outcome.error,
            invocation=invocation,
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            duration_seconds=outcome.duration_seconds,
            failure_kind=failure_kind,
        )
    status = ToolStatus.PASS if outcome.exit_code == 0 else ToolStatus.FAIL
    return ToolResult(
        tool=kind,
        status=status,
        message=f"{kind.value} exited with code {outcome.exit_code}",
        invocation=invocation,
        exit_code=outcome.exit_code,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        duration_seconds=outcome.duration_seconds,
        failure_kind=failure_kind,
    )


def _skipped(kind: ToolKind, message: str) -> ToolResult:
    return ToolResult(tool=kind, status=ToolStatus.SKIPPED, message=message)


def _unavailable(kind: ToolKind, message: str) -> ToolResult:
    return ToolResult(tool=kind, status=ToolStatus.UNAVAILABLE, message=message)
