"""Application coordination for one resume invocation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from typing import Protocol
from typing import Self

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


class ResumeLockCapability(Protocol):
    """Exclusive resume lock capability supplied by infrastructure."""

    def release(self) -> None:
        """Release concrete ownership."""


class ResumeLockOwnershipState(Enum):
    """Adapter-side ownership state before lifecycle transfer."""

    ACQUIRED = "acquired"
    RELEASED = "released"
    TRANSFERRED = "transferred"


@dataclass
class ResumeLockOwnership[LockT: ResumeLockCapability]:
    """Track the single release-or-transfer decision for an acquired lock."""

    _lock: LockT
    _state: ResumeLockOwnershipState

    @classmethod
    def acquired(cls, lock: LockT) -> Self:
        """Create adapter ownership immediately after concrete acquisition."""
        return cls(lock, ResumeLockOwnershipState.ACQUIRED)

    @property
    def state(self) -> ResumeLockOwnershipState:
        """Expose the current ownership disposition for explicit coordination."""
        return self._state

    @property
    def held_lock(self) -> LockT:
        """Return the lock while this adapter still owns it."""
        if self._state is not ResumeLockOwnershipState.ACQUIRED:
            message = f"Resume lock ownership already {self._state.value}."
            raise RuntimeError(message)
        return self._lock

    def release(self) -> None:
        """Release concrete ownership exactly once."""
        lock = self.held_lock
        lock.release()
        self._state = ResumeLockOwnershipState.RELEASED

    def release_if_acquired(self) -> None:
        """Release only when rollback still owns the concrete lock."""
        if self._state is ResumeLockOwnershipState.ACQUIRED:
            self.release()

    def transfer(self) -> LockT:
        """Transfer unreleased ownership exactly once to the lifecycle."""
        lock = self.held_lock
        self._state = ResumeLockOwnershipState.TRANSFERRED
        return lock


class ResumeInvocationDisposition(Enum):
    """Exclusive terminal or ownership state returned by resume coordination."""

    CONTINUE = "continue"
    TERMINAL = "terminal"
    TRANSFERRED = "transferred"


@dataclass(frozen=True)
class ResumeInvocationConfiguration[LockT]:
    """Represent exactly one resume outcome without ambiguous lock ownership."""

    disposition: ResumeInvocationDisposition
    exit_code: int | None
    lock: LockT | None

    def __post_init__(self) -> None:
        if self.disposition is ResumeInvocationDisposition.CONTINUE:
            valid = self.exit_code is None and self.lock is None
        elif self.disposition is ResumeInvocationDisposition.TERMINAL:
            valid = self.exit_code is not None and self.lock is None
        else:
            valid = self.exit_code is None and self.lock is not None
        if not valid:
            message = (
                "Resume outcome must be continue without values, terminal exit without lock, "
                "or transferred lock without exit."
            )
            raise ValueError(message)

    @classmethod
    def continue_without_ownership[ResultLockT](
        cls,
    ) -> ResumeInvocationConfiguration[ResultLockT]:
        """Continue a non-resume invocation without lock ownership."""
        return ResumeInvocationConfiguration(
            ResumeInvocationDisposition.CONTINUE,
            None,
            None,
        )

    @classmethod
    def terminal[ResultLockT](
        cls,
        exit_code: int,
    ) -> ResumeInvocationConfiguration[ResultLockT]:
        """Return a terminal exit after proving no lock remains owned."""
        return ResumeInvocationConfiguration(
            ResumeInvocationDisposition.TERMINAL,
            exit_code,
            None,
        )

    @classmethod
    def transferred[TransferredLockT](
        cls,
        lock: TransferredLockT,
    ) -> ResumeInvocationConfiguration[TransferredLockT]:
        """Return lock ownership transferred to the lifecycle."""
        return ResumeInvocationConfiguration(
            ResumeInvocationDisposition.TRANSFERRED,
            None,
            lock,
        )


def _without_ownership[LockT](
    exit_code: int | None,
) -> ResumeInvocationConfiguration[LockT]:
    if exit_code is None:
        return ResumeInvocationConfiguration.continue_without_ownership()
    return ResumeInvocationConfiguration.terminal(exit_code)


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
        return _without_ownership(compatible.exit_code)
    candidate = compatible.candidate
    config = compatible.config
    configuration.restore_options(request, config)
    config = configuration.apply_override(request, config, candidate)
    context_exit = configuration.restore_context(request, config)
    if context_exit is not None:
        return ResumeInvocationConfiguration.terminal(context_exit)
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
        return _without_ownership(prelock.exit_code)
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
        return _without_ownership(catalog_result.exit_code)
    if target is None:
        return ResumeInvocationConfiguration.terminal(missing_target_exit)
    if list_requested:
        collaborators.discovery.list_candidates(catalog_result.catalog, target)
        return ResumeInvocationConfiguration.terminal(0)
    prepared = collaborators.discovery.prepare_candidate(
        request,
        catalog_result.catalog,
        target,
    )
    if prepared.exit_code is not None or prepared.candidate is None or prepared.store is None:
        return _without_ownership(prepared.exit_code)
    return configure_prepared_resume(
        request,
        prepared.candidate,
        prepared.store,
        collaborators.configuration,
        collaborators.ownership,
    )
