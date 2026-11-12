"""Provider adapter and CLI normalization for Cursor executions."""

from __future__ import annotations

import argparse
import os
import shlex

from agent_loop.agent.providers import AgentLoopApplication
from agent_loop.agent.providers import invoke_agent_loop_application
from agent_loop.domain.exit_codes import AGENT_INEFFECTIVE_EXIT_CODE

DEFAULT_CURSOR_COMMAND = "cursor-agent -p --trust --force --output-format text"
CURSOR_MODEL_ENV = "AGENT_LOOP_CURSOR_MODEL"
CURSOR_MODE_ENV = "AGENT_LOOP_CURSOR_MODE"
CURSOR_COMMAND_ENV = "AGENT_LOOP_CURSOR_COMMAND"
CURSOR_AUTO_FALLBACK_MODEL_ENV = "AGENT_LOOP_CURSOR_AUTO_FALLBACK_MODEL"
AUTO_FALLBACK_MODEL = "Auto"


def build_cursor_agent_command(
    *,
    base_command: str,
    model: str | None = None,
    mode: str | None = None,
    plan: bool = False,
) -> str:
    """Build a Cursor command with optional model, mode, and plan flags."""
    parts = shlex.split(base_command)
    if plan and "--plan" not in parts:
        parts.append("--plan")
    if mode:
        parts.extend(["--mode", mode])
    if model:
        parts.extend(["--model", model])
    return shlex.join(parts)


def parse_cursor_wrapper_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Parse Cursor-specific flags and preserve all orchestrator arguments."""
    parser = argparse.ArgumentParser(
        description="Loop local con cursor-agent (wrapper de agent_loop.py).",
        add_help=True,
    )
    parser.add_argument("--model", dest="cursor_model")
    parser.add_argument("--mode", dest="cursor_mode")
    parser.add_argument("--plan", action="store_true", dest="cursor_plan")
    parser.add_argument("--auto-fallback", action="store_true")
    parser.add_argument("--commit_msg", default=None)
    return parser.parse_known_args(argv)


def resolve_cursor_agent_command(args: argparse.Namespace) -> str:
    """Resolve the configured Cursor command, honoring environment overrides."""
    env_command = os.environ.get(CURSOR_COMMAND_ENV, "").strip()
    if env_command:
        return env_command
    model = args.cursor_model or os.environ.get(CURSOR_MODEL_ENV, "").strip() or None
    mode = args.cursor_mode or os.environ.get(CURSOR_MODE_ENV, "").strip() or None
    return build_cursor_agent_command(
        base_command=DEFAULT_CURSOR_COMMAND,
        model=model,
        mode=mode,
        plan=args.cursor_plan,
    )


def explicit_cursor_model(args: argparse.Namespace) -> str | None:
    """Return the explicitly configured Cursor model, if any."""
    return args.cursor_model or os.environ.get(CURSOR_MODEL_ENV, "").strip() or None


def build_fallback_cursor_agent_command(args: argparse.Namespace) -> str:
    """Build the single automatic-model retry command."""
    base_command = os.environ.get(CURSOR_COMMAND_ENV, "").strip() or DEFAULT_CURSOR_COMMAND
    mode = args.cursor_mode or os.environ.get(CURSOR_MODE_ENV, "").strip() or None
    fallback_model = os.environ.get(CURSOR_AUTO_FALLBACK_MODEL_ENV, "").strip() or AUTO_FALLBACK_MODEL
    return build_cursor_agent_command(
        base_command=base_command,
        model=fallback_model,
        mode=mode,
        plan=args.cursor_plan,
    )


def resolve_auto_fallback_model() -> str:
    """Return the configured automatic Cursor fallback model."""
    return os.environ.get(CURSOR_AUTO_FALLBACK_MODEL_ENV, "").strip() or AUTO_FALLBACK_MODEL


def run_agent_loop(
    agent_command: str,
    remaining_argv: list[str],
    *,
    application: AgentLoopApplication,
    commit_msg: str | None = None,
) -> int:
    """Execute the compatible application lifecycle in-process."""
    return invoke_agent_loop_application(
        application,
        agent_command,
        remaining_argv,
        commit_msg=commit_msg,
    )


def main(argv: list[str], application: AgentLoopApplication) -> int:
    """Forward explicit arguments and retry once with Cursor's automatic model."""
    wrapper_args, remaining_argv = parse_cursor_wrapper_args(argv)
    exit_code = run_agent_loop(
        resolve_cursor_agent_command(wrapper_args),
        remaining_argv,
        application=application,
        commit_msg=wrapper_args.commit_msg,
    )
    configured_model = explicit_cursor_model(wrapper_args)
    fallback_model = resolve_auto_fallback_model()
    should_retry = (
        exit_code == AGENT_INEFFECTIVE_EXIT_CODE
        and wrapper_args.auto_fallback
        and configured_model is not None
        and configured_model.lower() != fallback_model.lower()
        and not os.environ.get(CURSOR_COMMAND_ENV, "").strip()
    )
    if not should_retry:
        return exit_code
    return run_agent_loop(
        build_fallback_cursor_agent_command(wrapper_args),
        remaining_argv,
        application=application,
        commit_msg=wrapper_args.commit_msg,
    )
