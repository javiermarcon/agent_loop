"""Command-line entrypoint for the autonomous agent-loop runtime."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from agent_loop.autonomy import __version__
from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.config import PromptTransport
from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.errors import AutonomyError
from agent_loop.autonomy.errors import ConfigurationError
from agent_loop.autonomy.errors import DiscoveryError
from agent_loop.autonomy.errors import SecurityError
from agent_loop.autonomy.execution.loop import AutonomyLoop
from agent_loop.autonomy.execution.loop import AutonomyRequest
from agent_loop.autonomy.report import AutonomyResult
from agent_loop.autonomy.report import render_markdown
from agent_loop.autonomy.report import to_json
from agent_loop.autonomy.requirement import RequirementSource
from agent_loop.autonomy.security import TargetWorkspace
from agent_loop.autonomy.tools.base import ToolStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_VALIDATION_FAILED = 4


def build_parser() -> argparse.ArgumentParser:
    """Build the autonomous CLI parser without consuming process argv."""
    parser = argparse.ArgumentParser(
        prog="agent-loop",
        description="Run AgentLoop autonomously against an external software repository.",
    )
    parser.add_argument("--repo", required=True, help="Path to the target Git repository.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--requirement", help="Development requirement inline.")
    source.add_argument("--requirement-file", help="UTF-8 file containing the requirement.")
    parser.add_argument(
        "--agent-command",
        help=(
            "Explicit agent command. The prompt is appended as the last argument "
            "only when --prompt-transport selects argument delivery."
        ),
    )
    parser.add_argument(
        "--prompt-transport",
        choices=[transport.value for transport in PromptTransport],
        default=PromptTransport.AUTO.value,
        help=(
            "Prompt delivery: auto (stdin for large prompts), stdin (stream through a pipe), "
            "or argument (append as the last argv entry)."
        ),
    )
    parser.add_argument("--max-iterations", type=int, default=3, help="Maximum agent/validation cycles.")
    parser.add_argument("--timeout-seconds", type=int, default=60 * 60, help="Per-command timeout.")
    parser.add_argument("--max-output-chars", type=int, default=20_000, help="Captured output limit per stream.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Discover and print the prompt without executing.")
    mode.add_argument("--discover-only", action="store_true", help="Discover the target and print a report.")
    mode.add_argument("--validate-only", action="store_true", help="Run validations without invoking an agent.")
    parser.add_argument(
        "--tool",
        action="append",
        choices=[kind.value for kind in ToolKind],
        help="Limit validations to a tool; may be repeated.",
    )
    parser.add_argument("--full", action="store_true", help="Run full validations instead of focused ones.")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    parser.add_argument("--output", help="Write the report to this path.")
    parser.add_argument(
        "--target-path",
        action="append",
        default=[],
        help="Repository path for instruction scope and Git audit scope; may be repeated.",
    )
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="Explicit target env var.")
    parser.add_argument("--log-dir", help="Directory for prompt and result artifacts.")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, run autonomy and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    prepared = _prepare(args)
    if prepared is None:
        return EXIT_USAGE
    request, config = prepared
    result, error_code = _execute(config, request)
    if result is None:
        return error_code
    return _emit(result, args)


def _prepare(args: argparse.Namespace) -> tuple[AutonomyRequest, AutonomyConfig] | None:
    try:
        requirement = _read_requirement(args)
        config = _build_config(args)
        workspace = TargetWorkspace.open(args.repo)
        request = AutonomyRequest(
            workspace=workspace,
            requirement=requirement.text,
            config=config,
            tool_kinds=_tool_kinds(args.tool),
            full_validation=args.full,
            dry_run=args.dry_run,
            discover_only=args.discover_only,
            validate_only=args.validate_only,
            target_paths=tuple(args.target_path),
            requirement_source=requirement,
        )
    except (AutonomyError, OSError) as exc:
        sys.stderr.write(f"agent-loop: {exc}\n")
        return None
    return request, config


def _execute(config: AutonomyConfig, request: AutonomyRequest) -> tuple[AutonomyResult | None, int]:
    try:
        result = AutonomyLoop(config).run(request)
    except DiscoveryError as exc:
        sys.stderr.write(f"agent-loop: {exc}\n")
        return None, EXIT_UNAVAILABLE
    except (SecurityError, ConfigurationError) as exc:
        sys.stderr.write(f"agent-loop: {exc}\n")
        return None, EXIT_USAGE
    except OSError as exc:
        sys.stderr.write(f"agent-loop: cannot complete the run: {exc}\n")
        return None, EXIT_USAGE
    return result, EXIT_OK


def _emit(result: AutonomyResult, args: argparse.Namespace) -> int:
    rendered = to_json(result) if args.json else render_markdown(result)
    if args.output:
        try:
            Path(args.output).write_text(rendered, encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"agent-loop: cannot write report: {exc}\n")
            return EXIT_USAGE
        return _exit_code(result)
    sys.stdout.write(rendered)
    if not rendered.endswith("\n"):
        sys.stdout.write("\n")
    return _exit_code(result)


def _read_requirement(args: argparse.Namespace) -> RequirementSource:
    if args.requirement is not None:
        return RequirementSource.inline(str(args.requirement))
    return RequirementSource.from_file(str(args.requirement_file))


def _build_config(args: argparse.Namespace) -> AutonomyConfig:
    if args.agent_command:
        try:
            command = tuple(shlex.split(args.agent_command))
        except ValueError as exc:
            message = f"invalid --agent-command quoting: {exc}"
            raise ConfigurationError(message) from exc
    else:
        command = ()
    environment = _parse_environment(args.env)
    log_dir = Path(args.log_dir).expanduser() if args.log_dir else None
    return AutonomyConfig(
        agent_command=command,
        agent_prompt_transport=PromptTransport(args.prompt_transport),
        max_iterations=int(args.max_iterations),
        command_timeout_seconds=int(args.timeout_seconds),
        max_output_chars=int(args.max_output_chars),
        target_environment=environment,
        log_dir=log_dir,
    )


def _parse_environment(entries: Sequence[str]) -> tuple[tuple[str, str], ...]:
    parsed: list[tuple[str, str]] = []
    for entry in entries:
        key, separator, value = entry.partition("=")
        if not separator or not key:
            message = f"invalid --env entry (expected KEY=VALUE): {entry}"
            raise ConfigurationError(message)
        parsed.append((key, value))
    return tuple(parsed)


def _tool_kinds(values: Sequence[str] | None) -> tuple[ToolKind, ...] | None:
    if not values:
        return None
    return tuple(ToolKind(value) for value in values)


def _exit_code(result: AutonomyResult) -> int:
    if result.status is ToolStatus.FAIL or result.validation_status is ToolStatus.FAIL:
        return EXIT_VALIDATION_FAILED
    if result.status is ToolStatus.PASS and result.validation_status in {None, ToolStatus.PASS}:
        return EXIT_OK
    return EXIT_UNAVAILABLE


def _json_dump(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)
