"""Flight Data Recorder — outcome measurement and discrepancy detection.

After a human makes a decision, the world moves on. This module records
what actually happened (the outcome), compares it to what the agent
predicted, and identifies discrepancies. It does NOT change any agent
behavior — it only observes.

Key types:
    ``DecisionOutcomeLog`` — what happened after the human decided.
    ``DecisionDiscrepancy`` — where prediction diverged from reality.

The ``calculate_prediction_error`` and ``derive_decision_discrepancy``
functions are pure: they take inputs and return measurements.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from lltpm.ontology.core import SourceRef
from lltpm.structural.feedback import HumanDecisionRecord


class DecisionOutcomeType(str, Enum):
    RISK_MATERIALIZED = "risk_materialized"
    RISK_RESOLVED = "risk_resolved"
    RISK_UNCHANGED = "risk_unchanged"
    BETTER_THAN_PREDICTED = "better_than_predicted"
    WORSE_THAN_PREDICTED = "worse_than_predicted"
    OUTCOME_UNKNOWN = "outcome_unknown"


class DiscrepancyType(str, Enum):
    OVERESTIMATED_RISK = "overestimated_risk"
    UNDERESTIMATED_RISK = "underestimated_risk"
    WRONG_ROOT_CAUSE = "wrong_root_cause"
    CORRECT_PREDICTION = "correct_prediction"
    INSUFFICIENT_DATA = "insufficient_data"


class DecisionOutcomeLog(BaseModel):
    """What actually happened after a human decision.

    Links back to the ``HumanDecisionRecord`` and records the observed
    structural risk after the decision played out. The ``actual_risk_change``
    is the measured delta — positive means risk went down (improvement),
    negative means risk went up (deterioration).
    """
    id: str = ""
    decision_record_id: str = ""
    program_id: str = ""
    outcome_type: DecisionOutcomeType = DecisionOutcomeType.OUTCOME_UNKNOWN
    actual_risk_change: float = 0.0
    observed_at: datetime = Field(default_factory=datetime.utcnow)
    observation_window_days: int = 7
    structural_metric_snapshot: dict = Field(default_factory=dict)
    notes: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)


class DecisionDiscrepancy(BaseModel):
    """Where the agent's prediction diverged from the observed outcome.

    Pure observation — the agent may use this to generate a calibration
    hypothesis, but the discrepancy itself changes nothing.
    """
    id: str = ""
    decision_record_id: str = ""
    outcome_log_id: str = ""
    discrepancy_type: DiscrepancyType = DiscrepancyType.INSUFFICIENT_DATA
    predicted_risk_reduction: float = 0.0
    actual_risk_change: float = 0.0
    prediction_error: float = 0.0
    severity: str = ""
    explanation: str = ""
    detected_at: datetime = Field(default_factory=datetime.utcnow)


def calculate_prediction_error(
    predicted_risk_reduction: float,
    actual_risk_change: float,
) -> float:
    """Absolute prediction error.

    Both values use the convention: positive = risk decreased (good),
    negative = risk increased (bad). The error is the absolute difference.
    """
    return abs(predicted_risk_reduction - actual_risk_change)


def record_decision_outcome(
    decision_record: HumanDecisionRecord,
    actual_risk_change: float,
    *,
    observation_window_days: int = 7,
    structural_metric_snapshot: dict | None = None,
    notes: str = "",
) -> DecisionOutcomeLog:
    """Create a DecisionOutcomeLog from observed results."""
    predicted = decision_record.agent_predicted_risk_reduction

    if actual_risk_change < -0.1:
        outcome_type = DecisionOutcomeType.RISK_MATERIALIZED
    elif actual_risk_change > 0.1:
        outcome_type = DecisionOutcomeType.RISK_RESOLVED
    else:
        outcome_type = DecisionOutcomeType.RISK_UNCHANGED

    if predicted > 0 and outcome_type not in (
        DecisionOutcomeType.RISK_MATERIALIZED,
    ):
        if actual_risk_change > predicted * 1.2:
            outcome_type = DecisionOutcomeType.BETTER_THAN_PREDICTED
        elif actual_risk_change < predicted * 0.5:
            outcome_type = DecisionOutcomeType.WORSE_THAN_PREDICTED

    return DecisionOutcomeLog(
        id=f"dol-{decision_record.id}-{int(datetime.utcnow().timestamp())}",
        decision_record_id=decision_record.id,
        program_id=decision_record.program_id,
        outcome_type=outcome_type,
        actual_risk_change=actual_risk_change,
        observation_window_days=observation_window_days,
        structural_metric_snapshot=structural_metric_snapshot or {},
        notes=notes,
    )


def derive_decision_discrepancy(
    decision_record: HumanDecisionRecord,
    outcome_log: DecisionOutcomeLog,
) -> DecisionDiscrepancy:
    """Compare the agent's prediction with the observed outcome.

    Classifies the discrepancy: overestimated risk (false alarm),
    underestimated risk (missed danger), wrong root cause, correct,
    or insufficient data.
    """
    predicted = decision_record.agent_predicted_risk_reduction
    actual = outcome_log.actual_risk_change
    error = calculate_prediction_error(predicted, actual)

    if error < 0.1:
        disc_type = DiscrepancyType.CORRECT_PREDICTION
        explanation = (
            f"Prediction closely matched outcome "
            f"(predicted={predicted:.2f}, actual={actual:.2f})."
        )
    elif predicted > actual + 0.15:
        disc_type = DiscrepancyType.OVERESTIMATED_RISK
        explanation = (
            f"Agent predicted risk reduction of {predicted:.2f} but "
            f"actual change was {actual:.2f} — overestimated the benefit."
        )
    elif actual > predicted + 0.15:
        disc_type = DiscrepancyType.UNDERESTIMATED_RISK
        explanation = (
            f"Agent predicted risk reduction of {predicted:.2f} but "
            f"actual change was {actual:.2f} — underestimated the benefit."
        )
    else:
        disc_type = DiscrepancyType.CORRECT_PREDICTION
        explanation = (
            f"Prediction was within tolerance "
            f"(predicted={predicted:.2f}, actual={actual:.2f})."
        )

    return DecisionDiscrepancy(
        id=f"disc-{outcome_log.id}",
        decision_record_id=decision_record.id,
        outcome_log_id=outcome_log.id,
        discrepancy_type=disc_type,
        predicted_risk_reduction=predicted,
        actual_risk_change=actual,
        prediction_error=error,
        severity=decision_record.agent_severity,
        explanation=explanation,
    )
