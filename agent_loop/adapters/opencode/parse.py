"""Deterministic parsers for OpenCode CLI discovery outputs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum

# Observed OpenCode v2.1.4: a single line ``opencode vX.Y.Z``.
_VERSION_RE = re.compile(
    r"^opencode v(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?)$",
    re.IGNORECASE,
)
_MCP_LINE_RE = re.compile(
    r"^(?P<icon>[✓✗⚠○])\s+"
    r"(?P<name>[A-Za-z0-9._-]+)\s+"
    r"(?P<status>.+?)"
    r"(?P<oauth>\s*\(OAuth\))?\s*$",
)
# Observed empty ``mcp list`` (v2.1.4) prints this informational line.
_MCP_ALLOWED_AUXILIARY_LINES = frozenset({"No MCP servers configured"})
# Capability-safe ids for agents (same charset as MCP server names).
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Observed OpenCode v2.1.4 ``models --standalone`` ids are ``provider/model``;
# the model segment may include ``:`` (e.g. Ollama tags like ``gemma3:4b``).
_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._:-]+$")
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(authorization:\s*)\S+"),
    re.compile(r"(?i)(bearer\s+)\S+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)\S+"),
    re.compile(r"(?i)((?:password|passwd|secret|token)\s*[:=]\s*)\S+"),
    re.compile(
        r'(?i)((?:password|passwd|secret|token|api[_-]?key)"\s*:\s*")[^"]*',
    ),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b"),
)

_STATUS_ALIASES: dict[str, str] = {
    "connected": "connected",
    "disabled": "disabled",
    "failed": "failed",
    "not initialized": "not_initialized",
    "needs authentication": "needs_auth",
    "needs client registration": "needs_client_registration",
}

MAX_OBSERVATION_LENGTH = 240


class ParseOutcome(StrEnum):
    """Whether CLI stdout matched a known, usable discovery format."""

    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class McpServerObservation:
    """One MCP server row parsed from ``opencode mcp list``."""

    name: str
    status: str
    oauth_hint: bool = False
    raw_status: str = ""


@dataclass(frozen=True, slots=True)
class McpListParseResult:
    """Validated parse of ``opencode mcp list`` stdout."""

    outcome: ParseOutcome
    entries: tuple[McpServerObservation, ...]


@dataclass(frozen=True, slots=True)
class AgentObservation:
    """One agent entry parsed from ``opencode debug agents`` JSON."""

    agent_id: str
    name: str


@dataclass(frozen=True, slots=True)
class AgentsParseResult:
    """Validated parse of ``opencode debug agents`` stdout."""

    outcome: ParseOutcome
    agents: tuple[AgentObservation, ...]


@dataclass(frozen=True, slots=True)
class ModelsParseResult:
    """Validated parse of ``opencode models --standalone`` stdout."""

    outcome: ParseOutcome
    models: tuple[str, ...]


def redact_sensitive(text: str) -> str:
    """Redact credential-like fragments from adapter observations.

    Covers common Authorization/Bearer, ``sk-...``, api key / password /
    secret / token ``key=value`` and simple JSON ``"key": "value"`` forms.
    Not a universal secret detector.
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
    """Parse observed ``opencode vX.Y.Z`` stdout, or ``None`` if unknown."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    match = _VERSION_RE.fullmatch(lines[0])
    if match is None:
        return None
    return match.group(1)


def parse_mcp_list(stdout: str) -> McpListParseResult:
    """Parse compact ``opencode mcp list`` rows into a validated result.

    Empty stdout (or only the observed empty-list message) is a valid empty
    inventory. Every other non-empty line must be a fully recognized MCP row;
    unknown lines, unknown statuses, or duplicate server names make the whole
    result ``INVALID`` (no partial inventory). Identifier comparison is
    exact and case-sensitive.
    """
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return McpListParseResult(outcome=ParseOutcome.VALID, entries=())

    entries: list[McpServerObservation] = []
    seen_names: set[str] = set()
    for line in lines:
        if line in _MCP_ALLOWED_AUXILIARY_LINES:
            continue
        match = _MCP_LINE_RE.match(line)
        if match is None:
            return McpListParseResult(outcome=ParseOutcome.INVALID, entries=())
        raw_status = match.group("status").strip()
        normalized = _STATUS_ALIASES.get(raw_status.lower())
        if normalized is None:
            return McpListParseResult(outcome=ParseOutcome.INVALID, entries=())
        name = match.group("name")
        if name in seen_names:
            return McpListParseResult(outcome=ParseOutcome.INVALID, entries=())
        seen_names.add(name)
        entries.append(
            McpServerObservation(
                name=name,
                status=normalized,
                oauth_hint=bool(match.group("oauth")),
                raw_status=raw_status,
            ),
        )
    return McpListParseResult(outcome=ParseOutcome.VALID, entries=tuple(entries))


def parse_models(stdout: str) -> ModelsParseResult:
    """Parse ``provider/model`` lines from ``opencode models --standalone``.

    Observed OpenCode v2.1.4 always emits ``provider/model`` lines when the
    command succeeds; an empty or whitespace-only stdout has not been observed
    as a legitimate zero-model inventory, so it is ``INVALID``. Every
    non-empty line must match the observed pattern; any other line makes the
    whole result ``INVALID`` (no partial inventory).
    """
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return ModelsParseResult(outcome=ParseOutcome.INVALID, models=())

    models: list[str] = []
    for line in lines:
        if _MODEL_RE.fullmatch(line) is None:
            return ModelsParseResult(outcome=ParseOutcome.INVALID, models=())
        models.append(line)
    return ModelsParseResult(outcome=ParseOutcome.VALID, models=tuple(models))


def _agent_from_item(item: object) -> AgentObservation | None:
    """Return an agent observation, or ``None`` when the element is invalid."""
    if not isinstance(item, dict):
        return None
    agent_id = item.get("id")
    name = item.get("name")
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    cleaned_id = agent_id.strip()
    if _AGENT_ID_RE.fullmatch(cleaned_id) is None:
        return None
    return AgentObservation(agent_id=cleaned_id, name=name.strip())


def parse_agents_json(stdout: str) -> AgentsParseResult:
    """Parse ``opencode debug agents`` JSON into a validated result.

    ``[]`` is a valid empty inventory. Every array element must be an object
    with non-empty capability-safe ``id`` and non-empty ``name``; invalid
    elements or duplicate ids (exact, case-sensitive) make the whole result
    ``INVALID``.
    """
    text = stdout.strip()
    if not text:
        return AgentsParseResult(outcome=ParseOutcome.INVALID, agents=())
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return AgentsParseResult(outcome=ParseOutcome.INVALID, agents=())
    if not isinstance(payload, list):
        return AgentsParseResult(outcome=ParseOutcome.INVALID, agents=())

    agents: list[AgentObservation] = []
    seen_ids: set[str] = set()
    for item in payload:
        agent = _agent_from_item(item)
        if agent is None:
            return AgentsParseResult(outcome=ParseOutcome.INVALID, agents=())
        if agent.agent_id in seen_ids:
            return AgentsParseResult(outcome=ParseOutcome.INVALID, agents=())
        seen_ids.add(agent.agent_id)
        agents.append(agent)
    return AgentsParseResult(outcome=ParseOutcome.VALID, agents=tuple(agents))
