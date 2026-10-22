"""Evidence-backed agent decision recommendations.

The agent does not merely make it easier for a human to infer a
decision from evidence — it performs the inference itself and exposes
the strongest defensible recommendation *beside* the evidence.

Epistemic invariants:
- No hidden chain-of-thought. The stored artifact is a
  ``DecisionRationale``: supported facts, decisive factors, evidence
  for, evidence against, assumptions, unknowns, dissent, and why the
  selected option dominates the alternatives.
- Abstention is a feature. Close options, thin evidence, or unresolved
  critical unknowns produce CONTESTED / INSUFFICIENT_EVIDENCE /
  HUMAN_JUDGMENT_REQUIRED, never fake certainty.
- Advocacy is forbidden: a recommendation must carry the evidence
  arguing against it.
- Authority is not correctness. Who may decide and which option the
  evidence supports are separate fields that are never conflated.
- Confidence never reaches 1.0.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from lltpm.core.actors import ActorReference


# ── Enums ──────────────────────────────────────────────────────────────

class AgentRecommendationStatus(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    RECOMMENDATION_AVAILABLE = "recommendation_available"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONTESTED = "contested"
    HUMAN_JUDGMENT_REQUIRED = "human_judgment_required"


class DecisionRecommendationStrength(str, Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class DecisionEvaluationAction(str, Enum):
    VALIDATE = "validate"
    MODIFY = "modify"
    REJECT = "reject"
    DEFER = "defer"


class DecisionCriterionType(str, Enum):
    OBJECTIVE_ALIGNMENT = "objective_alignment"
    PRODUCT_VALUE = "product_value"
    KPI_IMPACT = "kpi_impact"
    LAUNCH_IMPACT = "launch_impact"
    DEPENDENCY_IMPACT = "dependency_impact"
    TECHNICAL_FEASIBILITY = "technical_feasibility"
    REVERSIBILITY = "reversibility"
    COMMITMENT_IMPACT = "commitment_impact"
    RISK = "risk"
    COST_OF_DELAY = "cost_of_delay"
    EVIDENCE_STRENGTH = "evidence_strength"


class DecisionConstraintType(str, Enum):
    HARD = "hard"
    SOFT = "soft"
    UNKNOWN = "unknown"


# ── Criteria and assessments ─────────────────────────────────────────

class DecisionCriterion(BaseModel):
    """Decision weighting is never hidden — every criterion states its
    weight and whether it is a hard requirement."""
    id: str
    criterion_type: DecisionCriterionType
    description: str
    weight: float
    hard_requirement: bool = False
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "DecisionCriterion":
        if not (0.0 <= self.weight <= 1.0):
            raise ValueError("Criterion weight must be within [0, 1].")
        return self


class DecisionOptionAssessment(BaseModel):
    """A derived assessment of one decision option. The original
    ``DecisionOption`` is never mutated."""
    option_id: str
    criterion_scores: dict[str, float] = Field(default_factory=dict)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    satisfied_hard_constraints: list[str] = Field(default_factory=list)
    violated_hard_constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    predicted_positive_impacts: list[str] = Field(default_factory=list)
    predicted_negative_impacts: list[str] = Field(default_factory=list)
    reversible: bool | None = None
    evidence_confidence: float = 0.0

    @model_validator(mode="after")
    def _check(self) -> "DecisionOptionAssessment":
        for criterion_id, score in self.criterion_scores.items():
            if not (0.0 <= score <= 1.0):
                raise ValueError(
                    f"Criterion score for '{criterion_id}' must be within "
                    f"[0, 1], got {score}."
                )
        if not (0.0 <= self.evidence_confidence <= 1.0):
            raise ValueError("evidence_confidence must be within [0, 1].")
        return self


class DecisionRationale(BaseModel):
    """The auditable explanation displayed beside the evidence — not
    chain-of-thought."""
    recommended_option_id: str | None = None
    summary: str = ""
    decisive_factors: list[str] = Field(default_factory=list)
    evidence_for_ids: list[str] = Field(default_factory=list)
    evidence_against_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_unknowns: list[str] = Field(default_factory=list)
    dissenting_positions: list[str] = Field(default_factory=list)
    alternative_rejection_reasons: dict[str, list[str]] = Field(
        default_factory=dict
    )
    confidence_explanation: str = ""


_MAX_CONFIDENCE = 0.99


class AgentDecisionRecommendation(BaseModel):
    id: str
    project_id: str
    workstream_id: str | None = None
    decision_capsule_id: str
    decision_question: str
    status: AgentRecommendationStatus
    recommended_option_id: str | None = None
    strength: DecisionRecommendationStrength | None = None
    confidence: float = 0.0
    assessments: list[DecisionOptionAssessment] = Field(default_factory=list)
    rationale: DecisionRationale = Field(default_factory=DecisionRationale)
    evidence_ids: list[str] = Field(default_factory=list)
    generated_at: datetime
    valid_as_of: datetime
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> "AgentDecisionRecommendation":
        if not (0.0 <= self.confidence <= _MAX_CONFIDENCE):
            raise ValueError(
                f"Recommendation confidence must be within "
                f"[0, {_MAX_CONFIDENCE}] — certainty is never warranted."
            )
        if self.status == AgentRecommendationStatus.RECOMMENDATION_AVAILABLE:
            if not self.recommended_option_id:
                raise ValueError(
                    "RECOMMENDATION_AVAILABLE requires a recommended "
                    "option."
                )
            if not self.evidence_ids:
                raise ValueError(
                    "A recommendation must be evidence-backed."
                )
        if (
            self.status
            in (
                AgentRecommendationStatus.INSUFFICIENT_EVIDENCE,
                AgentRecommendationStatus.NOT_EVALUATED,
            )
            and self.recommended_option_id is not None
        ):
            raise ValueError(
                "An abstaining recommendation must not pretend to choose "
                "an option."
            )
        if (
            self.status == AgentRecommendationStatus.CONTESTED
            and not self.assessments
        ):
            raise ValueError(
                "CONTESTED must preserve the competing assessments."
            )
        return self


class DecisionRecommendationPolicy(BaseModel):
    minimum_recommendation_confidence: float = 0.65
    strong_recommendation_threshold: float = 0.85
    moderate_recommendation_threshold: float = 0.72
    minimum_option_margin: float = 0.10
    require_evidence_for_all_hard_constraints: bool = True
    prohibit_recommendation_with_unresolved_critical_unknown: bool = True
    allow_recommendation_with_dissent: bool = True


# ── Human evaluation ─────────────────────────────────────────────────

class HumanDecisionEvaluation(BaseModel):
    id: str
    recommendation_id: str
    evaluator: ActorReference
    action: DecisionEvaluationAction
    selected_option_id: str | None = None
    modification_summary: str | None = None
    rejection_reason: str | None = None
    defer_reason: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    evaluated_at: datetime

    @model_validator(mode="after")
    def _check(self) -> "HumanDecisionEvaluation":
        if self.action in (
            DecisionEvaluationAction.VALIDATE,
            DecisionEvaluationAction.MODIFY,
        ) and not self.selected_option_id:
            raise ValueError(
                f"A {self.action.value} evaluation must name the selected "
                f"option."
            )
        if (
            self.action == DecisionEvaluationAction.REJECT
            and not self.rejection_reason
        ):
            raise ValueError("A rejection must state its reason.")
        if (
            self.action == DecisionEvaluationAction.DEFER
            and not self.defer_reason
        ):
            raise ValueError("A deferral must state what is missing.")
        return self
