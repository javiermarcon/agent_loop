"""Application orchestration for preparing one selected resume candidate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class ResumePreparationCollaborators[RequestT, CatalogT, TargetT, CandidateT, StoreT]:
    """Explicit operations implemented by the legacy resume adapters."""

    select_candidate: Callable[
        [RequestT, CatalogT, TargetT],
        tuple[CandidateT | None, int | None],
    ]
    open_store: Callable[[CandidateT], StoreT]
    repair_candidate: Callable[
        [CandidateT, StoreT],
        tuple[CandidateT | None, int | None],
    ]
    validate_candidate: Callable[[CandidateT, TargetT], int | None]


@dataclass(frozen=True)
class ResumePreparationResult[CandidateT, StoreT]:
    """Prepared values or the unchanged exit selected by one stage."""

    candidate: CandidateT | None
    store: StoreT | None
    exit_code: int | None


def prepare_resume_candidate[RequestT, CatalogT, TargetT, CandidateT, StoreT](
    request: RequestT,
    catalog: CatalogT,
    target: TargetT,
    collaborators: ResumePreparationCollaborators[
        RequestT,
        CatalogT,
        TargetT,
        CandidateT,
        StoreT,
    ],
) -> ResumePreparationResult[CandidateT, StoreT]:
    """Coordinate selection, store creation, repair, and validation in order."""
    candidate, selection_exit = collaborators.select_candidate(
        request,
        catalog,
        target,
    )
    if candidate is None:
        return ResumePreparationResult(None, None, selection_exit)
    store = collaborators.open_store(candidate)
    repaired, repair_exit = collaborators.repair_candidate(candidate, store)
    if repaired is None:
        return ResumePreparationResult(None, None, repair_exit)
    validation_exit = collaborators.validate_candidate(repaired, target)
    if validation_exit is not None:
        return ResumePreparationResult(None, None, validation_exit)
    return ResumePreparationResult(repaired, store, None)
