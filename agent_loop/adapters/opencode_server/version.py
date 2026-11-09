"""Explicit OpenCode Server version compatibility policy.

The policy is fail-closed: a version that cannot be parsed is ``UNKNOWN`` and
never counts as supported. Only the verified server major/minor range is
accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_SEMVER = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:[-+][0-9A-Za-z.-]+)?$")


class VersionCompatibility(StrEnum):
    """Compatibility class for one observed server version."""

    SUPPORTED = "supported"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VersionPolicy:
    """Supported major/minor floor for the OpenCode Server HTTP API."""

    supported_major: int = 2
    minimum_minor: int = 2

    def __post_init__(self) -> None:
        if self.supported_major < 1:
            message = "VersionPolicy.supported_major must be positive"
            raise ValueError(message)
        if self.minimum_minor < 0:
            message = "VersionPolicy.minimum_minor must not be negative"
            raise ValueError(message)


DEFAULT_VERSION_POLICY = VersionPolicy()


def classify_server_version(
    version: str | None,
    policy: VersionPolicy = DEFAULT_VERSION_POLICY,
) -> VersionCompatibility:
    """Classify ``version`` against ``policy`` without inferring compatibility."""
    if version is None or not version.strip():
        return VersionCompatibility.UNKNOWN
    match = _SEMVER.fullmatch(version.strip().lstrip("v"))
    if match is None:
        return VersionCompatibility.UNKNOWN
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    if major != policy.supported_major:
        return VersionCompatibility.INCOMPATIBLE
    if minor < policy.minimum_minor:
        return VersionCompatibility.INCOMPATIBLE
    return VersionCompatibility.SUPPORTED
