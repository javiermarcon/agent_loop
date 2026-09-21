"""Application coordination for one resume invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class ResumeCatalogResult[CatalogT]:
    """Return a target-scoped catalog or an unchanged terminal exit."""

    catalog: CatalogT | None
    exit_code: int | None


@dataclass(frozen=True)
class ResumeCandidateResult[CandidateT, StoreT]:
    """Return one prepared candidate and its store, or a terminal exit."""

    candidate: CandidateT | None
    store: StoreT | None
    exit_code: int | None


@dataclass(frozen=True)
class ResumeConfigResult[CandidateT, ConfigT]:
    """Return a compatible candidate/config pair, or a terminal exit."""

    candidate: CandidateT | None
    config: ConfigT | None
    exit_code: int | None


@dataclass(frozen=True)
class ResumePrelockResult[CandidateT]:
    """Return the candidate approved for ownership, or a pre-lock exit."""

    candidate: CandidateT | None
    exit_code: int | None


@dataclass(frozen=True)
class ResumeInvocationConfiguration[LockT]:
    """Return a terminal exit or ownership transferred to the lifecycle."""

    exit_code: int | None
    lock: LockT | None


@dataclass(frozen=True)
class ResumeDiscoveryCollaborators[RequestT, TargetT, CatalogT, CandidateT, StoreT]:
    """Catalog, presentation, and existing candidate-preparation operations."""

    resolve_catalog: Callable[
        [RequestT, TargetT | None],
        ResumeCatalogResult[CatalogT],
    ]
    list_candidates: Callable[[CatalogT, TargetT], None]
    prepare_candidate: Callable[
        [RequestT, CatalogT, TargetT],
        ResumeCandidateResult[CandidateT, StoreT],
    ]


@dataclass(frozen=True)
class ResumeConfigurationCollaborators[RequestT, CandidateT, StoreT, ConfigT]:
    """Config compatibility, override, and context-restoration operations."""

    load_config: Callable[[CandidateT], ConfigT]
    ensure_compatible_config: Callable[
        [RequestT, CandidateT, StoreT, ConfigT],
        ResumeConfigResult[CandidateT, ConfigT],
    ]
    restore_options: Callable[[RequestT, ConfigT], None]
    apply_override: Callable[[RequestT, ConfigT, CandidateT], ConfigT]
    restore_context: Callable[[RequestT, ConfigT], int | None]


@dataclass(frozen=True)
class ResumeOwnershipCollaborators[RequestT, CandidateT, StoreT, ConfigT, LockT]:
    """Pre-lock interaction and concrete ownership operations."""

    prepare_lock: Callable[
        [RequestT, CandidateT, StoreT, ConfigT],
        ResumePrelockResult[CandidateT],
    ]
    acquire_lock: Callable[
        [RequestT, CandidateT, StoreT, ConfigT],
        ResumeInvocationConfiguration[LockT],
    ]


@dataclass(frozen=True)
class ResumeInvocationCollaborators[
    RequestT,
    TargetT,
    CatalogT,
    CandidateT,
    StoreT,
    ConfigT,
    LockT,
]:
    """Group cohesive collaborators used by the invocation coordinator."""

    discovery: ResumeDiscoveryCollaborators[
        RequestT,
        TargetT,
        CatalogT,
        CandidateT,
        StoreT,
    ]
    configuration: ResumeConfigurationCollaborators[
        RequestT,
        CandidateT,
        StoreT,
        ConfigT,
    ]
    ownership: ResumeOwnershipCollaborators[
        RequestT,
        CandidateT,
        StoreT,
        ConfigT,
        LockT,
    ]


def configure_prepared_resume[
    RequestT,
    CandidateT,
    StoreT,
    ConfigT,
    LockT,
](
    request: RequestT,
    candidate: CandidateT,
    store: StoreT,
    configuration: ResumeConfigurationCollaborators[
        RequestT,
        CandidateT,
        StoreT,
        ConfigT,
    ],
    ownership: ResumeOwnershipCollaborators[
        RequestT,
        CandidateT,
        StoreT,
        ConfigT,
        LockT,
    ],
) -> ResumeInvocationConfiguration[LockT]:
    """Restore configuration and transfer acquired ownership to lifecycle."""
    loaded_config = configuration.load_config(candidate)
    compatible = configuration.ensure_compatible_config(
        request,
        candidate,
        store,
        loaded_config,
    )
    if compatible.exit_code is not None or compatible.candidate is None or compatible.config is None:
        return ResumeInvocationConfiguration(compatible.exit_code, None)
    candidate = compatible.candidate
    config = compatible.config
    configuration.restore_options(request, config)
    config = configuration.apply_override(request, config, candidate)
    context_exit = configuration.restore_context(request, config)
    if context_exit is not None:
        return ResumeInvocationConfiguration(context_exit, None)
    return configure_resume_ownership(
        request,
        candidate,
        store,
        config,
        ownership,
    )


def configure_resume_ownership[
    RequestT,
    CandidateT,
    StoreT,
    ConfigT,
    LockT,
](
    request: RequestT,
    candidate: CandidateT,
    store: StoreT,
    config: ConfigT,
    ownership: ResumeOwnershipCollaborators[
        RequestT,
        CandidateT,
        StoreT,
        ConfigT,
        LockT,
    ],
) -> ResumeInvocationConfiguration[LockT]:
    """Complete pre-lock interaction before invoking lock infrastructure."""
    prelock = ownership.prepare_lock(request, candidate, store, config)
    if prelock.exit_code is not None or prelock.candidate is None:
        return ResumeInvocationConfiguration(prelock.exit_code, None)
    return ownership.acquire_lock(request, prelock.candidate, store, config)


def configure_resume_invocation[
    RequestT,
    TargetT,
    CatalogT,
    CandidateT,
    StoreT,
    ConfigT,
    LockT,
](
    request: RequestT,
    target: TargetT | None,
    *,
    list_requested: bool,
    missing_target_exit: int,
    collaborators: ResumeInvocationCollaborators[
        RequestT,
        TargetT,
        CatalogT,
        CandidateT,
        StoreT,
        ConfigT,
        LockT,
    ],
) -> ResumeInvocationConfiguration[LockT]:
    """Coordinate discovery, restoration, interaction, and lock acquisition."""
    catalog_result = collaborators.discovery.resolve_catalog(request, target)
    if catalog_result.exit_code is not None or catalog_result.catalog is None:
        return ResumeInvocationConfiguration(catalog_result.exit_code, None)
    if target is None:
        return ResumeInvocationConfiguration(missing_target_exit, None)
    if list_requested:
        collaborators.discovery.list_candidates(catalog_result.catalog, target)
        return ResumeInvocationConfiguration(0, None)
    prepared = collaborators.discovery.prepare_candidate(
        request,
        catalog_result.catalog,
        target,
    )
    if prepared.exit_code is not None or prepared.candidate is None or prepared.store is None:
        return ResumeInvocationConfiguration(prepared.exit_code, None)
    return configure_prepared_resume(
        request,
        prepared.candidate,
        prepared.store,
        collaborators.configuration,
        collaborators.ownership,
    )
