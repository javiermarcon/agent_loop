"""Parser contract tests for OpenCode CLI discovery outputs."""

from __future__ import annotations

from pathlib import Path

from agent_loop.adapters.opencode.parse import ParseOutcome
from agent_loop.adapters.opencode.parse import parse_agents_json
from agent_loop.adapters.opencode.parse import parse_mcp_list
from agent_loop.adapters.opencode.parse import parse_models
from agent_loop.adapters.opencode.parse import parse_version
from agent_loop.adapters.opencode.parse import redact_sensitive

_FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_parse_version_from_observed_v2_1_4() -> None:
    """Observed ``opencode --version`` yields a normalized version string."""
    assert parse_version(_read("observed_v2_1_4_version.txt")) == "2.1.4"


def test_parse_version_rejects_embedded_or_bare_semver() -> None:
    """Only the observed ``opencode vX.Y.Z`` line is accepted."""
    assert parse_version("") is None
    assert parse_version("1.2.3") is None
    assert parse_version("not a version line") is None
    assert parse_version("prefix opencode v2.1.4") is None
    assert parse_version("opencode v2.1.4\nextra") is None
    assert parse_version("opencode 2.1.4") is None
    assert parse_version("opencode v2.1") is None


def test_parse_mcp_list_from_observed_v2_1_4() -> None:
    """Observed compact mcp list maps connected servers deterministically."""
    parsed = parse_mcp_list(_read("observed_v2_1_4_mcp_list.txt"))
    assert parsed.outcome is ParseOutcome.VALID
    assert [entry.name for entry in parsed.entries] == [
        "context7",
        "github",
        "notion",
        "playwright",
        "postgresql",
        "railway",
    ]
    assert all(entry.status == "connected" for entry in parsed.entries)
    assert all(entry.oauth_hint is False for entry in parsed.entries)


def test_parse_mcp_list_observed_empty_message_is_valid_empty() -> None:
    """Observed empty-list message is a valid empty inventory."""
    parsed = parse_mcp_list(_read("observed_v2_1_4_mcp_list_empty.txt"))
    assert parsed.outcome is ParseOutcome.VALID
    assert parsed.entries == ()


def test_parse_mcp_list_synthetic_mixed_statuses() -> None:
    """Synthetic status vocabulary maps to normalized status tokens."""
    parsed = parse_mcp_list(_read("synthetic_mcp_mixed_statuses.txt"))
    assert parsed.outcome is ParseOutcome.VALID
    entries = {entry.name: entry for entry in parsed.entries}
    assert entries["docs"].status == "connected"
    assert entries["docs"].oauth_hint is True
    assert entries["cache"].status == "disabled"
    assert entries["pending"].status == "not_initialized"
    assert entries["private"].status == "needs_auth"
    assert entries["broken"].status == "failed"
    assert entries["legacy"].status == "needs_client_registration"


def test_parse_mcp_list_empty_stdout_is_valid_empty() -> None:
    """Whitespace-only mcp stdout is a valid empty inventory, not invalid."""
    parsed = parse_mcp_list(_read("synthetic_mcp_empty.txt"))
    assert parsed.outcome is ParseOutcome.VALID
    assert parsed.entries == ()


def test_parse_mcp_list_malformed_is_invalid_not_empty_inventory() -> None:
    """Non-empty unreadable mcp stdout is INVALID, not a silent empty list."""
    parsed = parse_mcp_list(_read("synthetic_mcp_malformed.txt"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.entries == ()


def test_parse_mcp_list_unknown_resource_status_is_invalid() -> None:
    """A resource-looking row with unknown status invalidates the inventory."""
    parsed = parse_mcp_list(_read("synthetic_mcp_unknown_status_row.txt"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.entries == ()


def test_parse_mcp_list_valid_row_then_unexpected_text_is_invalid() -> None:
    """Valid rows plus unexpected text must not yield a partial VALID inventory."""
    parsed = parse_mcp_list(_read("synthetic_mcp_valid_then_garbage.txt"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.entries == ()


def test_parse_mcp_list_duplicate_names_are_invalid() -> None:
    """Duplicate MCP server names invalidate the whole inventory."""
    parsed = parse_mcp_list(_read("synthetic_mcp_duplicate_names.txt"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.entries == ()


def test_parse_models_from_observed_sample() -> None:
    """Observed models lines keep provider/model ids in order, including tags."""
    parsed = parse_models(_read("observed_v2_1_4_models_sample.txt"))
    assert parsed.outcome is ParseOutcome.VALID
    assert parsed.models == (
        "cursor-acp/auto",
        "cursor-acp/claude-4-sonnet",
        "cursor-acp/claude-4.5-sonnet",
        "opencode/gpt-5",
        "anthropic/claude-sonnet-4",
        "ollama/gemma3:4b",
    )


def test_parse_models_empty_stdout_is_invalid() -> None:
    """Empty/whitespace models stdout is INVALID (empty inventory not observed)."""
    for payload in ("", "   \n\t  \n"):
        parsed = parse_models(payload)
        assert parsed.outcome is ParseOutcome.INVALID
        assert parsed.models == ()


def test_parse_models_illegible_stdout_is_invalid() -> None:
    """Non-empty unreadable models stdout is INVALID, not a silent empty list."""
    parsed = parse_models(_read("synthetic_models_illegible.txt"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.models == ()


def test_parse_models_mixed_valid_and_invalid_is_invalid() -> None:
    """One invalid models line invalidates the whole inventory (no partial)."""
    parsed = parse_models(_read("synthetic_models_malformed.txt"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.models == ()


def test_parse_agents_json_synthetic_non_empty() -> None:
    """Synthetic (non-observed) debug agents JSON yields ordered agent ids."""
    parsed = parse_agents_json(_read("synthetic_agents.json"))
    assert parsed.outcome is ParseOutcome.VALID
    assert [(agent.agent_id, agent.name) for agent in parsed.agents] == [
        ("build", "Build"),
        ("plan", "Plan"),
    ]


def test_parse_agents_json_empty_array_is_valid_empty() -> None:
    """``[]`` is a valid empty agent inventory."""
    parsed = parse_agents_json("[]")
    assert parsed.outcome is ParseOutcome.VALID
    assert parsed.agents == ()


def test_parse_agents_json_from_observed_v2_1_4_empty() -> None:
    """Observed ``opencode debug agents`` stdout (empty array) parses as VALID."""
    parsed = parse_agents_json(_read("observed_v2_1_4_debug_agents.json"))
    assert parsed.outcome is ParseOutcome.VALID
    assert parsed.agents == ()


def test_parse_agents_json_malformed_is_invalid() -> None:
    """Empty/malformed agents payloads are INVALID, not empty inventories."""
    assert parse_agents_json("").outcome is ParseOutcome.INVALID
    assert parse_agents_json("{not-json").outcome is ParseOutcome.INVALID
    assert parse_agents_json('{"id":"x"}').outcome is ParseOutcome.INVALID


def test_parse_agents_json_invalid_only_elements_is_invalid() -> None:
    """Arrays of only invalid elements are INVALID, not a silent empty list."""
    parsed = parse_agents_json(_read("synthetic_agents_invalid_only.json"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.agents == ()


def test_parse_agents_json_mixed_valid_and_invalid_is_invalid() -> None:
    """One invalid element invalidates the whole agents array (no partial)."""
    parsed = parse_agents_json(_read("synthetic_agents_mixed.json"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.agents == ()


def test_parse_agents_json_duplicate_ids_are_invalid() -> None:
    """Duplicate agent ids invalidate the whole inventory."""
    parsed = parse_agents_json(_read("synthetic_agents_duplicate.json"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.agents == ()


def test_parse_agents_json_rejects_capability_unsafe_ids() -> None:
    """Agent ids with unexpected characters are INVALID."""
    parsed = parse_agents_json(_read("synthetic_agents_bad_id.json"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.agents == ()
    assert parse_agents_json('[{"id":"","name":"X"}]').outcome is ParseOutcome.INVALID
    assert parse_agents_json('[{"id":"ok","name":""}]').outcome is ParseOutcome.INVALID


def test_redact_sensitive_strips_tokens_from_observations() -> None:
    """Credential-like fragments are redacted before leaving the adapter."""
    raw = 'Authorization: Bearer sk-secret-value api_key=abc123 token=xyz password=hunter2 secret": "json-secret"'
    redacted = redact_sensitive(raw)
    assert "sk-secret-value" not in redacted
    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert "hunter2" not in redacted
    assert "json-secret" not in redacted
    assert "Bearer" in redacted or "REDACTED" in redacted
