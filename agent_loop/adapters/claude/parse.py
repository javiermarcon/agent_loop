"""Deterministic parsers for Claude Code CLI discovery outputs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum

# Observed Claude Code v2.1.283: a single line ``X.Y.Z (Claude Code)``.
_VERSION_RE = re.compile(
    r"^(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?)\s+\(Claude Code\)$",
)
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MCP_ROW_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+):\s+(?P<body>.+)\s+-\s+(?P<status>.+)$",
)
# Leading status glyphs from Claude MCP rows (check, cross, pause, disabled).
# Includes U+2298 (observed disabled) and lookalike U+2300.
_STATUS_DECORATION_RE = re.compile(
    r"^[\s\u2713\u2714\u00d7\u2717\u2718\u23f8\u2298\u2300!\-]+",
)
_MCP_EMPTY_MESSAGE = "No MCP servers configured. Use `claude mcp add` to add a server."
# Exact observed health-check lines only (ellipsis unicode or ASCII). A bare
# ``startswith`` would swallow unrecognized lines such as
# ``Checking MCP server healthy …`` and allow a false VALID inventory.
_MCP_HEALTH_LINES = frozenset(
    {
        "Checking MCP server health…",
        "Checking MCP server health...",
    },
)
_PLUGIN_EMPTY_MESSAGE = "No plugins installed. Use `claude plugin install` to install a plugin."
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(authorization:\s*)\S+"),
    re.compile(r"(?i)(bearer\s+)\S+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)\S+"),
    re.compile(r"(?i)((?:password|passwd|secret|token)\s*[:=]\s*)\S+"),
    re.compile(
        r'(?i)((?:password|passwd|secret|token|api[_-]?key|email|orgid)"\s*:\s*")[^"]*',
    ),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # Home-directory usernames in absolute paths (Linux / macOS). Applied only
    # via ``redact_sensitive`` at observation boundaries — never before parse.
    re.compile(r"(?i)(/home/)([^/\s\"']+)"),
    re.compile(r"(?i)(/Users/)([^/\s\"']+)"),
)

_STATUS_ALIASES: dict[str, str] = {
    "connected": "connected",
    "connected · tools fetch failed": "connected_tools_failed",
    "needs authentication": "needs_auth",
    "pending approval (run `claude` to approve)": "pending_approval",
    "disabled for this project (re-enable via /mcp)": "disabled",
    "rejected (see disabledmcpjsonservers in settings)": "rejected",
    "failed to connect": "failed",
    "not configured": "not_configured",
}

MAX_OBSERVATION_LENGTH = 240


class ParseOutcome(StrEnum):
    """Whether CLI stdout matched a known, usable discovery format."""

    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class McpServerObservation:
    """One MCP server row parsed from ``claude mcp list``."""

    name: str
    status: str
    transport: str
    raw_status: str = ""


@dataclass(frozen=True, slots=True)
class McpListParseResult:
    """Validated parse of ``claude mcp list`` stdout."""

    outcome: ParseOutcome
    entries: tuple[McpServerObservation, ...]


@dataclass(frozen=True, slots=True)
class AuthObservation:
    """Auth axes extracted from ``claude auth status --json`` (no secrets)."""

    logged_in: bool
    auth_method: str
    api_provider: str | None = None


@dataclass(frozen=True, slots=True)
class AuthParseResult:
    """Validated parse of Claude auth status JSON."""

    outcome: ParseOutcome
    auth: AuthObservation | None = None


@dataclass(frozen=True, slots=True)
class PluginListParseResult:
    """Validated parse of ``claude plugin list`` stdout."""

    outcome: ParseOutcome
    empty: bool = False


def redact_sensitive(text: str) -> str:
    """Redact credentials, emails, and home-path usernames from observations.

    Does not mutate CLI stdout before parsers run; callers apply this at the
    observation/evidence boundary (and on timeout captures that are never
    parsed as structured inventories).
    """
    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    if len(redacted) > MAX_OBSERVATION_LENGTH:
        return redacted[: MAX_OBSERVATION_LENGTH - 3] + "..."
    return redacted


def parse_version(stdout: str) -> str | None:
    """Parse observed ``X.Y.Z (Claude Code)`` stdout, or ``None`` if unknown."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    match = _VERSION_RE.fullmatch(lines[0])
    if match is None:
        return None
    return match.group(1)


def parse_auth_status_json(stdout: str) -> AuthParseResult:
    """Parse ``claude auth status --json`` into a secret-free observation.

    Requires boolean ``loggedIn`` and string ``authMethod``. Optional
    ``apiProvider`` is retained when it is a non-empty string. Email, org
    identifiers, and filesystem paths are never kept on the observation.
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return AuthParseResult(outcome=ParseOutcome.INVALID)
    if not isinstance(payload, dict):
        return AuthParseResult(outcome=ParseOutcome.INVALID)
    logged_in = payload.get("loggedIn")
    auth_method = payload.get("authMethod")
    if not isinstance(logged_in, bool):
        return AuthParseResult(outcome=ParseOutcome.INVALID)
    if not isinstance(auth_method, str) or not auth_method.strip():
        return AuthParseResult(outcome=ParseOutcome.INVALID)
    api_provider = payload.get("apiProvider")
    provider: str | None = None
    if isinstance(api_provider, str) and api_provider.strip():
        provider = api_provider.strip()
    return AuthParseResult(
        outcome=ParseOutcome.VALID,
        auth=AuthObservation(
            logged_in=logged_in,
            auth_method=auth_method.strip(),
            api_provider=provider,
        ),
    )


def parse_mcp_list(stdout: str) -> McpListParseResult:
    """Parse ``claude mcp list`` rows into a validated result.

    Recognized auxiliary lines are the observed empty-inventory message and the
    exact health-check header forms. An empty or whitespace-only stdout is
    ``INVALID``: absence of text is not evidence of an empty inventory. A
    health header with zero parseable rows and without the empty-inventory
    message is ``INVALID`` (covers WebSocket-invisible inventories). Every
    non-auxiliary line must be a fully recognized MCP row; unknown statuses,
    duplicate names, or unrecognized non-header lines invalidate the whole
    list. ``VALID`` empty requires the observed empty-inventory message.
    Empty-inventory message combined with one or more server rows is
    contradictory and therefore ``INVALID`` (fail-closed).
    """
    raw_lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not raw_lines:
        return McpListParseResult(outcome=ParseOutcome.INVALID, entries=())

    saw_empty_message = False
    entries: list[McpServerObservation] = []
    seen_names: set[str] = set()
    for line in raw_lines:
        if line == _MCP_EMPTY_MESSAGE:
            saw_empty_message = True
            continue
        if line in _MCP_HEALTH_LINES:
            continue
        parsed = _parse_mcp_row(line)
        if parsed is None:
            return McpListParseResult(outcome=ParseOutcome.INVALID, entries=())
        if parsed.name in seen_names:
            return McpListParseResult(outcome=ParseOutcome.INVALID, entries=())
        seen_names.add(parsed.name)
        entries.append(parsed)

    return _finalize_mcp_list(saw_empty_message=saw_empty_message, entries=entries)


def _finalize_mcp_list(
    *,
    saw_empty_message: bool,
    entries: list[McpServerObservation],
) -> McpListParseResult:
    """Decide VALID/INVALID after scanning mcp list lines.

    Empty-inventory message plus server rows is contradictory → INVALID.
    Health header alone (observed for type=ws) without the empty message is
    INVALID. ``VALID`` empty requires the observed empty-inventory message.
    """
    if saw_empty_message and entries:
        return McpListParseResult(outcome=ParseOutcome.INVALID, entries=())
    if entries:
        return McpListParseResult(outcome=ParseOutcome.VALID, entries=tuple(entries))
    if saw_empty_message:
        return McpListParseResult(outcome=ParseOutcome.VALID, entries=())
    return McpListParseResult(outcome=ParseOutcome.INVALID, entries=())


def parse_plugin_list(stdout: str) -> PluginListParseResult:
    """Parse ``claude plugin list``; only the observed empty message is VALID."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return PluginListParseResult(outcome=ParseOutcome.INVALID)
    if lines == [_PLUGIN_EMPTY_MESSAGE]:
        return PluginListParseResult(outcome=ParseOutcome.VALID, empty=True)
    return PluginListParseResult(outcome=ParseOutcome.INVALID)


def _parse_mcp_row(line: str) -> McpServerObservation | None:
    """Return one MCP observation, or ``None`` when the row is unrecognized."""
    match = _MCP_ROW_RE.fullmatch(line)
    if match is None:
        return None
    name = match.group("name")
    if _MCP_NAME_RE.fullmatch(name) is None:
        return None
    body = match.group("body").strip()
    raw_status = match.group("status").strip()
    normalized = _normalize_mcp_status(raw_status)
    if normalized is None:
        return None
    transport = _infer_transport(body)
    return McpServerObservation(
        name=name,
        status=normalized,
        transport=transport,
        raw_status=raw_status,
    )


def _normalize_mcp_status(raw_status: str) -> str | None:
    """Map decorated Claude MCP status text to a stable token.

    After stripping leading status glyphs, the remaining phrase must match a
    known alias exactly. Em-dash issue suffixes (`` — …``) are not discarded:
    a known status plus unexpected trailing text does not normalize to that
    status. Observed fixtures use bare status phrases without issue suffixes.
    """
    core = _STATUS_DECORATION_RE.sub("", raw_status.strip()).strip()
    return _STATUS_ALIASES.get(core.lower())


def _infer_transport(body: str) -> str:
    """Infer transport label from the middle segment of an mcp list row."""
    if body.endswith("(HTTP)"):
        return "http"
    if body.endswith("(SSE)"):
        return "sse"
    if body.startswith(("http://", "https://", "ws://", "wss://")):
        return "url"
    return "stdio"
