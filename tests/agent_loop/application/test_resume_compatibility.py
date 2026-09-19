"""Tests for pure resume compatibility policy."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_loop.application import resume_compatibility

CURRENT_VERSION = 2


def _persisted(
    *,
    target: str = "repo-a",
    orchestrator: str = "orchestrator-a",
    version: int = CURRENT_VERSION,
    fingerprint: str | None = "same",
) -> resume_compatibility.PersistedResumeIdentity:
    return resume_compatibility.PersistedResumeIdentity(
        target_repository=target,
        orchestrator_repository=orchestrator,
        compatibility_version=version,
        orchestrator_fingerprint=fingerprint,
    )


def _current(
    *,
    target: str = "repo-a",
    orchestrator: str = "orchestrator-a",
    fingerprint: str = "same",
) -> resume_compatibility.CurrentResumeIdentity:
    return resume_compatibility.CurrentResumeIdentity(
        target_repository=target,
        orchestrator_repository=orchestrator,
        compatibility_version=CURRENT_VERSION,
        orchestrator_fingerprint=fingerprint,
    )


@pytest.mark.parametrize(
    ("persisted_fingerprint", "current_fingerprint", "reason"),
    [
        ("same", "same", "EXACT_ORCHESTRATOR_MATCH"),
        ("old", "new", "COMPATIBLE_CODE_CHANGE"),
    ],
)
def test_current_version_accepts_compatible_repository_identity(
    persisted_fingerprint: str,
    current_fingerprint: str,
    reason: str,
) -> None:
    """Current-schema runs accept exact identity and compatible code changes."""
    decision = resume_compatibility.decide_resume_compatibility(
        _persisted(fingerprint=persisted_fingerprint),
        _current(fingerprint=current_fingerprint),
    )

    assert decision.compatible is True
    assert decision.requires_migration is False
    assert decision.reason_codes == (reason,)


@pytest.mark.parametrize(
    ("persisted", "reason"),
    [
        (_persisted(target="repo-b"), "TARGET_REPOSITORY_MISMATCH"),
        (_persisted(version=3), "FUTURE_STATE_VERSION"),
        (_persisted(version=0), "STATE_MIGRATION_MISSING"),
        (
            _persisted(orchestrator="orchestrator-b"),
            "AMBIGUOUS_EXTERNAL_ORCHESTRATOR_ROOT",
        ),
    ],
)
def test_resume_policy_rejects_incompatible_identity(
    persisted: resume_compatibility.PersistedResumeIdentity,
    reason: str,
) -> None:
    """Unsafe repository or schema identities return their stable reason."""
    decision = resume_compatibility.decide_resume_compatibility(persisted, _current())

    assert decision.compatible is False
    assert decision.requires_migration is False
    assert decision.reason_codes == (reason,)


@pytest.mark.parametrize(
    "orchestrator",
    ["legacy:no persistido", "repo-a", "orchestrator-a"],
)
def test_v1_identity_selects_registered_migration(orchestrator: str) -> None:
    """Every supported v1 root representation selects the bounded v1 migration."""
    decision = resume_compatibility.decide_resume_compatibility(
        _persisted(orchestrator=orchestrator, version=1, fingerprint=None),
        _current(fingerprint="new"),
    )

    assert decision.compatible is True
    assert decision.requires_migration is True
    assert decision.migration_path == ("v1_to_v2",)
    assert decision.reason_codes == ("STATE_MIGRATION_REQUIRED",)


def test_current_schema_normalizes_legacy_target_root() -> None:
    """A current-schema target recorded as orchestrator gets one normalization."""
    decision = resume_compatibility.decide_resume_compatibility(
        _persisted(orchestrator="repo-a"),
        _current(orchestrator="orchestrator-a"),
    )

    assert decision.compatible is True
    assert decision.requires_migration is True
    assert decision.migration_path == ("normalize_legacy_root_context",)
    assert decision.reason_codes == ("LEGACY_ORCHESTRATOR_ROOT_WAS_TARGET_ROOT",)


def test_resume_policy_is_deterministic_and_infrastructure_free() -> None:
    """Equal value inputs produce equal decisions without infrastructure imports."""
    persisted = _persisted(fingerprint="old")
    current = _current(fingerprint="new")

    assert resume_compatibility.decide_resume_compatibility(
        persisted,
        current,
    ) == resume_compatibility.decide_resume_compatibility(persisted, current)

    source = Path(resume_compatibility.__file__).read_text(encoding="utf-8")
    imported = {node.module or "" for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)}
    imported.update(
        alias.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import) for alias in node.names
    )
    assert not imported.intersection(
        {"pathlib", "os", "subprocess", "scripts", "scripts.agent_loop"},
    )
