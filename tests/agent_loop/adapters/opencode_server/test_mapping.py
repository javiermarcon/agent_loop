"""Unit tests for neutral request/response mapping."""

from __future__ import annotations

import pytest

from agent_loop.adapters.opencode_server.client import PermissionRule
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.mapping import MetadataInput
from agent_loop.adapters.opencode_server.mapping import agent_for_request
from agent_loop.adapters.opencode_server.mapping import evidence_refs
from agent_loop.adapters.opencode_server.mapping import execution_failure_detail
from agent_loop.adapters.opencode_server.mapping import final_text
from agent_loop.adapters.opencode_server.mapping import metadata_pairs
from agent_loop.adapters.opencode_server.mapping import parse_native_model
from agent_loop.adapters.opencode_server.mapping import permission_rules
from agent_loop.adapters.opencode_server.mapping import status_from_outcome
from agent_loop.adapters.opencode_server.mapping import usage_from_session
from agent_loop.adapters.opencode_server.models import AssistantMessage
from agent_loop.adapters.opencode_server.models import DiffEntry
from agent_loop.adapters.opencode_server.models import ModelRef
from agent_loop.adapters.opencode_server.models import SessionInfo
from agent_loop.adapters.opencode_server.models import TokenUsage
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy

_INPUT_TOKENS = 10
_OUTPUT_TOKENS = 5
_COST_MICROS = 250_000
_MAX_DETAIL_LENGTH = 300


def _session(
    *,
    outcome: str | None = "succeeded",
    tokens: TokenUsage | None = None,
    model: ModelRef | None = None,
) -> SessionInfo:
    return SessionInfo(
        session_id="ses_123",
        directory="/workspace",
        model=model,
        outcome=outcome,
        tokens=tokens or TokenUsage(),
        title="agent-loop execution",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("opencode-go/deepseek-v4.1-flash", ModelRef("opencode-go", "deepseek-v4.1-flash")),
        ("ollama/qwen3-coder:30b", ModelRef("ollama", "qwen3-coder:30b")),
        ("openai/gpt-6-astra#fast", ModelRef("openai", "gpt-6-astra", "fast")),
    ],
)
def test_parse_native_model_accepts_documented_forms(raw: str, expected: ModelRef) -> None:
    """The documented provider/model[#variant] form is preserved."""
    assert parse_native_model(raw) == expected


@pytest.mark.parametrize("raw", ["deepseek-v4.1-flash", "/model", "provider/", ""])
def test_parse_native_model_rejects_ambiguous_forms(raw: str) -> None:
    """Ambiguous model strings fail closed rather than guessing a provider."""
    with pytest.raises(OpenCodeServerError) as captured:
        parse_native_model(raw)
    assert captured.value.code is ServerFailureCode.INVALID_REQUEST


def test_parse_native_model_none_stays_none() -> None:
    """No model means the server default, not an invented model."""
    assert parse_native_model(None) is None


def test_agent_mapping_is_least_privileged() -> None:
    """Read-only mutation policy forces the plan agent regardless of strategy."""
    no_mutation = MutationPolicy(allow_workspace_mutation=False)
    assert agent_for_request(ExecutionStrategy.BUILD, no_mutation) == "plan"
    assert agent_for_request(ExecutionStrategy.PLAN, no_mutation) == "plan"
    assert agent_for_request(ExecutionStrategy.BUILD, MutationPolicy(allow_workspace_mutation=True)) == "build"
    assert agent_for_request(ExecutionStrategy.REVIEW, MutationPolicy(allow_workspace_mutation=True)) == "build"


def test_permission_rules_only_restrict() -> None:
    """No rule expands a permission; restrictive axes are explicit."""
    rules = permission_rules(
        MutationPolicy(allow_workspace_mutation=False),
        PermissionPolicy(allow_network=False),
    )
    assert rules == (
        PermissionRule("external_directory", "*", "deny"),
        PermissionRule("webfetch", "*", "deny"),
        PermissionRule("websearch", "*", "deny"),
        PermissionRule("edit", "*", "deny"),
        PermissionRule("shell", "*", "deny"),
    )


def test_permission_rules_keep_allows_untouched_when_permitted() -> None:
    """Permitted axes are not replaced by a broad allow rule."""
    rules = permission_rules(
        MutationPolicy(allow_workspace_mutation=True),
        PermissionPolicy(allow_network=True),
    )
    assert rules == (PermissionRule("external_directory", "*", "deny"),)
    assert all(rule.effect == "deny" for rule in rules)


def test_final_text_keeps_assistant_order_and_ignores_empty() -> None:
    """Assistant text is concatenated in server order."""
    messages = (
        AssistantMessage("msg_1", "first"),
        AssistantMessage("msg_2", ""),
        AssistantMessage("msg_3", "second"),
    )
    assert final_text(messages) == "first\nsecond"


def test_evidence_refs_are_deterministic_and_unique() -> None:
    """Evidence references are stable diagnostic handles, not quality verdicts."""
    messages = (AssistantMessage("msg_1", "ok"), AssistantMessage("msg_1", "ok"))
    diffs = (DiffEntry("a.py", 1, 0, "modified"), DiffEntry("a.py", 1, 0, "modified"))
    assert evidence_refs("ses_1", messages, diffs) == (
        "opencode-server:session:ses_1",
        "opencode-server:message:msg_1",
        "opencode-server:diff:a.py",
    )


def test_usage_preserves_unknown_metrics() -> None:
    """Missing cost/tokens stay missing rather than becoming zero."""
    assert usage_from_session(_session()) is None
    usage = usage_from_session(
        _session(tokens=TokenUsage(input_tokens=10, output_tokens=5, cost_usd=0.25)),
    )
    assert usage is not None
    assert usage.input_tokens == _INPUT_TOKENS
    assert usage.output_tokens == _OUTPUT_TOKENS
    assert usage.cost_micros == _COST_MICROS


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("succeeded", AgentExecutionStatus.SUCCEEDED),
        ("failed", AgentExecutionStatus.FAILED),
        ("interrupted", AgentExecutionStatus.CANCELLED),
        (None, None),
        ("unknown", None),
    ],
)
def test_status_from_outcome(outcome: str | None, expected: AgentExecutionStatus | None) -> None:
    """Unknown outcomes are not promoted to success."""
    assert status_from_outcome(outcome) is expected


def test_metadata_keeps_only_observed_fields() -> None:
    """Metadata never invents reasoning tokens, cost, or model data."""
    pairs = metadata_pairs(
        MetadataInput(
            version="2.2.6",
            session=_session(model=ModelRef("opencode-go", "model-a")),
            agent="build",
            diff_count=2,
            usage=TokenUsage(),
        ),
    )
    assert ("adapter", "opencode-server") in pairs
    assert ("server_version", "2.2.6") in pairs
    assert ("model", "opencode-go/model-a") in pairs
    assert all(name != "reasoning_tokens" for name, _ in pairs)


def test_execution_failure_detail_redacts_and_bounds() -> None:
    """Failed execution details are redacted and bounded."""
    event = {
        "type": "session.execution.failed",
        "data": {"error": {"type": "provider", "message": "token=abc123 " + "x" * 1000}},
    }
    detail = execution_failure_detail(event, "abc123")
    assert "abc123" not in detail
    assert len(detail) <= _MAX_DETAIL_LENGTH
