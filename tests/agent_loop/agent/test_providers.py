"""Contract tests for provider adapters and their subprocess boundary."""

from __future__ import annotations

from argparse import Namespace

from agent_loop.agent.providers import codex
from agent_loop.agent.providers import cursor

EXPECTED_FORWARDING_EXIT_CODE = 7


def test_cursor_command_preserves_shell_quoting_and_optional_flags() -> None:
    """Cursor command construction preserves tokens and requested options."""
    command = cursor.build_cursor_agent_command(
        base_command="cursor-agent -p",
        model="model with spaces",
        mode="plan",
        plan=True,
    )

    assert command == "cursor-agent -p --plan --mode plan --model 'model with spaces'"


def test_provider_forwarding_uses_the_canonical_subprocess_boundary(monkeypatch) -> None:
    """Codex forwards its command, arguments, and commit message unchanged."""
    calls: list[tuple[str, list[str], str | None]] = []

    def fake_invoke(
        command: str,
        argv: list[str],
        *,
        commit_msg: str | None = None,
    ) -> int:
        calls.append((command, argv, commit_msg))
        return EXPECTED_FORWARDING_EXIT_CODE

    monkeypatch.setattr(codex, "invoke_agent_loop_subprocess", fake_invoke)

    result = codex.run_agent_loop("codex exec", ["--requirement", "x"], commit_msg="msg")

    assert result == EXPECTED_FORWARDING_EXIT_CODE
    assert calls == [("codex exec", ["--requirement", "x"], "msg")]


def test_cursor_retries_once_with_auto_model_after_ineffective_agent(monkeypatch) -> None:
    """Cursor fallback retries only after the provider reports ineffectiveness."""
    calls: list[str] = []

    def fake_run(command: str, _argv: list[str], *, commit_msg: str | None = None) -> int:
        del commit_msg
        calls.append(command)
        return cursor.AGENT_INEFFECTIVE_EXIT_CODE if len(calls) == 1 else 0

    monkeypatch.setattr(cursor, "run_agent_loop", fake_run)
    monkeypatch.setattr(cursor, "explicit_cursor_model", lambda _args: "gpt-5")
    monkeypatch.setattr(cursor, "resolve_auto_fallback_model", lambda: "Auto")
    monkeypatch.setattr(cursor, "resolve_cursor_agent_command", lambda _args: "cursor-agent --model gpt-5")
    monkeypatch.setattr(cursor, "build_fallback_cursor_agent_command", lambda _args: "cursor-agent --model Auto")

    args = Namespace(
        auto_fallback=True,
        cursor_model="gpt-5",
        cursor_mode=None,
        cursor_plan=False,
        commit_msg=None,
    )
    monkeypatch.setattr(cursor, "parse_cursor_wrapper_args", lambda _argv: (args, ["--requirement", "x"]))

    assert cursor.main() == 0
    assert calls == ["cursor-agent --model gpt-5", "cursor-agent --model Auto"]
