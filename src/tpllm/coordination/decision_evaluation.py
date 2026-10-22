"""Decision evaluation and recommendation selection.

Criteria are derived from actual context — never a fixed weight table
applied blindly to every decision. Options violating a supported hard
constraint are ineligible, not merely penalized. Selection abstains
rather than manufacturing certainty.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tpllm.coordination.decision_capsules import DecisionCapsule
from tpllm.coordination.models import (
    DecisionPosition,
    PositionType,
    WorkstreamCommitment,
)
from tpllm.coordination.recommendations import (
    AgentDecisionRecommendation,
    AgentRecommendationStatus,
    DecisionCriterion,
    DecisionCriterionType,
    DecisionOptionAssessment,
    DecisionRationale,
    DecisionRecommendationPolicy,
    DecisionRecommendationStrength,
)
from tpllm.intelligence.launch_readiness import LaunchReadinessSnapshot
from tpllm.intelligence.project_understanding import (
    ProjectEvidence,
    ProjectUnderstandingSnapshot,
)


class DecisionEvaluationProvider(Protocol):
    """Port for option assessment. The domain never hardwires an LLM."""

    def evaluate(
        self,
        *,
        capsule: DecisionCapsule,
        criteria: list[DecisionCriterion],
        evidence: list[ProjectEvidence],
        understanding: ProjectUnderstandingSnapshot,
        readiness: LaunchReadinessSnapshot | None,
        kpi_context: list[object],
    ) -> list[DecisionOptionAssessment]:
        ...


# ── Criteria construction ────────────────────────────────────────────

_BASE_WEIGHTS: dict[DecisionCriterionType, float] = {
    DecisionCriterionType.OBJECTIVE_ALIGNMENT: 0.85,
    DecisionCriterionType.EVIDENCE_STRENGTH: 0.60,
    DecisionCriterionType.DEPENDENCY_IMPACT: 0.75,
    DecisionCriterionType.LAUNCH_IMPACT: 0.80,
    DecisionCriterionType.KPI_IMPACT: 0.70,
    DecisionCriterionType.COMMITMENT_IMPACT: 0.90,
    DecisionCriterionType.REVERSIBILITY: 0.50,
    DecisionCriterionType.COST_OF_DELAY: 0.65,
    DecisionCriterionType.PRODUCT_VALUE: 0.60,
    DecisionCriterionType.TECHNICAL_FEASIBILITY: 0.80,
    DecisionCriterionType.RISK: 0.70,
}


def build_decision_criteria(
    *,
    capsule: DecisionCapsule,
    understanding: ProjectUnderstandingSnapshot,
    readiness: LaunchReadinessSnapshot | None,
    kpi_context: list[object],
    commitments: list[WorkstreamCommitment],
) -> list[DecisionCriterion]:
    """Criteria derived from the decision's actual context. Weights vary
    with what is genuinely at stake — a decision touching a customer
    commitment weights commitment impact as a hard requirement; one with
    no KPI linkage gets no KPI criterion at all."""
    criteria: list[DecisionCriterion] = []
    counter = 0

    def add(
        criterion_type: DecisionCriterionType,
        description: str,
        *,
        weight: float | None = None,
        hard: bool = False,
        evidence_ids: list[str] | None = None,
    ) -> None:
        nonlocal counter
        counter += 1
        criteria.append(
            DecisionCriterion(
                id=f"crit-{counter}-{criterion_type.value}",
                criterion_type=criterion_type,
                description=description,
                weight=weight if weight is not None else _BASE_WEIGHTS[
                    criterion_type
                ],
                hard_requirement=hard,
                evidence_ids=list(evidence_ids or []),
            )
        )

    # Always: does the option serve the objective, and is it evidenced?
    add(
        DecisionCriterionType.OBJECTIVE_ALIGNMENT,
        f"Advances the workstream objective and the decision question: "
        f"{capsule.decision_question}",
        evidence_ids=capsule.evidence_ids[:5],
    )
    add(
        DecisionCriterionType.EVIDENCE_STRENGTH,
        "Supported by fresh, non-contested evidence.",
        evidence_ids=capsule.evidence_ids[:5],
    )

    # Technical feasibility is a hard requirement when the mechanism is
    # understood well enough to judge it.
    if understanding.technical_mechanism.steps:
        add(
            DecisionCriterionType.TECHNICAL_FEASIBILITY,
            "Compatible with the understood technical mechanism.",
            hard=True,
            evidence_ids=understanding.technical_mechanism.evidence_ids[:3],
        )

    # Dependency and launch impact only when something is actually
    # blocked or a launch is actually at stake.
    if "unblock" in capsule.impact_if_decided.lower() or (
        "block" in capsule.impact_if_delayed.lower()
    ):
        add(
            DecisionCriterionType.DEPENDENCY_IMPACT,
            f"Unblocks dependent work: {capsule.impact_if_decided}",
            evidence_ids=capsule.evidence_ids[:3],
        )
    if readiness is not None:
        weight = 0.80 + min(
            0.15, 0.05 * len(readiness.scorecard.blockers)
        )
        add(
            DecisionCriterionType.LAUNCH_IMPACT,
            f"Effect on launch readiness (currently "
            f"{readiness.scorecard.overall_score:.0f}, on-time probability "
            f"{readiness.forecast.on_time_probability:.2f}).",
            weight=min(1.0, weight),
        )
    if capsule.affected_kpi_ids or kpi_context:
        add(
            DecisionCriterionType.KPI_IMPACT,
            f"Moves affected KPIs: "
            f"{', '.join(capsule.affected_kpi_ids) or 'linked KPIs'}.",
        )

    # A commitment in play makes commitment impact a hard requirement.
    relevant_commitments = [
        c for c in commitments
        if capsule.workstream_id is None or c.workstream_id == (
            capsule.workstream_id
        )
    ]
    external = [
        c for c in relevant_commitments
        if c.commitment_type.value in ("customer", "partner", "regulatory")
    ]
    if relevant_commitments:
        add(
            DecisionCriterionType.COMMITMENT_IMPACT,
            "Does not violate an existing commitment: "
            + "; ".join(c.description for c in relevant_commitments[:3]),
            hard=bool(external),
            evidence_ids=[
                e for c in relevant_commitments[:3] for e in c.evidence_ids
            ],
        )

    add(
        DecisionCriterionType.REVERSIBILITY,
        "Can be reversed if the assumption proves wrong.",
    )
    add(
        DecisionCriterionType.COST_OF_DELAY,
        f"Cost of not deciding now: {capsule.impact_if_delayed}",
    )
    if capsule.open_disagreements:
        add(
            DecisionCriterionType.RISK,
            "Residual risk given open disagreements: "
            + "; ".join(capsule.open_disagreements[:3]),
        )

    return criteria


# ── Scoring ──────────────────────────────────────────────────────────

def option_is_eligible(assessment: DecisionOptionAssessment) -> bool:
    """A hard-constraint violation makes an option ineligible — not
    merely lower-scoring."""
    return not assessment.violated_hard_constraints


def score_decision_option(
    *,
    assessment: DecisionOptionAssessment,
    criteria: list[DecisionCriterion],
) -> float:
    """Transparent weighted mean over the criteria the assessment
    actually scored."""
    weighted_sum = 0.0
    weight_total = 0.0
    for criterion in criteria:
        if criterion.id not in assessment.criterion_scores:
            continue
        weighted_sum += criterion.weight * assessment.criterion_scores[
            criterion.id
        ]
        weight_total += criterion.weight
    if weight_total == 0.0:
        return 0.0
    return weighted_sum / weight_total


# ── Selection ────────────────────────────────────────────────────────

def _dissent_lines(positions: list[DecisionPosition]) -> list[str]:
    dissenting = {
        PositionType.OPPOSE, PositionType.CONCERN, PositionType.CONDITIONAL,
    }
    return [
        f"{p.actor.team_name or p.actor.team_id or 'team unknown'} "
        f"({p.actor.role or 'role unknown'}): {p.statement}"
        for p in positions
        if p.position_type in dissenting
    ]


def select_agent_recommendation(
    *,
    capsule: DecisionCapsule,
    assessments: list[DecisionOptionAssessment],
    criteria: list[DecisionCriterion],
    policy: DecisionRecommendationPolicy,
    critical_unknowns: list[str],
    now: datetime | None = None,
    expires_at: datetime | None = None,
) -> AgentDecisionRecommendation:
    """Filter ineligible options, score, check margin, evidence
    confidence, and critical unknowns — then recommend or abstain."""
    stamp = now or capsule.last_relevant_discussion_at or datetime.utcnow()
    dissent = _dissent_lines(capsule.actor_positions)

    def abstain(
        status: AgentRecommendationStatus,
        summary: str,
        confidence: float = 0.0,
        rejection_reasons: dict[str, list[str]] | None = None,
        unknowns: list[str] | None = None,
    ) -> AgentDecisionRecommendation:
        return AgentDecisionRecommendation(
            id=f"rec-{capsule.id}",
            project_id=capsule.project_id,
            workstream_id=capsule.workstream_id,
            decision_capsule_id=capsule.id,
            decision_question=capsule.decision_question,
            status=status,
            recommended_option_id=None,
            strength=None,
            confidence=min(confidence, 0.99),
            assessments=list(assessments),
            rationale=DecisionRationale(
                recommended_option_id=None,
                summary=summary,
                evidence_for_ids=[],
                evidence_against_ids=sorted(
                    {
                        e
                        for a in assessments
                        for e in a.contradicting_evidence_ids
                    }
                ),
                unresolved_unknowns=list(
                    unknowns if unknowns is not None else critical_unknowns
                ),
                dissenting_positions=dissent,
                alternative_rejection_reasons=rejection_reasons or {},
                confidence_explanation=summary,
            ),
            evidence_ids=list(capsule.evidence_ids),
            generated_at=stamp,
            valid_as_of=stamp,
            expires_at=expires_at,
        )

    if not assessments:
        return abstain(
            AgentRecommendationStatus.INSUFFICIENT_EVIDENCE,
            "No options were assessed; the agent has nothing to compare.",
        )

    rejection_reasons: dict[str, list[str]] = {}
    eligible: list[DecisionOptionAssessment] = []
    for assessment in assessments:
        if option_is_eligible(assessment):
            eligible.append(assessment)
        else:
            rejection_reasons[assessment.option_id] = [
                f"Violates hard constraint: {constraint}"
                for constraint in assessment.violated_hard_constraints
            ]

    if not eligible:
        return abstain(
            AgentRecommendationStatus.HUMAN_JUDGMENT_REQUIRED,
            "Every option violates a hard constraint; this requires human "
            "judgment about the constraint itself.",
            rejection_reasons=rejection_reasons,
        )

    scored = sorted(
        ((score_decision_option(assessment=a, criteria=criteria), a)
         for a in eligible),
        key=lambda pair: (-pair[0], pair[1].option_id),
    )
    top_score, top = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    margin = top_score - second_score

    # Evidence confidence gates the recommendation's confidence.
    confidence = min(0.99, top_score * max(0.0, top.evidence_confidence))

    if critical_unknowns and (
        policy.prohibit_recommendation_with_unresolved_critical_unknown
    ):
        return abstain(
            AgentRecommendationStatus.HUMAN_JUDGMENT_REQUIRED,
            f"An unresolved critical unknown blocks a recommendation: "
            f"{critical_unknowns[0]}",
            confidence=confidence,
            rejection_reasons=rejection_reasons,
        )

    if len(scored) > 1 and margin < policy.minimum_option_margin:
        return abstain(
            AgentRecommendationStatus.CONTESTED,
            f"Top options are separated by only {margin:.2f} (threshold "
            f"{policy.minimum_option_margin:.2f}); the evidence does not "
            f"clearly favor one.",
            confidence=confidence,
            rejection_reasons=rejection_reasons,
        )

    if confidence < policy.minimum_recommendation_confidence:
        return abstain(
            AgentRecommendationStatus.INSUFFICIENT_EVIDENCE,
            f"Best option scores {top_score:.2f} with evidence confidence "
            f"{top.evidence_confidence:.2f}, below the "
            f"{policy.minimum_recommendation_confidence:.2f} minimum.",
            confidence=confidence,
            rejection_reasons=rejection_reasons,
        )

    if not policy.allow_recommendation_with_dissent and dissent:
        return abstain(
            AgentRecommendationStatus.CONTESTED,
            "Policy prohibits recommending while dissent is unresolved.",
            confidence=confidence,
            rejection_reasons=rejection_reasons,
        )

    if confidence >= policy.strong_recommendation_threshold:
        strength = DecisionRecommendationStrength.STRONG
    elif confidence >= policy.moderate_recommendation_threshold:
        strength = DecisionRecommendationStrength.MODERATE
    else:
        strength = DecisionRecommendationStrength.WEAK

    for score, assessment in scored[1:]:
        rejection_reasons.setdefault(assessment.option_id, []).append(
            f"Scores {score:.2f} versus {top_score:.2f} for the "
            f"recommended option."
        )

    decisive: list[str] = []
    for criterion in sorted(criteria, key=lambda c: -c.weight):
        score = top.criterion_scores.get(criterion.id)
        if score is None or score < 0.6:
            continue
        decisive.append(f"{criterion.description} (score {score:.2f})")
        if len(decisive) >= 5:
            break
    decisive.extend(top.predicted_positive_impacts[:2])

    evidence_ids = list(capsule.evidence_ids)
    for eid in top.supporting_evidence_ids:
        if eid not in evidence_ids:
            evidence_ids.append(eid)

    rationale = DecisionRationale(
        recommended_option_id=top.option_id,
        summary=(
            f"{top.option_id} scores {top_score:.2f}, ahead of the next "
            f"option by {margin:.2f}, with evidence confidence "
            f"{top.evidence_confidence:.2f}."
        ),
        decisive_factors=decisive,
        evidence_for_ids=list(top.supporting_evidence_ids),
        # Advocacy is forbidden: the counter-evidence always travels
        # with the recommendation.
        evidence_against_ids=list(top.contradicting_evidence_ids),
        assumptions=list(top.assumptions),
        unresolved_unknowns=list(critical_unknowns),
        dissenting_positions=dissent,
        alternative_rejection_reasons=rejection_reasons,
        confidence_explanation=(
            f"Confidence {confidence:.2f} = option score {top_score:.2f} "
            f"scaled by evidence confidence {top.evidence_confidence:.2f}; "
            f"capped below certainty."
        ),
    )

    return AgentDecisionRecommendation(
        id=f"rec-{capsule.id}",
        project_id=capsule.project_id,
        workstream_id=capsule.workstream_id,
        decision_capsule_id=capsule.id,
        decision_question=capsule.decision_question,
        status=AgentRecommendationStatus.RECOMMENDATION_AVAILABLE,
        recommended_option_id=top.option_id,
        strength=strength,
        confidence=confidence,
        assessments=list(assessments),
        rationale=rationale,
        evidence_ids=evidence_ids,
        generated_at=stamp,
        valid_as_of=stamp,
        expires_at=expires_at,
    )
