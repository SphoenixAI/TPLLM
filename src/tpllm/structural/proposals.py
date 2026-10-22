"""Calibration proposal creation — hypotheses become reviewable proposals.

Transforms ``CalibrationHypothesis`` and ``TopologicalMismatch`` records
from the inverse solver into structured ``CalibrationProposal`` objects
that can be regression-tested before human review.

Every proposal defaults to ``PROPOSED`` status. Only the regression
engine may advance it to ``VALIDATED_FOR_REVIEW`` or ``REGRESSION_DETECTED``.
Nothing in this module auto-applies a calibration change.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from tpllm.structural.calibration import CalibrationHypothesis, CalibrationHypothesisType


# ── Enums ──────────────────────────────────────────────────────────────

class ProposalStatus(str, Enum):
    PROPOSED = "proposed"
    VALIDATED_FOR_REVIEW = "validated_for_review"
    REGRESSION_DETECTED = "regression_detected"
    NEEDS_MORE_DATA = "needs_more_data"
    REJECTED = "rejected"
    APPROVED = "approved"
    APPLIED = "applied"


class CalibrationProposalType(str, Enum):
    ADD_MISSING_EDGE_TYPE = "add_missing_edge_type"
    ADD_CONSTRAINT_TYPE = "add_constraint_type"
    ADJUST_INTERVENTION_COST = "adjust_intervention_cost"
    INCREASE_METRIC_WEIGHT = "increase_metric_weight"
    DECREASE_METRIC_WEIGHT = "decrease_metric_weight"
    ADD_FALSE_POSITIVE_SUPPRESSOR = "add_false_positive_suppressor"
    ADD_AUTHORIZED_BRIDGE = "add_authorized_bridge"
    NO_CHANGE = "no_change"


class RegressionCaseClassification(str, Enum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED = "unchanged"
    INCONCLUSIVE = "inconclusive"


class EvidenceBundleRecommendation(str, Enum):
    PROMOTE_FOR_REVIEW = "promote_for_review"
    NEEDS_MORE_DATA = "needs_more_data"
    REJECT_DUE_TO_REGRESSION = "reject_due_to_regression"
    REJECT_NO_IMPROVEMENT = "reject_no_improvement"


class CalibrationTestCaseStatus(str, Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class CalibrationTestCaseType(str, Enum):
    RANK_HUMAN_OPTION_HIGHER = "rank_human_option_higher"
    NO_REGRESSION_ON_ACCEPTED_TOP = "no_regression_on_accepted_top"
    PREDICTION_ERROR_IMPROVES = "prediction_error_improves"
    CONSTRAINT_BLOCKS_BAD_OPTION = "constraint_blocks_bad_option"


# ── Models ─────────────────────────────────────────────────────────────

class CalibrationProposal(BaseModel):
    """A reviewable candidate calibration change.

    Created from a ``CalibrationHypothesis`` or ``TopologicalMismatch``,
    then regression-tested before promotion. Status defaults to ``PROPOSED``
    and is only advanced by the regression engine — never auto-applied.
    """
    id: str = ""
    proposal_type: CalibrationProposalType = CalibrationProposalType.NO_CHANGE

    source_hypothesis_ids: list[str] = Field(default_factory=list)
    source_mismatch_ids: list[str] = Field(default_factory=list)

    target_metric_name: str | None = None
    target_intervention_type: str | None = None
    target_edge_type: str | None = None
    target_constraint_type: str | None = None

    proposed_delta: float | None = None
    proposed_change_summary: str = ""

    status: ProposalStatus = ProposalStatus.PROPOSED

    historical_success_rate: float | None = None
    regression_count: int = 0
    replayed_case_count: int = 0

    evidence_bundle_id: str | None = None
    regression_report_id: str | None = None

    confidence: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RegressionCaseResult(BaseModel):
    """One historical replay result — live vs proposed behavior."""
    id: str = ""
    calibration_proposal_id: str = ""
    human_decision_record_id: str = ""
    decision_outcome_log_id: str | None = None

    live_matched_human_choice: bool = False
    proposal_matched_human_choice: bool = False

    live_prediction_error: float | None = None
    proposal_prediction_error: float | None = None

    live_human_option_rank: int | None = None
    proposal_human_option_rank: int | None = None

    classification: RegressionCaseClassification = RegressionCaseClassification.INCONCLUSIVE

    improved: bool = False
    regressed: bool = False
    inconclusive: bool = False

    summary: str = ""


class RegressionReport(BaseModel):
    """Aggregate result of back-testing a proposal against historical cases."""
    id: str = ""
    calibration_proposal_id: str = ""

    replayed_case_count: int = 0
    improved_count: int = 0
    regression_count: int = 0
    unchanged_count: int = 0
    inconclusive_count: int = 0

    historical_success_rate: float = 0.0
    average_error_delta: float | None = None

    case_result_ids: list[str] = Field(default_factory=list)

    status: ProposalStatus = ProposalStatus.PROPOSED
    summary: str = ""


class HistoricalValidationImpact(BaseModel):
    """Human-readable performance impact of a calibration proposal."""
    id: str = ""
    calibration_proposal_id: str = ""

    matched_human_override_count: int = 0
    total_replayable_overrides: int = 0

    match_rate: float = 0.0
    regression_count: int = 0

    average_prediction_error_before: float | None = None
    average_prediction_error_after: float | None = None
    average_prediction_error_delta: float | None = None

    confidence: float = 0.0
    summary: str = ""


class CalibrationEvidenceBundle(BaseModel):
    """The final artifact for human review — proposal + regression results."""
    id: str = ""
    calibration_proposal_id: str = ""

    proposal_summary: str = ""

    source_hypothesis_ids: list[str] = Field(default_factory=list)
    source_mismatch_ids: list[str] = Field(default_factory=list)

    regression_report_id: str | None = None
    historical_validation_impact_id: str | None = None

    recommendation: EvidenceBundleRecommendation = EvidenceBundleRecommendation.NEEDS_MORE_DATA

    explanation: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CalibrationTestCase(BaseModel):
    """A data-driven regression test record — structured, not generated Python."""
    id: str = ""
    calibration_proposal_id: str = ""

    source_human_decision_record_id: str = ""
    source_outcome_log_id: str | None = None
    source_mismatch_id: str | None = None

    test_type: CalibrationTestCaseType = CalibrationTestCaseType.RANK_HUMAN_OPTION_HIGHER

    input_snapshot_id: str | None = None

    agent_top_intervention_id: str | None = None
    human_selected_intervention_id: str | None = None

    expected_human_option_rank_at_or_above: int | None = None
    expected_no_regression: bool = True

    status: CalibrationTestCaseStatus = CalibrationTestCaseStatus.ACTIVE
    summary: str = ""


class RegressionTestedProposalBundle(BaseModel):
    """Convenience wrapper — all artifacts from a full proposal pipeline run."""
    proposal: CalibrationProposal = Field(default_factory=CalibrationProposal)
    report: RegressionReport = Field(default_factory=RegressionReport)
    impact: HistoricalValidationImpact = Field(default_factory=HistoricalValidationImpact)
    evidence_bundle: CalibrationEvidenceBundle = Field(default_factory=CalibrationEvidenceBundle)
    test_cases: list[CalibrationTestCase] = Field(default_factory=list)


# ── Hypothesis type → proposal type mapping ────────────────────────────

_HYPOTHESIS_TYPE_TO_PROPOSAL: dict[str, CalibrationProposalType] = {
    "add_missing_edge_type": CalibrationProposalType.ADD_MISSING_EDGE_TYPE,
    "add_constraint_type": CalibrationProposalType.ADD_CONSTRAINT_TYPE,
    "adjust_intervention_cost": CalibrationProposalType.ADJUST_INTERVENTION_COST,
    "increase_metric_weight": CalibrationProposalType.INCREASE_METRIC_WEIGHT,
    "decrease_metric_weight": CalibrationProposalType.DECREASE_METRIC_WEIGHT,
    "threshold_too_sensitive": CalibrationProposalType.ADD_FALSE_POSITIVE_SUPPRESSOR,
    "false_positive_pattern": CalibrationProposalType.ADD_FALSE_POSITIVE_SUPPRESSOR,
    "threshold_too_lenient": CalibrationProposalType.INCREASE_METRIC_WEIGHT,
    "wrong_metric_weight": CalibrationProposalType.ADJUST_INTERVENTION_COST,
    "missing_context_signal": CalibrationProposalType.ADD_CONSTRAINT_TYPE,
    "false_negative_pattern": CalibrationProposalType.INCREASE_METRIC_WEIGHT,
    "no_change": CalibrationProposalType.NO_CHANGE,
}


def map_hypothesis_type_to_proposal_type(
    hypothesis_type: CalibrationHypothesisType | str,
) -> CalibrationProposalType:
    value = getattr(hypothesis_type, "value", str(hypothesis_type))
    return _HYPOTHESIS_TYPE_TO_PROPOSAL.get(value, CalibrationProposalType.NO_CHANGE)


# ── Proposal creation ─────────────────────────────────────────────────

def _save(store: object | None, program_id: str, name: str, model: BaseModel) -> None:
    if store is not None:
        store.save(program_id, name, model)


def create_calibration_proposal_from_hypothesis(
    *,
    hypothesis: CalibrationHypothesis,
    artifact_store: object | None = None,
    program_id: str = "",
) -> CalibrationProposal:
    """Turn a CalibrationHypothesis into a reviewable CalibrationProposal.

    The proposal starts at ``PROPOSED`` and must pass regression testing
    before it can advance. Nothing is auto-applied.
    """
    proposal_type = map_hypothesis_type_to_proposal_type(hypothesis.hypothesis_type)

    proposal = CalibrationProposal(
        id=f"proposal-{uuid4().hex[:8]}",
        proposal_type=proposal_type,
        source_hypothesis_ids=[hypothesis.id],
        source_mismatch_ids=(
            [hypothesis.source_mismatch_id] if hypothesis.source_mismatch_id else []
        ),
        target_metric_name=hypothesis.metric_name_affected or None,
        proposed_change_summary=hypothesis.summary,
        status=ProposalStatus.PROPOSED,
        confidence=hypothesis.confidence,
    )

    _save(artifact_store, program_id, f"calibration_proposal_{proposal.id}", proposal)
    return proposal
