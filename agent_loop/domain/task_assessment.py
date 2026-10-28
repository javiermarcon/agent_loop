"""Deterministic task assessment and capability-requirement derivation.

Pure domain policy for S11. Classifies complexity/risk/uncertainty from
declared, verifiable task signals and derives ``CapabilityRequirement`` values
using the existing effective-capability vocabulary. Does not call discovery,
runtimes, filesystem, or network. Does not select models or harnesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_loop.domain.capabilities import CapabilityAxis
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilityRequirement

_FILESYSTEM_READ = "filesystem.read"
_FILESYSTEM_WRITE = "filesystem.write"
_WORK_ITEM_READ = "work_item.notion.read"
_WORK_ITEM_WRITE = "work_item.notion.write"

_BASE_AXES = frozenset({CapabilityAxis.SUPPORTED, CapabilityAxis.AVAILABLE})
_AUTHORIZED_AXES = frozenset(
    {
        CapabilityAxis.SUPPORTED,
        CapabilityAxis.AVAILABLE,
        CapabilityAxis.AUTHORIZED,
    },
)


class TaskComplexity(StrEnum):
    """Ordinal task-complexity classes for control-plane policy."""

    MECHANICAL = "mechanical"
    LOCAL = "local"
    CROSS_MODULE = "cross_module"
    ARCHITECTURAL = "architectural"


class TaskRisk(StrEnum):
    """Ordinal task-risk classes for control-plane policy.

    ``CRITICAL`` is reserved for a future explicit contract. S11 floors raise
    at most to ``HIGH``; no deterministic rule invents ``CRITICAL``.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskUncertainty(StrEnum):
    """How clear the acceptance criteria and task evidence are."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AcceptanceClarity(StrEnum):
    """Declared clarity of acceptance criteria (input signal)."""

    CLEAR = "clear"
    INCOMPLETE = "incomplete"
    MAJOR_AMBIGUITY = "major_ambiguity"


class ImpactPresence(StrEnum):
    """Tri-state impact flag; ``UNKNOWN`` is not evidence of absence."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class AutonomyRequired(StrEnum):
    """How much unattended mutation the task policy should allow later."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_COMPLEXITY_RANK: dict[TaskComplexity, int] = {
    TaskComplexity.MECHANICAL: 0,
    TaskComplexity.LOCAL: 1,
    TaskComplexity.CROSS_MODULE: 2,
    TaskComplexity.ARCHITECTURAL: 3,
}
_RISK_RANK: dict[TaskRisk, int] = {
    TaskRisk.LOW: 0,
    TaskRisk.MEDIUM: 1,
    TaskRisk.HIGH: 2,
    TaskRisk.CRITICAL: 3,
}
_UNCERTAINTY_RANK: dict[TaskUncertainty, int] = {
    TaskUncertainty.LOW: 0,
    TaskUncertainty.MEDIUM: 1,
    TaskUncertainty.HIGH: 2,
}


@dataclass(frozen=True, slots=True)
class TaskAssessmentRequest:
    """Verifiable task signals supplied by the control plane.

    Callers declare impacts and scope; this module does not infer them from
    free text, repository scans, or runtime discovery.
    """

    baseline_complexity: TaskComplexity
    baseline_risk: TaskRisk
    baseline_uncertainty: TaskUncertainty
    acceptance_clarity: AcceptanceClarity
    impacted_domains: tuple[str, ...] = ()
    estimated_files: int | None = None
    architecture_impact: ImpactPresence = ImpactPresence.UNKNOWN
    schema_impact: ImpactPresence = ImpactPresence.UNKNOWN
    security_impact: ImpactPresence = ImpactPresence.UNKNOWN
    authentication_impact: ImpactPresence = ImpactPresence.UNKNOWN
    payments_impact: ImpactPresence = ImpactPresence.UNKNOWN
    concurrency_impact: ImpactPresence = ImpactPresence.UNKNOWN
    dependency_inversion_or_public_api: bool = False
    external_integrations: tuple[str, ...] = ()
    frontend_backend_boundary: ImpactPresence = ImpactPresence.UNKNOWN
    requires_work_item_read: bool = False
    requires_work_item_write: bool = False

    def __post_init__(self) -> None:
        if self.estimated_files is not None and self.estimated_files < 0:
            message = "TaskAssessmentRequest.estimated_files must be >= 0 when set"
            raise ValueError(message)
        for domain in self.impacted_domains:
            if not domain or not domain.strip() or domain != domain.strip():
                message = "impacted_domains entries must be non-empty stripped strings"
                raise ValueError(message)
        for name in self.external_integrations:
            if not name or not name.strip() or name != name.strip():
                message = "external_integrations entries must be non-empty stripped strings"
                raise ValueError(message)


@dataclass(frozen=True, slots=True)
class TaskAssessment:
    """Immutable assessment result: classification plus derived requirements."""

    complexity: TaskComplexity
    risk: TaskRisk
    uncertainty: TaskUncertainty
    impacted_domains: tuple[str, ...]
    estimated_files: int | None
    architecture_impact: ImpactPresence
    schema_impact: ImpactPresence
    security_impact: ImpactPresence
    authentication_impact: ImpactPresence
    payments_impact: ImpactPresence
    concurrency_impact: ImpactPresence
    external_integrations: tuple[str, ...]
    frontend_backend_boundary: ImpactPresence
    required_capabilities: tuple[CapabilityRequirement, ...]
    required_context: tuple[str, ...]
    autonomy_required: AutonomyRequired
    reasons: tuple[str, ...]


def assess_task(request: TaskAssessmentRequest) -> TaskAssessment:
    """Classify ``request`` and derive capability/context requirements.

    Deterministic floors may only raise baseline complexity/risk/uncertainty.
    Unknown impact flags are preserved and never treated as ``NO``.
    """
    reasons: list[str] = []
    complexity = _apply_complexity_floors(request, reasons)
    risk = _apply_risk_floors(request, reasons)
    uncertainty = _apply_uncertainty_floors(request, reasons)
    autonomy = _derive_autonomy(complexity, risk, reasons)
    capabilities, context = _derive_requirements(request, reasons)
    domains = tuple(sorted(request.impacted_domains))
    integrations = tuple(sorted(request.external_integrations))
    return TaskAssessment(
        complexity=complexity,
        risk=risk,
        uncertainty=uncertainty,
        impacted_domains=domains,
        estimated_files=request.estimated_files,
        architecture_impact=request.architecture_impact,
        schema_impact=request.schema_impact,
        security_impact=request.security_impact,
        authentication_impact=request.authentication_impact,
        payments_impact=request.payments_impact,
        concurrency_impact=request.concurrency_impact,
        external_integrations=integrations,
        frontend_backend_boundary=request.frontend_backend_boundary,
        required_capabilities=capabilities,
        required_context=context,
        autonomy_required=autonomy,
        reasons=tuple(reasons),
    )


def _max_complexity(current: TaskComplexity, floor: TaskComplexity) -> TaskComplexity:
    if _COMPLEXITY_RANK[floor] > _COMPLEXITY_RANK[current]:
        return floor
    return current


def _max_risk(current: TaskRisk, floor: TaskRisk) -> TaskRisk:
    if _RISK_RANK[floor] > _RISK_RANK[current]:
        return floor
    return current


def _apply_complexity_floors(
    request: TaskAssessmentRequest,
    reasons: list[str],
) -> TaskComplexity:
    complexity = request.baseline_complexity
    reasons.append(f"baseline_complexity={complexity.value}")
    if request.concurrency_impact is ImpactPresence.YES:
        raised = _max_complexity(complexity, TaskComplexity.CROSS_MODULE)
        if raised is not complexity:
            reasons.append("floor:concurrency→complexity>=cross_module")
        else:
            reasons.append("floor:concurrency_complexity_already_met")
        complexity = raised
    if request.dependency_inversion_or_public_api or request.architecture_impact is ImpactPresence.YES:
        raised = _max_complexity(complexity, TaskComplexity.ARCHITECTURAL)
        if raised is not complexity:
            reasons.append("floor:architecture_or_dependency_inversion→architectural")
        else:
            reasons.append("floor:architectural_complexity_already_met")
        complexity = raised
    return complexity


def _apply_risk_floors(
    request: TaskAssessmentRequest,
    reasons: list[str],
) -> TaskRisk:
    risk = request.baseline_risk
    reasons.append(f"baseline_risk={risk.value}")
    high_signals: list[str] = []
    if request.schema_impact is ImpactPresence.YES:
        high_signals.append("schema_or_migration")
    if request.security_impact is ImpactPresence.YES:
        high_signals.append("security")
    if request.authentication_impact is ImpactPresence.YES:
        high_signals.append("authentication")
    if request.payments_impact is ImpactPresence.YES:
        high_signals.append("payments")
    if request.concurrency_impact is ImpactPresence.YES:
        high_signals.append("concurrency")
    for signal in high_signals:
        raised = _max_risk(risk, TaskRisk.HIGH)
        if raised is not risk:
            reasons.append(f"floor:{signal}→risk>=high")
        else:
            reasons.append(f"floor:{signal}_risk_already_met")
        risk = raised
    # UNKNOWN impacts are not floors and are not treated as NO.
    for label, presence in (
        ("schema", request.schema_impact),
        ("security", request.security_impact),
        ("authentication", request.authentication_impact),
        ("payments", request.payments_impact),
        ("concurrency", request.concurrency_impact),
        ("architecture", request.architecture_impact),
    ):
        if presence is ImpactPresence.UNKNOWN:
            reasons.append(f"impact:{label}=unknown_not_absence")
    return risk


def _max_uncertainty(current: TaskUncertainty, floor: TaskUncertainty) -> TaskUncertainty:
    if _UNCERTAINTY_RANK[floor] > _UNCERTAINTY_RANK[current]:
        return floor
    return current


def _apply_uncertainty_floors(
    request: TaskAssessmentRequest,
    reasons: list[str],
) -> TaskUncertainty:
    """Raise ``baseline_uncertainty`` to the acceptance-clarity floor; never lower it."""
    uncertainty = request.baseline_uncertainty
    reasons.append(f"baseline_uncertainty={uncertainty.value}")
    if request.acceptance_clarity is AcceptanceClarity.CLEAR:
        floor = TaskUncertainty.LOW
        reasons.append("floor:clear_acceptance→uncertainty>=low")
    elif request.acceptance_clarity is AcceptanceClarity.INCOMPLETE:
        floor = TaskUncertainty.MEDIUM
        reasons.append("floor:incomplete_acceptance→uncertainty>=medium")
    else:
        floor = TaskUncertainty.HIGH
        reasons.append("floor:major_ambiguity→uncertainty>=high")
    raised = _max_uncertainty(uncertainty, floor)
    if raised is not uncertainty:
        reasons.append(f"uncertainty_raised_to={raised.value}")
    else:
        reasons.append("uncertainty_floor_already_met")
    return raised


def _derive_autonomy(
    complexity: TaskComplexity,
    risk: TaskRisk,
    reasons: list[str],
) -> AutonomyRequired:
    if complexity is TaskComplexity.ARCHITECTURAL or risk in {TaskRisk.HIGH, TaskRisk.CRITICAL}:
        reasons.append("autonomy=high_from_complexity_or_risk")
        return AutonomyRequired.HIGH
    if complexity is TaskComplexity.CROSS_MODULE or risk is TaskRisk.MEDIUM or complexity is TaskComplexity.LOCAL:
        reasons.append("autonomy=medium_from_complexity_or_risk")
        return AutonomyRequired.MEDIUM
    reasons.append("autonomy=low_mechanical_low_risk")
    return AutonomyRequired.LOW


def _add_requirement(
    sink: dict[str, CapabilityRequirement],
    reasons: list[str],
    *,
    capability_id: str,
    axes: frozenset[CapabilityAxis],
    reason: str,
) -> None:
    if capability_id in sink:
        return
    sink[capability_id] = CapabilityRequirement(
        capability_id=CapabilityId(capability_id),
        mandatory_axes=axes,
    )
    reasons.append(reason)


def _derive_capability_requirements(
    request: TaskAssessmentRequest,
    reasons: list[str],
) -> dict[str, CapabilityRequirement]:
    """Map declared signals to existing CapabilityId vocabulary only."""
    requirements: dict[str, CapabilityRequirement] = {}
    _add_requirement(
        requirements,
        reasons,
        capability_id=_FILESYSTEM_READ,
        axes=_BASE_AXES,
        reason="require:filesystem.read(supported,available)",
    )
    _add_requirement(
        requirements,
        reasons,
        capability_id=_FILESYSTEM_WRITE,
        axes=_BASE_AXES,
        reason="require:filesystem.write(supported,available)",
    )
    if request.requires_work_item_read:
        _add_requirement(
            requirements,
            reasons,
            capability_id=_WORK_ITEM_READ,
            axes=_BASE_AXES,
            reason="require:work_item.notion.read(supported,available)",
        )
    if request.requires_work_item_write:
        _add_requirement(
            requirements,
            reasons,
            capability_id=_WORK_ITEM_WRITE,
            axes=_AUTHORIZED_AXES,
            reason="require:work_item.notion.write(supported,available,authorized)",
        )
    return requirements


def _derive_required_context(
    request: TaskAssessmentRequest,
    reasons: list[str],
) -> tuple[str, ...]:
    """Collect context keys; integrations are mentioned, not proven available."""
    context: list[str] = []
    impact_contexts = (
        (request.schema_impact, "schema_or_migration"),
        (request.security_impact, "security_sensitive"),
        (request.authentication_impact, "authentication_sensitive"),
        (request.payments_impact, "payments_sensitive"),
        (request.concurrency_impact, "concurrency_sensitive"),
    )
    for presence, key in impact_contexts:
        if presence is ImpactPresence.YES:
            context.append(key)
            reasons.append(f"context:{key}")
    if request.architecture_impact is ImpactPresence.YES or request.dependency_inversion_or_public_api:
        context.append("architecture_or_public_contract")
        reasons.append("context:architecture_or_public_contract")
    if request.frontend_backend_boundary is ImpactPresence.YES:
        context.append("frontend_backend_boundary")
        reasons.append("context:frontend_backend_boundary")
    elif request.frontend_backend_boundary is ImpactPresence.UNKNOWN:
        reasons.append("frontend_backend_boundary=unknown_not_required")
    for name in sorted(request.external_integrations):
        key = f"external_integration:{name}"
        context.append(key)
        reasons.append(f"context:{key}_mentioned_not_proven_available")
        reasons.append(
            f"gap:no_capability_id_for_integration:{name}_without_inventory_mapping",
        )
    return tuple(dict.fromkeys(context))


def _derive_requirements(
    request: TaskAssessmentRequest,
    reasons: list[str],
) -> tuple[tuple[CapabilityRequirement, ...], tuple[str, ...]]:
    requirements = _derive_capability_requirements(request, reasons)
    context = _derive_required_context(request, reasons)
    ordered = tuple(requirements[key] for key in sorted(requirements))
    return ordered, context
