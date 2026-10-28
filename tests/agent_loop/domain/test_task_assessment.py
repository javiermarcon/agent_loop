"""Domain tests for deterministic TaskAssessment and requirement derivation."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_loop.domain.capabilities import CapabilityAxis
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.task_assessment import AcceptanceClarity
from agent_loop.domain.task_assessment import AutonomyRequired
from agent_loop.domain.task_assessment import ImpactPresence
from agent_loop.domain.task_assessment import TaskAssessment
from agent_loop.domain.task_assessment import TaskAssessmentRequest
from agent_loop.domain.task_assessment import TaskComplexity
from agent_loop.domain.task_assessment import TaskRisk
from agent_loop.domain.task_assessment import TaskUncertainty
from agent_loop.domain.task_assessment import assess_task

_BASE_AXES = frozenset({CapabilityAxis.SUPPORTED, CapabilityAxis.AVAILABLE})
_KNOWN_FILE_COUNT = 3
_FS_READ_ID = CapabilityId("filesystem.read")
_FS_WRITE_ID = CapabilityId("filesystem.write")
_BASE_REQUEST = TaskAssessmentRequest(
    baseline_complexity=TaskComplexity.MECHANICAL,
    baseline_risk=TaskRisk.LOW,
    baseline_uncertainty=TaskUncertainty.LOW,
    acceptance_clarity=AcceptanceClarity.CLEAR,
)
_UNCERTAINTY_ORDER = {
    TaskUncertainty.LOW: 0,
    TaskUncertainty.MEDIUM: 1,
    TaskUncertainty.HIGH: 2,
}


def _capability_ids(assessment: TaskAssessment) -> set[str]:
    return {item.capability_id.value for item in assessment.required_capabilities}


def test_mechanical_clear_task_stays_low() -> None:
    """Mechanical, clear, low-risk task keeps floors at the baseline."""
    assessment = assess_task(_BASE_REQUEST)
    assert assessment.complexity is TaskComplexity.MECHANICAL
    assert assessment.risk is TaskRisk.LOW
    assert assessment.uncertainty is TaskUncertainty.LOW
    assert assessment.autonomy_required is AutonomyRequired.LOW
    assert assessment.estimated_files is None
    assert _capability_ids(assessment) == {"filesystem.read", "filesystem.write"}
    assert assessment.required_context == ()
    assert any(reason.startswith("baseline_complexity=") for reason in assessment.reasons)


def test_local_baseline_preserved_without_floors() -> None:
    """Local baseline without elevating signals remains local/medium autonomy."""
    assessment = assess_task(
        replace(_BASE_REQUEST, baseline_complexity=TaskComplexity.LOCAL, baseline_risk=TaskRisk.MEDIUM),
    )
    assert assessment.complexity is TaskComplexity.LOCAL
    assert assessment.risk is TaskRisk.MEDIUM
    assert assessment.autonomy_required is AutonomyRequired.MEDIUM


def test_cross_module_baseline() -> None:
    """Explicit cross-module baseline is preserved without architecture floors."""
    assessment = assess_task(replace(_BASE_REQUEST, baseline_complexity=TaskComplexity.CROSS_MODULE))
    assert assessment.complexity is TaskComplexity.CROSS_MODULE
    assert assessment.autonomy_required is AutonomyRequired.MEDIUM


def test_architectural_baseline() -> None:
    """Architectural baseline remains architectural with high autonomy."""
    assessment = assess_task(replace(_BASE_REQUEST, baseline_complexity=TaskComplexity.ARCHITECTURAL))
    assert assessment.complexity is TaskComplexity.ARCHITECTURAL
    assert assessment.autonomy_required is AutonomyRequired.HIGH


def test_concurrency_raises_complexity_and_risk_floors() -> None:
    """Concurrency YES raises complexity to CROSS_MODULE and risk to HIGH."""
    assessment = assess_task(
        replace(
            _BASE_REQUEST,
            baseline_complexity=TaskComplexity.MECHANICAL,
            baseline_risk=TaskRisk.LOW,
            concurrency_impact=ImpactPresence.YES,
        ),
    )
    assert assessment.complexity is TaskComplexity.CROSS_MODULE
    assert assessment.risk is TaskRisk.HIGH
    assert "floor:concurrency→complexity>=cross_module" in assessment.reasons
    assert "floor:concurrency→risk>=high" in assessment.reasons
    assert "concurrency_sensitive" in assessment.required_context


def test_dependency_inversion_forces_architectural() -> None:
    """Dependency inversion / public API change forces ARCHITECTURAL."""
    assessment = assess_task(
        replace(
            _BASE_REQUEST,
            baseline_complexity=TaskComplexity.LOCAL,
            dependency_inversion_or_public_api=True,
        ),
    )
    assert assessment.complexity is TaskComplexity.ARCHITECTURAL
    assert "floor:architecture_or_dependency_inversion→architectural" in assessment.reasons
    assert "architecture_or_public_contract" in assessment.required_context


def test_architecture_impact_yes_forces_architectural() -> None:
    """Declared architecture impact YES also forces ARCHITECTURAL."""
    assessment = assess_task(replace(_BASE_REQUEST, architecture_impact=ImpactPresence.YES))
    assert assessment.complexity is TaskComplexity.ARCHITECTURAL


def test_schema_migration_forces_high_risk() -> None:
    """Schema/migration impact YES floors risk at HIGH even from LOW baseline."""
    assessment = assess_task(
        replace(_BASE_REQUEST, baseline_risk=TaskRisk.LOW, schema_impact=ImpactPresence.YES),
    )
    assert assessment.risk is TaskRisk.HIGH
    assert assessment.risk is not TaskRisk.CRITICAL
    assert "floor:schema_or_migration→risk>=high" in assessment.reasons
    assert "schema_or_migration" in assessment.required_context


def test_security_and_authentication_force_high_risk() -> None:
    """Security and authentication impacts each floor risk to HIGH."""
    security = assess_task(replace(_BASE_REQUEST, security_impact=ImpactPresence.YES))
    auth = assess_task(replace(_BASE_REQUEST, authentication_impact=ImpactPresence.YES))
    assert security.risk is TaskRisk.HIGH
    assert auth.risk is TaskRisk.HIGH
    assert "security_sensitive" in security.required_context
    assert "authentication_sensitive" in auth.required_context


def test_payments_force_high_risk() -> None:
    """Payments impact YES floors risk to HIGH."""
    assessment = assess_task(replace(_BASE_REQUEST, payments_impact=ImpactPresence.YES))
    assert assessment.risk is TaskRisk.HIGH
    assert "payments_sensitive" in assessment.required_context


def test_heuristic_low_cannot_undercut_deterministic_high_floor() -> None:
    """Baseline LOW risk cannot remain LOW when a HIGH floor signal is present."""
    assessment = assess_task(
        replace(
            _BASE_REQUEST,
            baseline_complexity=TaskComplexity.MECHANICAL,
            baseline_risk=TaskRisk.LOW,
            payments_impact=ImpactPresence.YES,
            schema_impact=ImpactPresence.YES,
        ),
    )
    assert assessment.risk is TaskRisk.HIGH
    assert assessment.complexity is TaskComplexity.MECHANICAL


def test_critical_exists_but_floors_do_not_invent_it() -> None:
    """CRITICAL is part of the vocabulary but S11 floors never assign it."""
    assert TaskRisk.CRITICAL.value == "critical"
    assessment = assess_task(
        replace(
            _BASE_REQUEST,
            schema_impact=ImpactPresence.YES,
            security_impact=ImpactPresence.YES,
            payments_impact=ImpactPresence.YES,
            concurrency_impact=ImpactPresence.YES,
        ),
    )
    assert assessment.risk is TaskRisk.HIGH
    assert assessment.risk is not TaskRisk.CRITICAL


@pytest.mark.parametrize(
    ("baseline", "clarity", "expected"),
    [
        (TaskUncertainty.LOW, AcceptanceClarity.CLEAR, TaskUncertainty.LOW),
        (TaskUncertainty.LOW, AcceptanceClarity.INCOMPLETE, TaskUncertainty.MEDIUM),
        (TaskUncertainty.LOW, AcceptanceClarity.MAJOR_AMBIGUITY, TaskUncertainty.HIGH),
        (TaskUncertainty.MEDIUM, AcceptanceClarity.CLEAR, TaskUncertainty.MEDIUM),
        (TaskUncertainty.MEDIUM, AcceptanceClarity.INCOMPLETE, TaskUncertainty.MEDIUM),
        (TaskUncertainty.MEDIUM, AcceptanceClarity.MAJOR_AMBIGUITY, TaskUncertainty.HIGH),
        (TaskUncertainty.HIGH, AcceptanceClarity.CLEAR, TaskUncertainty.HIGH),
        (TaskUncertainty.HIGH, AcceptanceClarity.INCOMPLETE, TaskUncertainty.HIGH),
        (TaskUncertainty.HIGH, AcceptanceClarity.MAJOR_AMBIGUITY, TaskUncertainty.HIGH),
    ],
)
def test_uncertainty_floor_matrix(
    baseline: TaskUncertainty,
    clarity: AcceptanceClarity,
    expected: TaskUncertainty,
) -> None:
    """Acceptance clarity floors never reduce baseline_uncertainty."""
    assessment = assess_task(
        replace(
            _BASE_REQUEST,
            baseline_uncertainty=baseline,
            acceptance_clarity=clarity,
        ),
    )
    assert assessment.uncertainty is expected
    assert _UNCERTAINTY_ORDER[assessment.uncertainty] >= _UNCERTAINTY_ORDER[baseline]


def test_clear_does_not_reduce_high_baseline_uncertainty() -> None:
    """CLEAR must not lower a HIGH baseline to LOW."""
    assessment = assess_task(
        replace(
            _BASE_REQUEST,
            baseline_uncertainty=TaskUncertainty.HIGH,
            acceptance_clarity=AcceptanceClarity.CLEAR,
        ),
    )
    assert assessment.uncertainty is TaskUncertainty.HIGH
    assert assessment.uncertainty is not TaskUncertainty.LOW


def test_unknown_impact_is_not_treated_as_absence() -> None:
    """UNKNOWN security impact does not floor risk and is recorded as unknown."""
    assessment = assess_task(
        replace(
            _BASE_REQUEST,
            baseline_risk=TaskRisk.LOW,
            security_impact=ImpactPresence.UNKNOWN,
        ),
    )
    assert assessment.risk is TaskRisk.LOW
    assert assessment.security_impact is ImpactPresence.UNKNOWN
    assert "impact:security=unknown_not_absence" in assessment.reasons
    assert "security_sensitive" not in assessment.required_context


def test_estimated_files_unknown_and_non_negative() -> None:
    """None estimated_files means unknown; negatives are rejected."""
    unknown = assess_task(replace(_BASE_REQUEST, estimated_files=None))
    known = assess_task(replace(_BASE_REQUEST, estimated_files=_KNOWN_FILE_COUNT))
    assert unknown.estimated_files is None
    assert known.estimated_files == _KNOWN_FILE_COUNT
    with pytest.raises(ValueError, match="estimated_files"):
        replace(_BASE_REQUEST, estimated_files=-1)


def test_mechanical_requirements_are_minimal_filesystem_only() -> None:
    """Mechanical tasks require filesystem axes only, not work-item or MCP ids."""
    assessment = assess_task(_BASE_REQUEST)
    assert _capability_ids(assessment) == {"filesystem.read", "filesystem.write"}
    for requirement in assessment.required_capabilities:
        assert requirement.mandatory_axes == _BASE_AXES
        assert requirement.capability_id in {_FS_READ_ID, _FS_WRITE_ID}


def test_cross_module_and_architectural_keep_filesystem_requirements() -> None:
    """Higher complexity still derives filesystem requirements without inventing MCP."""
    cross = assess_task(replace(_BASE_REQUEST, baseline_complexity=TaskComplexity.CROSS_MODULE))
    architectural = assess_task(
        replace(_BASE_REQUEST, baseline_complexity=TaskComplexity.ARCHITECTURAL),
    )
    assert "filesystem.read" in _capability_ids(cross)
    assert "filesystem.write" in _capability_ids(architectural)
    assert all(not item.startswith("mcp.") for item in _capability_ids(architectural))


def test_external_integration_is_context_not_availability() -> None:
    """Named integrations become required_context, not proven-available capabilities."""
    assessment = assess_task(
        replace(_BASE_REQUEST, external_integrations=("stripe", "notion-mcp")),
    )
    assert assessment.external_integrations == ("notion-mcp", "stripe")
    assert "external_integration:stripe" in assessment.required_context
    assert "external_integration:notion-mcp" in assessment.required_context
    assert "mcp.stripe" not in _capability_ids(assessment)
    assert "mcp.notion-mcp" not in _capability_ids(assessment)
    assert any("mentioned_not_proven_available" in reason for reason in assessment.reasons)
    assert any("gap:no_capability_id_for_integration:stripe" in reason for reason in assessment.reasons)


def test_frontend_backend_boundary_context() -> None:
    """Frontend/backend boundary YES adds context; UNKNOWN does not invent a requirement."""
    yes = assess_task(replace(_BASE_REQUEST, frontend_backend_boundary=ImpactPresence.YES))
    unknown = assess_task(replace(_BASE_REQUEST, frontend_backend_boundary=ImpactPresence.UNKNOWN))
    assert "frontend_backend_boundary" in yes.required_context
    assert "frontend_backend_boundary" not in unknown.required_context
    assert "frontend_backend_boundary=unknown_not_required" in unknown.reasons


def test_work_item_capabilities_when_declared() -> None:
    """Explicit work-item signals derive existing notion capability ids."""
    assessment = assess_task(
        replace(_BASE_REQUEST, requires_work_item_read=True, requires_work_item_write=True),
    )
    ids = _capability_ids(assessment)
    assert "work_item.notion.read" in ids
    assert "work_item.notion.write" in ids
    write = next(
        item for item in assessment.required_capabilities if item.capability_id.value == "work_item.notion.write"
    )
    assert CapabilityAxis.AUTHORIZED in write.mandatory_axes


def test_same_input_is_deterministic() -> None:
    """Identical requests produce equal assessments including reason order."""
    request = replace(
        _BASE_REQUEST,
        baseline_complexity=TaskComplexity.LOCAL,
        baseline_risk=TaskRisk.MEDIUM,
        impacted_domains=("orders", "payments"),
        external_integrations=("stripe",),
        schema_impact=ImpactPresence.YES,
        acceptance_clarity=AcceptanceClarity.INCOMPLETE,
    )
    first = assess_task(request)
    second = assess_task(request)
    assert first == second
    assert first.reasons == second.reasons
    assert first.required_capabilities == second.required_capabilities


def test_impacted_domains_are_sorted_copies() -> None:
    """Impacted domains are normalized to a stable sorted tuple."""
    assessment = assess_task(replace(_BASE_REQUEST, impacted_domains=("payments", "auth", "orders")))
    assert assessment.impacted_domains == ("auth", "orders", "payments")
