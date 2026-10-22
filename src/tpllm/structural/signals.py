"""Structural signals → the existing validation gate.

A ``StructuralSignal`` is the legible product of the metric engine: not a raw
number, but an explanation — "the Payments API contract is the bottleneck for
4 workstreams." The key architectural move here is reuse: ``signal_to_insight``
adapts a structural signal into a ``KPIInsight`` so it flows through the SAME
``validate_insight()`` gate as every other insight. Structural alarms get
evidence-checked, confidence-checked, staleness-checked, and contradiction-
checked — there is no parallel guardrail system.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from tpllm.ontology.interfaces import EvidenceBacked
from tpllm.ontology.measurement import (
    InsightSeverity,
    InsightType,
    KPIInsight,
)


class StructuralSignalType(str, Enum):
    SCOPE_DRIFT = "scope_drift"
    CONTEXT_ENTANGLEMENT = "context_entanglement"
    BOTTLENECK_NODE = "bottleneck_node"
    DEPENDENCY_CONGESTION = "dependency_congestion"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    REVIEWER_CONCENTRATION = "reviewer_concentration"


# Map each structural signal type onto the closest existing InsightType so the
# measurement layer can reason about it uniformly.
_SIGNAL_TO_INSIGHT_TYPE: dict[StructuralSignalType, InsightType] = {
    StructuralSignalType.SCOPE_DRIFT: InsightType.SCOPE_CHURN,
    StructuralSignalType.CONTEXT_ENTANGLEMENT: InsightType.EXECUTION_SLOWDOWN,
    StructuralSignalType.BOTTLENECK_NODE: InsightType.DEPENDENCY_DRAG,
    StructuralSignalType.DEPENDENCY_CONGESTION: InsightType.DEPENDENCY_DRAG,
    StructuralSignalType.OWNERSHIP_MISMATCH: InsightType.OWNERSHIP_GAP,
    StructuralSignalType.REVIEWER_CONCENTRATION: InsightType.COMMUNICATION_GAP,
}


class StructuralSignal(EvidenceBacked):
    """An explainable structural finding, backed by graph metric observations.

    Composes EvidenceBacked, so it carries ``evidence_claim_ids``,
    ``confidence``, and ``source_refs`` — the provenance that lets it survive
    the validation gate.
    """
    id: str = ""
    signal_type: StructuralSignalType = StructuralSignalType.BOTTLENECK_NODE
    affected_object_ids: list[str] = Field(default_factory=list)
    graph_metric_observation_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    severity: InsightSeverity = InsightSeverity.MEDIUM
    detected_at: datetime = Field(default_factory=datetime.utcnow)


def signal_to_insight(signal: StructuralSignal) -> KPIInsight:
    """Adapt a StructuralSignal into a KPIInsight for the validation gate.

    The signal's graph metric observations become the insight's
    ``related_kpi_observation_ids`` and its evidence claims become
    ``supporting_evidence_claim_ids`` — so ``validate_insight()`` can apply
    its evidence/confidence/staleness/contradiction checks unchanged.
    """
    return KPIInsight(
        id=f"ins-{signal.id}" if signal.id else "ins-structural",
        insight_type=_SIGNAL_TO_INSIGHT_TYPE.get(
            signal.signal_type, InsightType.EXECUTION_SLOWDOWN
        ),
        title=signal.signal_type.value.replace("_", " ").title(),
        summary=signal.summary,
        severity=signal.severity,
        related_kpi_observation_ids=list(signal.graph_metric_observation_ids),
        supporting_evidence_claim_ids=list(signal.evidence_claim_ids),
        affected_object_ids=list(signal.affected_object_ids),
        confidence=signal.confidence,
        human_review_required=signal.severity
        in (InsightSeverity.HIGH, InsightSeverity.CRITICAL),
    )
