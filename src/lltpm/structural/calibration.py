"""Flight Data Recorder — calibration hypothesis generation.

Reads discrepancies and decision records and produces *hypotheses* about
how the agent's thresholds or weights might be wrong. Crucially, every
hypothesis defaults to ``status=HYPOTHESIS`` — it never auto-applies.

A human must read the hypothesis, agree, and manually change the
threshold in code. The recorder may suggest "betweenness threshold may
be too low — 60% of bottleneck signals were overrides." It may not
write ``_classify(value, new_threshold, ...)`` into metrics.py.

``summarize_feedback_kpis`` provides aggregate statistics over a
collection of decision records — acceptance rate, override rate,
average prediction error, etc. These are the feedback KPIs the agent
reports on but does not react to autonomously.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from lltpm.structural.feedback import HumanDecisionRecord, HumanDecisionType
from lltpm.structural.outcomes import (
    DecisionDiscrepancy,
    DecisionOutcomeLog,
    DiscrepancyType,
)


class CalibrationHypothesisType(str, Enum):
    THRESHOLD_TOO_SENSITIVE = "threshold_too_sensitive"
    THRESHOLD_TOO_LENIENT = "threshold_too_lenient"
    WRONG_METRIC_WEIGHT = "wrong_metric_weight"
    MISSING_CONTEXT_SIGNAL = "missing_context_signal"
    FALSE_POSITIVE_PATTERN = "false_positive_pattern"
    FALSE_NEGATIVE_PATTERN = "false_negative_pattern"
    # Structural / inverse-solver hypotheses. Still observational —
    # these describe what the inverse solver thinks the model was missing.
    ADD_MISSING_EDGE_TYPE = "add_missing_edge_type"
    ADD_CONSTRAINT_TYPE = "add_constraint_type"
    ADJUST_INTERVENTION_COST = "adjust_intervention_cost"
    INCREASE_METRIC_WEIGHT = "increase_metric_weight"
    DECREASE_METRIC_WEIGHT = "decrease_metric_weight"
    NO_CHANGE = "no_change"


class CalibrationHypothesisStatus(str, Enum):
    HYPOTHESIS = "hypothesis"
    UNDER_REVIEW = "under_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    APPLIED_MANUALLY = "applied_manually"


class CalibrationHypothesis(BaseModel):
    """A suggestion about how the agent's model might be miscalibrated.

    Observational only. ``status`` defaults to ``HYPOTHESIS`` and must be
    manually progressed. The agent may generate these; it may not apply them.
    """
    id: str = ""
    hypothesis_type: CalibrationHypothesisType = CalibrationHypothesisType.THRESHOLD_TOO_SENSITIVE
    status: CalibrationHypothesisStatus = CalibrationHypothesisStatus.HYPOTHESIS
    signal_type_affected: str = ""
    metric_name_affected: str = ""
    current_threshold: float | None = None
    suggested_threshold: float | None = None
    # Set when this hypothesis originates from an inverse-solver TopologicalMismatch.
    source_mismatch_id: str = ""
    supporting_decision_record_ids: list[str] = Field(default_factory=list)
    supporting_discrepancy_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    evidence_count: int = 0
    confidence: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


def generate_calibration_hypothesis(
    discrepancies: list[DecisionDiscrepancy],
    decision_records: list[HumanDecisionRecord],
    signal_type: str = "",
    metric_name: str = "",
) -> CalibrationHypothesis | None:
    """Generate a calibration hypothesis from a batch of discrepancies.

    Looks for patterns: if most discrepancies are overestimates (false
    alarms), suggests the threshold is too sensitive. If most are
    underestimates, suggests it's too lenient. Returns None if there
    isn't enough data to form a hypothesis.
    """
    if len(discrepancies) < 3:
        return None

    overestimates = sum(
        1 for d in discrepancies
        if d.discrepancy_type == DiscrepancyType.OVERESTIMATED_RISK
    )
    underestimates = sum(
        1 for d in discrepancies
        if d.discrepancy_type == DiscrepancyType.UNDERESTIMATED_RISK
    )
    total = len(discrepancies)

    override_records = [
        r for r in decision_records
        if r.decision_type in (HumanDecisionType.OVERRIDDEN, HumanDecisionType.REJECTED)
    ]

    if overestimates / total >= 0.6:
        return CalibrationHypothesis(
            id=f"cal-{signal_type or 'general'}-{int(datetime.utcnow().timestamp())}",
            hypothesis_type=CalibrationHypothesisType.THRESHOLD_TOO_SENSITIVE,
            status=CalibrationHypothesisStatus.HYPOTHESIS,
            signal_type_affected=signal_type,
            metric_name_affected=metric_name,
            supporting_decision_record_ids=[r.id for r in override_records],
            supporting_discrepancy_ids=[d.id for d in discrepancies],
            summary=(
                f"{overestimates}/{total} discrepancies were overestimates — "
                f"the {metric_name or 'metric'} threshold may be too sensitive."
            ),
            evidence_count=total,
            confidence=min(0.9, overestimates / total),
        )

    if underestimates / total >= 0.6:
        return CalibrationHypothesis(
            id=f"cal-{signal_type or 'general'}-{int(datetime.utcnow().timestamp())}",
            hypothesis_type=CalibrationHypothesisType.THRESHOLD_TOO_LENIENT,
            status=CalibrationHypothesisStatus.HYPOTHESIS,
            signal_type_affected=signal_type,
            metric_name_affected=metric_name,
            supporting_decision_record_ids=[r.id for r in override_records],
            supporting_discrepancy_ids=[d.id for d in discrepancies],
            summary=(
                f"{underestimates}/{total} discrepancies were underestimates — "
                f"the {metric_name or 'metric'} threshold may be too lenient."
            ),
            evidence_count=total,
            confidence=min(0.9, underestimates / total),
        )

    if len(override_records) / max(len(decision_records), 1) >= 0.5:
        return CalibrationHypothesis(
            id=f"cal-{signal_type or 'general'}-{int(datetime.utcnow().timestamp())}",
            hypothesis_type=CalibrationHypothesisType.FALSE_POSITIVE_PATTERN,
            status=CalibrationHypothesisStatus.HYPOTHESIS,
            signal_type_affected=signal_type,
            metric_name_affected=metric_name,
            supporting_decision_record_ids=[r.id for r in override_records],
            supporting_discrepancy_ids=[d.id for d in discrepancies],
            summary=(
                f"{len(override_records)}/{len(decision_records)} decisions were "
                f"overrides/rejections — possible false-positive pattern."
            ),
            evidence_count=len(decision_records),
            confidence=0.6,
        )

    return None


class FeedbackKPISummary(BaseModel):
    """Aggregate feedback statistics over a set of decision records."""
    total_decisions: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    overridden_count: int = 0
    deferred_count: int = 0
    escalated_count: int = 0
    acceptance_rate: float = 0.0
    override_rate: float = 0.0
    average_prediction_error: float = 0.0
    average_agent_confidence: float = 0.0
    computed_at: datetime = Field(default_factory=datetime.utcnow)


def summarize_feedback_kpis(
    decision_records: list[HumanDecisionRecord],
    outcome_logs: list[DecisionOutcomeLog] | None = None,
) -> FeedbackKPISummary:
    """Compute aggregate feedback KPIs over a set of decision records.

    Pure observation — these numbers inform humans about agent performance
    but do not trigger any agent self-modification.
    """
    total = len(decision_records)
    if total == 0:
        return FeedbackKPISummary()

    accepted = sum(1 for r in decision_records if r.decision_type == HumanDecisionType.ACCEPTED)
    rejected = sum(1 for r in decision_records if r.decision_type == HumanDecisionType.REJECTED)
    overridden = sum(1 for r in decision_records if r.decision_type == HumanDecisionType.OVERRIDDEN)
    deferred = sum(1 for r in decision_records if r.decision_type == HumanDecisionType.DEFERRED)
    escalated = sum(1 for r in decision_records if r.decision_type == HumanDecisionType.ESCALATED)

    avg_confidence = sum(r.agent_confidence for r in decision_records) / total

    avg_error = 0.0
    if outcome_logs:
        record_map = {r.id: r for r in decision_records}
        errors = []
        for log in outcome_logs:
            rec = record_map.get(log.decision_record_id)
            if rec:
                errors.append(abs(rec.agent_predicted_risk_reduction - log.actual_risk_change))
        if errors:
            avg_error = sum(errors) / len(errors)

    return FeedbackKPISummary(
        total_decisions=total,
        accepted_count=accepted,
        rejected_count=rejected,
        overridden_count=overridden,
        deferred_count=deferred,
        escalated_count=escalated,
        acceptance_rate=accepted / total,
        override_rate=overridden / total,
        average_prediction_error=avg_error,
        average_agent_confidence=avg_confidence,
    )
