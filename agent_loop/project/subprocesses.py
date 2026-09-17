"""Subprocess adapter used by legacy provider commands."""

from __future__ import annotations

import subprocess
import sys
from functools import partial
from pathlib import Path


def invoke_agent_loop_subprocess(
    agent_command: str,
    remaining_argv: list[str],
    *,
    commit_msg: str | None = None,
) -> int:
    """Launch the compatible agent-loop entrypoint with one provider command."""
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "agent_loop.py"
    forwarded_argv = list(remaining_argv)
    if commit_msg is not None:
        forwarded_argv = ["--commit_msg", commit_msg, *forwarded_argv]
    command = [
        sys.executable,
        str(script_path),
        "--agent-command",
        agent_command,
        *forwarded_argv,
    ]
    execute = partial(subprocess.run, check=False)
    completed = execute(command)
    return completed.returncode
