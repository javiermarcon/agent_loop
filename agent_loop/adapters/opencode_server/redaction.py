"""Secret redaction helpers for OpenCode Server evidence and errors."""

from __future__ import annotations

import re

_PASSWORD_PATTERNS = (
    re.compile(r"(?i)(server password\s+)(\S+)"),
    re.compile(r"(?i)(password=)(\S+)"),
    re.compile(r"(?i)(authorization:\s*basic\s+)(\S+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"),
)
_REDACTED = "<redacted>"


def redact_secret(value: str | None, *secrets: str | None) -> str:
    """Return ``value`` with configured secrets and common auth forms redacted."""
    if value is None:
        return ""
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, _REDACTED)
    for pattern in _PASSWORD_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}{_REDACTED}", redacted)
    return redacted


def redact_url_credentials(url: str) -> str:
    """Remove ``user:password@`` credentials embedded in a URL."""
    return re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1<redacted>@", url)
