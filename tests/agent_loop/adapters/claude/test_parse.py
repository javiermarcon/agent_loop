"""Parser contract tests for Claude Code CLI discovery outputs."""

from __future__ import annotations

from pathlib import Path

from agent_loop.adapters.claude.parse import ParseOutcome
from agent_loop.adapters.claude.parse import parse_auth_status_json
from agent_loop.adapters.claude.parse import parse_mcp_list
from agent_loop.adapters.claude.parse import parse_plugin_list
from agent_loop.adapters.claude.parse import parse_version
from agent_loop.adapters.claude.parse import redact_sensitive

_FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_parse_version_from_observed_v2_1_283() -> None:
    """Observed ``claude --version`` yields a normalized version string."""
    assert parse_version(_read("observed_v2_1_283_version.txt")) == "2.1.283"


def test_parse_version_rejects_invalid_forms() -> None:
    """Only the observed ``X.Y.Z (Claude Code)`` line is accepted."""
    assert parse_version("") is None
    assert parse_version(_read("synthetic_version_invalid.txt")) is None
    assert parse_version("2.1.283") is None
    assert parse_version("2.1.283 (Claude Code)\nextra") is None


def test_parse_auth_status_not_logged_in_observed() -> None:
    """Observed logged-out JSON maps to logged_in=False without leaking paths."""
    parsed = parse_auth_status_json(_read("observed_v2_1_283_auth_status_not_logged_in.json"))
    assert parsed.outcome is ParseOutcome.VALID
    assert parsed.auth is not None
    assert parsed.auth.logged_in is False
    assert parsed.auth.auth_method == "none"
    assert parsed.auth.api_provider == "firstParty"


def test_parse_auth_status_logged_in_synthetic_omits_secrets() -> None:
    """Synthetic logged-in JSON keeps only safe fields on the observation."""
    parsed = parse_auth_status_json(_read("synthetic_auth_logged_in.json"))
    assert parsed.outcome is ParseOutcome.VALID
    assert parsed.auth is not None
    assert parsed.auth.logged_in is True
    assert parsed.auth.auth_method == "claude.ai"
    assert "email" not in repr(parsed.auth)
    assert "org" not in repr(parsed.auth).lower()
    assert "home" not in repr(parsed.auth)


def test_parse_auth_status_invalid_and_partial() -> None:
    """Malformed or partial auth JSON is INVALID, not a guessed session."""
    assert parse_auth_status_json(_read("synthetic_auth_invalid.json")).outcome is ParseOutcome.INVALID
    assert parse_auth_status_json(_read("synthetic_auth_partial.json")).outcome is ParseOutcome.INVALID
    assert parse_auth_status_json("not-json").outcome is ParseOutcome.INVALID


def test_parse_mcp_list_observed_empty() -> None:
    """Observed empty-list message is a valid empty inventory."""
    parsed = parse_mcp_list(_read("observed_v2_1_283_mcp_list_empty.txt"))
    assert parsed.outcome is ParseOutcome.VALID
    assert parsed.entries == ()


def test_parse_mcp_list_empty_or_whitespace_stdout_is_invalid() -> None:
    """Blank stdout is not evidence of an empty MCP inventory."""
    assert parse_mcp_list("").outcome is ParseOutcome.INVALID
    assert parse_mcp_list("").entries == ()
    assert parse_mcp_list("   \n\t\n").outcome is ParseOutcome.INVALID
    assert parse_mcp_list("   \n\t\n").entries == ()


def test_parse_mcp_list_arbitrary_text_without_header_is_invalid() -> None:
    """Arbitrary non-row text without the empty message is INVALID."""
    parsed = parse_mcp_list("something unexpected from the CLI")
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.entries == ()


def test_parse_mcp_list_partial_unknown_stdout_is_invalid() -> None:
    """Partial or unknown stdout must not become a silent empty inventory."""
    parsed = parse_mcp_list("Checking… incomplete")
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.entries == ()


def test_parse_mcp_list_observed_pending_http() -> None:
    """Observed pending-approval HTTP row maps without claiming connectivity."""
    parsed = parse_mcp_list(_read("observed_v2_1_283_mcp_list_pending.txt"))
    assert parsed.outcome is ParseOutcome.VALID
    assert len(parsed.entries) == 1
    entry = parsed.entries[0]
    assert entry.name == "demo-http"
    assert entry.status == "pending_approval"
    assert entry.transport == "http"


def test_parse_mcp_list_observed_pending_mixed_transports() -> None:
    """Observed SSE and stdio pending rows preserve transport labels."""
    parsed = parse_mcp_list(_read("observed_v2_1_283_mcp_list_pending_mixed_transports.txt"))
    assert parsed.outcome is ParseOutcome.VALID
    by_name = {entry.name: entry for entry in parsed.entries}
    assert by_name["demo-sse"].transport == "sse"
    assert by_name["demo-stdio"].transport == "stdio"
    assert by_name["demo-sse"].status == "pending_approval"


def test_parse_mcp_list_ws_invisible_is_invalid_not_empty() -> None:
    """Health-header-only stdout (observed ws-invisible) is INVALID."""
    parsed = parse_mcp_list(_read("observed_v2_1_283_mcp_list_ws_invisible.txt"))
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.entries == ()


def test_parse_mcp_list_health_prefix_spoof_does_not_hide_garbage() -> None:
    """Lines that merely start with the health prefix must not be skipped.

    A too-broad ``startswith`` would drop unrecognized text and still accept
    later rows as a VALID inventory.
    """
    spoofed = (
        "Checking MCP server healthy FOOBAR should invalidate\n"
        "docs: https://example.invalid/docs (HTTP) - ✓ Connected\n"
    )
    parsed = parse_mcp_list(spoofed)
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.entries == ()


def test_parse_mcp_list_synthetic_mixed_statuses() -> None:
    """Synthetic status vocabulary maps to normalized tokens."""
    parsed = parse_mcp_list(_read("synthetic_mcp_mixed_statuses.txt"))
    assert parsed.outcome is ParseOutcome.VALID
    entries = {entry.name: entry for entry in parsed.entries}
    assert entries["docs"].status == "connected"
    assert entries["cache"].status == "disabled"
    assert entries["private"].status == "needs_auth"
    assert entries["broken"].status == "failed"
    assert entries["denied"].status == "rejected"
    assert entries["proxy"].transport == "url"
    assert entries["stdio-echo"].transport == "stdio"
    assert entries["tools-bad"].status == "connected_tools_failed"


def test_parse_mcp_list_accepts_exact_known_status_forms() -> None:
    """Known statuses normalize only when the phrase matches exactly after glyphs."""
    # Glyphs via escapes avoid RUF001 ambiguous-character warnings in source.
    check = "\u2713"
    cross = "\u00d7"
    mid_dot = "\u00b7"
    stdout = (
        "Checking MCP server health\u2026\n"
        f"docs: https://example.invalid/docs (HTTP) - {check} Connected\n"
        f"broken: https://example.invalid/broken (HTTP) - {cross} Failed to connect\n"
        f"tools-bad: https://example.invalid/tools (HTTP) - "
        f"! Connected {mid_dot} tools fetch failed\n"
    )
    parsed = parse_mcp_list(stdout)
    assert parsed.outcome is ParseOutcome.VALID
    by_name = {entry.name: entry.status for entry in parsed.entries}
    assert by_name == {
        "docs": "connected",
        "broken": "failed",
        "tools-bad": "connected_tools_failed",
    }


def test_parse_mcp_list_rejects_em_dash_and_unknown_status_suffixes() -> None:
    """Em-dash or other trailing junk must not silently map to a known status."""
    check = "\u2713"
    cross = "\u00d7"
    em_dash = "\u2014"
    cases = (
        f"docs: https://example.invalid/docs (HTTP) - {check} Connected {em_dash} texto inesperado\n",
        f"docs: https://example.invalid/docs (HTTP) - Connected {em_dash} arbitrary issue detail\n",
        f"docs: https://example.invalid/docs (HTTP) - {check} Connected unexpected\n",
        f"broken: https://example.invalid/broken (HTTP) - {cross} Failed to connect {em_dash} timeout\n",
        "weird: https://example.invalid/x (HTTP) - Totally Unknown Status\n",
        "empty-status: https://example.invalid/x (HTTP) - \n",
    )
    for stdout in cases:
        parsed = parse_mcp_list(f"Checking MCP server health\u2026\n{stdout}")
        assert parsed.outcome is ParseOutcome.INVALID, stdout
        assert parsed.entries == ()


def test_parse_mcp_list_empty_message_with_server_row_is_invalid() -> None:
    """Empty-inventory message plus a server row is contradictory → INVALID."""
    check = "\u2713"
    stdout = (
        "No MCP servers configured. Use `claude mcp add` to add a server.\n"
        f"docs: https://example.invalid/docs (HTTP) - {check} Connected\n"
    )
    parsed = parse_mcp_list(stdout)
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.entries == ()


def test_parse_mcp_list_empty_message_with_health_and_row_is_invalid() -> None:
    """Empty message, health header, and a row remain mutually incompatible."""
    check = "\u2713"
    stdout = (
        "Checking MCP server health\u2026\n"
        "No MCP servers configured. Use `claude mcp add` to add a server.\n"
        f"docs: https://example.invalid/docs (HTTP) - {check} Connected\n"
    )
    parsed = parse_mcp_list(stdout)
    assert parsed.outcome is ParseOutcome.INVALID
    assert parsed.entries == ()


def test_parse_mcp_list_malformed_unknown_duplicate_partial() -> None:
    """Unreadable, unknown-status, duplicate, or garbage-tailed lists are INVALID."""
    assert parse_mcp_list(_read("synthetic_mcp_malformed.txt")).outcome is ParseOutcome.INVALID
    assert parse_mcp_list(_read("synthetic_mcp_unknown_status.txt")).outcome is ParseOutcome.INVALID
    assert parse_mcp_list(_read("synthetic_mcp_duplicates.txt")).outcome is ParseOutcome.INVALID
    assert parse_mcp_list(_read("synthetic_mcp_valid_then_garbage.txt")).outcome is ParseOutcome.INVALID


def test_parse_plugin_list_observed_empty() -> None:
    """Observed empty plugin list message is VALID empty."""
    parsed = parse_plugin_list(_read("observed_v2_1_283_plugin_list_empty.txt"))
    assert parsed.outcome is ParseOutcome.VALID
    assert parsed.empty is True


def test_parse_plugin_list_unexpected_nonempty_is_invalid() -> None:
    """Non-empty plugin list formats are unobserved and therefore INVALID."""
    parsed = parse_plugin_list(_read("synthetic_plugin_list_unexpected.txt"))
    assert parsed.outcome is ParseOutcome.INVALID


def test_redact_sensitive_masks_secrets_and_emails() -> None:
    """Observations redact bearer tokens, sk- keys, and email addresses."""
    sample = "Authorization: Bearer secret-token sk-abcdefghijklmnop user@example.com"
    redacted = redact_sensitive(sample)
    assert "secret-token" not in redacted
    assert "sk-abcdefghijklmnop" not in redacted
    assert "user@example.com" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_sensitive_masks_home_usernames_but_keeps_tmp() -> None:
    """Home usernames in absolute paths are masked; non-home paths stay usable."""
    # Build the scratch path without a /tmp literal in source (Bandit S108).
    scratch = "claude-scratch/ok"
    tmp_fallback = f"{chr(47)}tmp{chr(47)}{scratch}"
    stderr = (
        f"failed reading /home/alguien/.claude/projects/x and /Users/alguien/Library/cache fallback={tmp_fallback}"
    )
    redacted = redact_sensitive(stderr)
    assert "/home/alguien" not in redacted
    assert "/Users/alguien" not in redacted
    assert "/home/[REDACTED]/.claude/projects/x" in redacted
    assert "/Users/[REDACTED]/Library/cache" in redacted
    assert scratch in redacted
    assert "[REDACTED]" not in redacted.split("fallback=", 1)[1]


def test_parse_mcp_list_uses_raw_stdout_before_redaction() -> None:
    """Path-like text in stdout must still parse; redaction is observation-only."""
    check = "\u2713"
    em_dash = "\u2014"
    stdout = f"Checking MCP server health\u2026\ndocs: https://example.invalid/docs (HTTP) - {check} Connected\n"
    assert parse_mcp_list(stdout).outcome is ParseOutcome.VALID
    # Redacting a copy must not be required for parsing, and must not invent
    # validity when the status itself is unknown after an em-dash suffix.
    polluted = (
        "Checking MCP server health\u2026\n"
        f"local: /home/alguien/.claude/bin/server - {check} Connected {em_dash} leak\n"
    )
    assert parse_mcp_list(polluted).outcome is ParseOutcome.INVALID
    assert "/home/alguien" in polluted
    assert "/home/alguien" not in redact_sensitive(polluted)
