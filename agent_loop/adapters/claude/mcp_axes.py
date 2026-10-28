"""Map Claude MCP status tokens to S7 capability axes (no over-claiming)."""

from __future__ import annotations

from dataclasses import dataclass

from agent_loop.domain.capabilities import CapabilityState

_YES = CapabilityState.YES
_NO = CapabilityState.NO
_UNKNOWN = CapabilityState.UNKNOWN


@dataclass(frozen=True, slots=True)
class McpCapabilityAxes:
    """Tri-state axes derived from one Claude MCP status token."""

    configured: CapabilityState
    enabled: CapabilityState
    available: CapabilityState
    connected: CapabilityState
    healthy: CapabilityState
    authorized: CapabilityState
    allowed: CapabilityState


_AXES_BY_STATUS: dict[str, McpCapabilityAxes] = {
    # CLI reports connection only; tool health and auth stay unknown.
    "connected": McpCapabilityAxes(
        configured=_YES,
        enabled=_YES,
        available=_YES,
        connected=_YES,
        healthy=_UNKNOWN,
        authorized=_UNKNOWN,
        allowed=_UNKNOWN,
    ),
    "connected_tools_failed": McpCapabilityAxes(
        configured=_YES,
        enabled=_YES,
        available=_NO,
        connected=_YES,
        healthy=_NO,
        authorized=_UNKNOWN,
        allowed=_UNKNOWN,
    ),
    "needs_auth": McpCapabilityAxes(
        configured=_YES,
        enabled=_YES,
        available=_NO,
        connected=_NO,
        healthy=_UNKNOWN,
        authorized=_NO,
        allowed=_UNKNOWN,
    ),
    "pending_approval": McpCapabilityAxes(
        configured=_YES,
        enabled=_UNKNOWN,
        available=_NO,
        connected=_NO,
        healthy=_UNKNOWN,
        authorized=_UNKNOWN,
        allowed=_NO,
    ),
    "rejected": McpCapabilityAxes(
        configured=_YES,
        enabled=_NO,
        available=_NO,
        connected=_NO,
        healthy=_UNKNOWN,
        authorized=_UNKNOWN,
        allowed=_NO,
    ),
    "disabled": McpCapabilityAxes(
        configured=_YES,
        enabled=_NO,
        available=_NO,
        connected=_NO,
        healthy=_UNKNOWN,
        authorized=_UNKNOWN,
        allowed=_UNKNOWN,
    ),
    "failed": McpCapabilityAxes(
        configured=_YES,
        enabled=_YES,
        available=_NO,
        connected=_NO,
        healthy=_NO,
        authorized=_UNKNOWN,
        allowed=_UNKNOWN,
    ),
    "not_configured": McpCapabilityAxes(
        configured=_NO,
        enabled=_UNKNOWN,
        available=_NO,
        connected=_NO,
        healthy=_UNKNOWN,
        authorized=_UNKNOWN,
        allowed=_UNKNOWN,
    ),
}

_FALLBACK_AXES = McpCapabilityAxes(
    configured=_UNKNOWN,
    enabled=_UNKNOWN,
    available=_UNKNOWN,
    connected=_UNKNOWN,
    healthy=_UNKNOWN,
    authorized=_UNKNOWN,
    allowed=_UNKNOWN,
)


def mcp_axes_for_status(status: str) -> McpCapabilityAxes:
    """Map normalized Claude MCP status tokens to capability axes."""
    return _AXES_BY_STATUS.get(status, _FALLBACK_AXES)
