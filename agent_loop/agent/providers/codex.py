"""Provider adapter and CLI normalization for Codex executions."""

from __future__ import annotations

import argparse
import os
import sys

from agent_loop.project.subprocesses import invoke_agent_loop_subprocess

DEFAULT_CODEX_COMMAND = "codex exec"


def parse_codex_wrapper_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Parse provider flags and leave orchestrator flags untouched."""
    parser = argparse.ArgumentParser(
        description="Loop local con Codex (wrapper de agent_loop.py).",
        add_help=True,
    )
    parser.add_argument("--commit_msg", default=None)
    return parser.parse_known_args(argv)


def run_agent_loop(
    agent_command: str,
    remaining_argv: list[str],
    *,
    commit_msg: str | None = None,
) -> int:
    """Delegate execution to the compatible orchestrator entrypoint."""
    return invoke_agent_loop_subprocess(
        agent_command,
        remaining_argv,
        commit_msg=commit_msg,
    )


def main() -> int:
    """Forward arguments to the orchestrator using Codex."""
    wrapper_args, remaining_argv = parse_codex_wrapper_args(sys.argv[1:])
    agent_command = os.environ.get("SHIPII_CODEX_COMMAND", DEFAULT_CODEX_COMMAND)
    return run_agent_loop(
        agent_command,
        remaining_argv,
        commit_msg=wrapper_args.commit_msg,
    )


if __name__ == "__main__":
    raise SystemExit(main())
