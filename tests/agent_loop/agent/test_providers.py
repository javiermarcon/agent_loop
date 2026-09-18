"""Contract tests for in-process agent provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Protocol

import pytest

from agent_loop.agent.providers import AgentLoopApplication
from agent_loop.agent.providers import codex
from agent_loop.agent.providers import cursor

EXPECTED_FORWARDING_EXIT_CODE = 7


@dataclass
class _RecordingApplication:
    """Record explicit lifecycle argv and return configured exit codes."""

    exit_codes: list[int]
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, argv: tuple[str, ...]) -> int:
        """Record one invocation and return its corresponding status."""
        self.calls.append(argv)
        return self.exit_codes.pop(0)


class _ProviderRunner(Protocol):
    """Provider entrypoint shared by Cursor and Codex."""

    def __call__(
        self,
        agent_command: str,
        remaining_argv: list[str],
        *,
        application: AgentLoopApplication,
        commit_msg: str | None = None,
    ) -> int:
        """Execute one provider-shaped application invocation."""


def test_cursor_command_preserves_shell_quoting_and_optional_flags() -> None:
    """Cursor command construction preserves tokens and requested options."""
    command = cursor.build_cursor_agent_command(
        base_command="cursor-agent -p",
        model="model with spaces",
        mode="plan",
        plan=True,
    )

    assert command == "cursor-agent -p --plan --mode plan --model 'model with spaces'"


@pytest.mark.parametrize("run_provider", [codex.run_agent_loop, cursor.run_agent_loop])
def test_provider_runs_application_in_process_with_exact_forwarding(
    run_provider: _ProviderRunner,
) -> None:
    """Both providers forward command, argv, commit message, and exit status."""
    application = _RecordingApplication([EXPECTED_FORWARDING_EXIT_CODE])

    result = run_provider(
        "agent command",
        ["--requirement", "x", "--dry-run"],
        application=application,
        commit_msg="msg",
    )

    assert result == EXPECTED_FORWARDING_EXIT_CODE
    assert application.calls == [
        (
            "--agent-command",
            "agent command",
            "--commit_msg",
            "msg",
            "--requirement",
            "x",
            "--dry-run",
        ),
    ]


def test_codex_main_uses_explicit_argv_and_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex resolves its command without consulting process argv."""
    application = _RecordingApplication([EXPECTED_FORWARDING_EXIT_CODE])
    monkeypatch.setenv("SHIPII_CODEX_COMMAND", "custom codex")

    result = codex.main(
        ["--commit_msg", "fix: exact", "--requirement", "x"],
        application,
    )

    assert result == EXPECTED_FORWARDING_EXIT_CODE
    assert application.calls == [
        (
            "--agent-command",
            "custom codex",
            "--commit_msg",
            "fix: exact",
            "--requirement",
            "x",
        ),
    ]


def test_cursor_retries_application_once_with_auto_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor fallback reinvokes only the application with the Auto model."""
    application = _RecordingApplication([cursor.AGENT_INEFFECTIVE_EXIT_CODE, 0])
    monkeypatch.delenv(cursor.CURSOR_COMMAND_ENV, raising=False)
    monkeypatch.delenv(cursor.CURSOR_MODEL_ENV, raising=False)
    monkeypatch.delenv(cursor.CURSOR_MODE_ENV, raising=False)
    monkeypatch.delenv(cursor.CURSOR_AUTO_FALLBACK_MODEL_ENV, raising=False)
    remaining = ["--requirement", "x", "--dry-run"]

    result = cursor.main(
        [
            "--model",
            "gpt-5",
            "--mode",
            "plan",
            "--auto-fallback",
            "--commit_msg",
            "feat: preserve",
            *remaining,
        ],
        application,
    )

    assert result == 0
    assert application.calls == [
        (
            "--agent-command",
            f"{cursor.DEFAULT_CURSOR_COMMAND} --mode plan --model gpt-5",
            "--commit_msg",
            "feat: preserve",
            *remaining,
        ),
        (
            "--agent-command",
            f"{cursor.DEFAULT_CURSOR_COMMAND} --mode plan --model {cursor.AUTO_FALLBACK_MODEL}",
            "--commit_msg",
            "feat: preserve",
            *remaining,
        ),
    ]


@pytest.mark.parametrize(
    ("initial_code", "model", "command_override"),
    [
        (1, "gpt-5", None),
        (cursor.AGENT_INEFFECTIVE_EXIT_CODE, cursor.AUTO_FALLBACK_MODEL, None),
        (cursor.AGENT_INEFFECTIVE_EXIT_CODE, "gpt-5", "custom cursor"),
    ],
)
def test_cursor_does_not_fallback_when_policy_disallows_it(
    monkeypatch: pytest.MonkeyPatch,
    initial_code: int,
    model: str,
    command_override: str | None,
) -> None:
    """General failures, Auto selection, and command overrides stay single-shot."""
    application = _RecordingApplication([initial_code])
    if command_override is None:
        monkeypatch.delenv(cursor.CURSOR_COMMAND_ENV, raising=False)
    else:
        monkeypatch.setenv(cursor.CURSOR_COMMAND_ENV, command_override)

    result = cursor.main(
        ["--model", model, "--auto-fallback", "--requirement", "x"],
        application,
    )

    assert result == initial_code
    assert len(application.calls) == 1


def test_providers_have_no_process_launch_dependency() -> None:
    """The provider boundary contains no adapter that launches agent_loop.py."""
    repository_root = Path(__file__).resolve().parents[3]

    assert not (repository_root / "agent_loop" / "project" / "subprocesses.py").exists()


def test_canonical_agent_loop_has_no_legacy_script_imports() -> None:
    """Canonical modules remain independent from the scripts composition root."""
    package_root = Path(__file__).resolve().parents[3] / "agent_loop"
    offending: list[str] = []
    for source_path in package_root.rglob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        if "from scripts" in source or "import scripts" in source:
            offending.append(str(source_path.relative_to(package_root.parent)))

    assert offending == []


def test_providers_do_not_read_process_argv() -> None:
    """Only external CLI wrappers may consult ``sys.argv``."""
    assert "sys" not in vars(codex)
    assert "sys" not in vars(cursor)
