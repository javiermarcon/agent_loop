"""Tool result contract tests."""

from __future__ import annotations

from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.tools.base import ToolInvocation
from agent_loop.autonomy.tools.base import ToolResult
from agent_loop.autonomy.tools.base import ToolStatus


def test_tool_result_serializes_invocation_and_status() -> None:
    """Structured results preserve reproducibility metadata."""
    invocation = ToolInvocation(
        tool=ToolKind.RUFF,
        command=("python", "-m", "ruff", "check", "."),
        cwd="/repo",
        interpreter="/repo/.venv/bin/python",
        environment_keys=("PATH",),
    )
    result = ToolResult(
        tool=ToolKind.RUFF,
        status=ToolStatus.FAIL,
        message="ruff exited with code 1",
        invocation=invocation,
        exit_code=1,
        stdout="out",
        stderr="err",
        duration_seconds=0.5,
    )
    assert result.succeeded is False
    payload = result.to_dict()
    assert payload["tool"] == "ruff"
    assert payload["status"] == "fail"
    assert payload["invocation"] == {
        "tool": "ruff",
        "command": ["python", "-m", "ruff", "check", "."],
        "cwd": "/repo",
        "interpreter": "/repo/.venv/bin/python",
        "environment_keys": ["PATH"],
    }


def test_tool_result_without_invocation_serializes_none() -> None:
    """Skipped and unavailable results do not invent an invocation."""
    result = ToolResult(tool=ToolKind.PYTEST, status=ToolStatus.SKIPPED, message="not configured")
    assert result.succeeded is False
    assert result.to_dict()["invocation"] is None
