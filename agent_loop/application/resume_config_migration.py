"""Pure transformation of durable resume configuration metadata."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_loop.application.resume_compatibility import ResumeCompatibilityDecision

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type ResumeHistoryEntry = dict[str, JsonValue]

_KNOWN_MIGRATIONS = {
    ("v1_to_v2",),
    ("normalize_legacy_root_context",),
}


@dataclass(frozen=True)
class ResumeConfigMetadata:
    """Resume metadata transformed independently from its durable container."""

    orchestrator_root: str
    external_orchestrator_mode: bool
    orchestrator_version: str
    resume_compatibility_version: int
    orchestrator_fingerprint: str | None
    orchestrator_git_commit: str | None
    orchestrator_dirty: bool | None
    orchestrator_history: tuple[ResumeHistoryEntry, ...]


@dataclass(frozen=True)
class ResumeMigrationInputs:
    """Current identities and Git metadata already resolved by infrastructure."""

    decision: ResumeCompatibilityDecision
    persisted_orchestrator_identity: str
    current_orchestrator_identity: str
    current_orchestrator_root: str
    external_orchestrator_mode: bool
    orchestrator_version: str
    resume_compatibility_version: int
    orchestrator_fingerprint: str
    orchestrator_git_commit: str | None
    orchestrator_dirty: bool | None


def validate_resume_config_migration(decision: ResumeCompatibilityDecision) -> None:
    """Reject incompatible decisions and unknown migration paths."""
    if not decision.compatible:
        message = "No se puede migrar una decisión de resume incompatible."
        raise ValueError(message)
    if decision.requires_migration and decision.migration_path not in _KNOWN_MIGRATIONS:
        message = f"Migración de resume desconocida: {decision.migration_path}"
        raise ValueError(message)


def resume_config_requires_upgrade(
    config: ResumeConfigMetadata,
    inputs: ResumeMigrationInputs,
) -> bool:
    """Return whether normalized current metadata differs from persisted data."""
    return not (
        config.resume_compatibility_version == inputs.resume_compatibility_version
        and config.orchestrator_fingerprint == inputs.orchestrator_fingerprint
        and inputs.persisted_orchestrator_identity == inputs.current_orchestrator_identity
    )


def migrate_resume_config(
    config: ResumeConfigMetadata,
    inputs: ResumeMigrationInputs,
) -> ResumeConfigMetadata:
    """Apply one deterministic, idempotent compatible metadata upgrade."""
    validate_resume_config_migration(inputs.decision)
    if not resume_config_requires_upgrade(config, inputs):
        return config

    old_entry: ResumeHistoryEntry = {
        "version": config.orchestrator_version,
        "resume_compatibility_version": config.resume_compatibility_version,
        "fingerprint": config.orchestrator_fingerprint,
        "root": config.orchestrator_root,
    }
    new_entry: ResumeHistoryEntry = {
        "version": inputs.orchestrator_version,
        "resume_compatibility_version": inputs.resume_compatibility_version,
        "fingerprint": inputs.orchestrator_fingerprint,
        "root": inputs.current_orchestrator_root,
        "migration": "→".join(inputs.decision.migration_path),
    }
    history = list(config.orchestrator_history)
    for entry in (old_entry, new_entry):
        if entry not in history:
            history.append(entry)
    return replace(
        config,
        orchestrator_root=inputs.current_orchestrator_root,
        external_orchestrator_mode=inputs.external_orchestrator_mode,
        orchestrator_version=inputs.orchestrator_version,
        resume_compatibility_version=inputs.resume_compatibility_version,
        orchestrator_fingerprint=inputs.orchestrator_fingerprint,
        orchestrator_git_commit=inputs.orchestrator_git_commit,
        orchestrator_dirty=inputs.orchestrator_dirty,
        orchestrator_history=tuple(history),
    )
