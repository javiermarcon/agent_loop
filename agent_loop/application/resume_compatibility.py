"""Pure policy for deciding durable resume compatibility."""

from __future__ import annotations

from dataclasses import dataclass

_LEGACY_UNPERSISTED_ROOT = "legacy:no persistido"
_V1_TO_V2 = (1, 2)


@dataclass(frozen=True)
class PersistedResumeIdentity:
    """Resume-relevant identity loaded and normalized by infrastructure."""

    target_repository: str
    orchestrator_repository: str
    compatibility_version: int
    orchestrator_fingerprint: str | None


@dataclass(frozen=True)
class CurrentResumeIdentity:
    """Current repository identity resolved before policy evaluation."""

    target_repository: str
    orchestrator_repository: str
    compatibility_version: int
    orchestrator_fingerprint: str


@dataclass(frozen=True)
class ResumeCompatibilityDecision:
    """Describe whether persisted orchestration metadata can be resumed."""

    compatible: bool
    requires_migration: bool
    from_version: int
    to_version: int
    migration_path: tuple[str, ...]
    reason_codes: tuple[str, ...]
    explanation: str


def decide_resume_compatibility(
    persisted: PersistedResumeIdentity,
    current: CurrentResumeIdentity,
) -> ResumeCompatibilityDecision:
    """Decide compatibility from normalized identities without I/O."""
    from_version = persisted.compatibility_version
    to_version = current.compatibility_version
    if persisted.target_repository != current.target_repository:
        return ResumeCompatibilityDecision(
            compatible=False,
            requires_migration=False,
            from_version=from_version,
            to_version=to_version,
            migration_path=(),
            reason_codes=("TARGET_REPOSITORY_MISMATCH",),
            explanation="El target repository persistido no coincide con el actual.",
        )
    if from_version > to_version:
        return ResumeCompatibilityDecision(
            compatible=False,
            requires_migration=False,
            from_version=from_version,
            to_version=to_version,
            migration_path=(),
            reason_codes=("FUTURE_STATE_VERSION",),
            explanation="La corrida usa una versión de resume futura.",
        )
    if from_version == to_version:
        return _decide_same_version(persisted, current)
    if (from_version, to_version) == _V1_TO_V2 and persisted.orchestrator_repository in {
        _LEGACY_UNPERSISTED_ROOT,
        persisted.target_repository,
        current.orchestrator_repository,
    }:
        return ResumeCompatibilityDecision(
            compatible=True,
            requires_migration=True,
            from_version=from_version,
            to_version=to_version,
            migration_path=("v1_to_v2",),
            reason_codes=("STATE_MIGRATION_REQUIRED",),
            explanation="La metadata legacy requiere migración v1→v2.",
        )
    return ResumeCompatibilityDecision(
        compatible=False,
        requires_migration=False,
        from_version=from_version,
        to_version=to_version,
        migration_path=(),
        reason_codes=("STATE_MIGRATION_MISSING",),
        explanation="No existe una migración segura para esta versión de resume.",
    )


def _decide_same_version(
    persisted: PersistedResumeIdentity,
    current: CurrentResumeIdentity,
) -> ResumeCompatibilityDecision:
    """Evaluate current-schema orchestrator identity and its legacy exception."""
    if persisted.orchestrator_repository not in {
        current.orchestrator_repository,
        persisted.target_repository,
    }:
        return ResumeCompatibilityDecision(
            compatible=False,
            requires_migration=False,
            from_version=persisted.compatibility_version,
            to_version=current.compatibility_version,
            migration_path=(),
            reason_codes=("AMBIGUOUS_EXTERNAL_ORCHESTRATOR_ROOT",),
            explanation="El root del orquestador no coincide ni es legacy.",
        )
    if (
        persisted.orchestrator_repository == persisted.target_repository
        and current.orchestrator_repository != current.target_repository
    ):
        return ResumeCompatibilityDecision(
            compatible=True,
            requires_migration=True,
            from_version=persisted.compatibility_version,
            to_version=current.compatibility_version,
            migration_path=("normalize_legacy_root_context",),
            reason_codes=("LEGACY_ORCHESTRATOR_ROOT_WAS_TARGET_ROOT",),
            explanation="El root legacy del orquestador requiere normalización.",
        )
    exact = persisted.orchestrator_fingerprint == current.orchestrator_fingerprint
    reason = "EXACT_ORCHESTRATOR_MATCH" if exact else "COMPATIBLE_CODE_CHANGE"
    return ResumeCompatibilityDecision(
        compatible=True,
        requires_migration=False,
        from_version=persisted.compatibility_version,
        to_version=current.compatibility_version,
        migration_path=(),
        reason_codes=(reason,),
        explanation="La versión durable de resume sigue siendo compatible.",
    )
