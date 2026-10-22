"""Materiality: deciding what deserves a person's attention.

Every change this system computes is, by construction, correct. Almost
none of them are worth interrupting anyone about. P(slip) moving from
0.431 to 0.433 is a true fact and a worthless notification; P(>7-day
slip) moving from 0.12 to 0.31 is the same kind of fact and a genuinely
important one.

Three rules keep the line in the right place.

Absolute movement matters, but severe outcomes get a tighter threshold —
a small rise in the chance of something bad is worth more than a large
rise in the chance of something inconvenient.

Threshold crossings matter even when the movement is small. Going from
0.19 to 0.21 against an alert threshold of 0.20 is two points of
probability and a change in what the organization should do.

Low confidence is a *label*, not a silencer. A dramatic shift held with
0.12 confidence still gets surfaced — described as what it is, an
uncertain estimate that moved a lot. Suppressing uncertain risk is how a
system stays quiet right up until the thing happens.
"""

from __future__ import annotations

from tpllm.runtime.models import (
    IntelligenceArtifactType,
    IntelligenceMaterialityAssessment,
    MaterialityReason,
    NoActionReason,
    RecomputedArtifact,
    ScenarioMaterialityPolicy,
    ScenarioShiftAssessment,
)

# How much each reason contributes to the materiality score. Severe risk
# and required decisions dominate; incremental movement does not.
_REASON_WEIGHTS: dict[MaterialityReason, float] = {
    MaterialityReason.DECISION_REQUIRED: 0.9,
    MaterialityReason.SEVERE_FAILURE_RISK_CHANGE: 0.85,
    MaterialityReason.AUTONOMOUS_ACTION_BLOCKED: 0.8,
    MaterialityReason.DECISION_CHANGED: 0.75,
    MaterialityReason.NEW_PORTFOLIO_BOTTLENECK: 0.7,
    MaterialityReason.NEW_CONTRADICTION: 0.65,
    MaterialityReason.RECOMMENDATION_CHANGED: 0.6,
    MaterialityReason.KPI_THRESHOLD_CROSSED: 0.6,
    MaterialityReason.PORTFOLIO_PRIORITY_CHANGED: 0.55,
    MaterialityReason.READINESS_MATERIAL_CHANGE: 0.5,
    MaterialityReason.SCENARIO_PROBABILITY_SHIFT: 0.5,
    MaterialityReason.NEW_CRITICAL_UNKNOWN: 0.45,
    MaterialityReason.BOTTLENECK_RESOLVED: 0.4,
    MaterialityReason.CRITICAL_UNKNOWN_RESOLVED: 0.35,
}

# Reasons that genuinely need a person: judgment, authority, or a risk
# nobody has seen. Everything else updates state quietly.
_ATTENTION_REASONS = frozenset({
    MaterialityReason.DECISION_REQUIRED,
    MaterialityReason.DECISION_CHANGED,
    MaterialityReason.SEVERE_FAILURE_RISK_CHANGE,
    MaterialityReason.AUTONOMOUS_ACTION_BLOCKED,
    MaterialityReason.NEW_PORTFOLIO_BOTTLENECK,
    MaterialityReason.NEW_CONTRADICTION,
    MaterialityReason.RECOMMENDATION_CHANGED,
    MaterialityReason.PORTFOLIO_PRIORITY_CHANGED,
})

_ARTIFACT_REASONS: dict[IntelligenceArtifactType, MaterialityReason] = {
    IntelligenceArtifactType.LAUNCH_READINESS: (
        MaterialityReason.READINESS_MATERIAL_CHANGE
    ),
    IntelligenceArtifactType.DECISION_STATE: (
        MaterialityReason.DECISION_CHANGED
    ),
    IntelligenceArtifactType.DECISION_RECOMMENDATION: (
        MaterialityReason.RECOMMENDATION_CHANGED
    ),
    IntelligenceArtifactType.SCENARIO_DECISION_RECOMMENDATION: (
        MaterialityReason.RECOMMENDATION_CHANGED
    ),
    IntelligenceArtifactType.PORTFOLIO_BOTTLENECKS: (
        MaterialityReason.NEW_PORTFOLIO_BOTTLENECK
    ),
    IntelligenceArtifactType.PORTFOLIO_RECOMMENDATION: (
        MaterialityReason.PORTFOLIO_PRIORITY_CHANGED
    ),
}


def assess_scenario_probability_materiality(
    *,
    outcome_label: str,
    before_probability: float,
    after_probability: float,
    policy: ScenarioMaterialityPolicy,
    is_severe_outcome: bool = False,
    alert_threshold: float | None = None,
    probability_confidence: float = 0.0,
) -> ScenarioShiftAssessment:
    """Judge one outcome's probability movement.

    Confidence never suppresses. When it is below the policy floor the
    shift is still reported, carrying a qualifier so the reader knows the
    estimate is thin rather than being told nothing at all.
    """
    delta = round(abs(after_probability - before_probability), 9)
    threshold_crossed = False
    if alert_threshold is not None and policy.threshold_crossings_are_material:
        threshold_crossed = (
            (before_probability < alert_threshold <= after_probability)
            or (after_probability < alert_threshold <= before_probability)
        )

    floor = (
        policy.severe_outcome_probability_delta if is_severe_outcome
        else policy.minimum_absolute_probability_delta
    )
    material = delta >= floor or threshold_crossed
    low_confidence = (
        probability_confidence < policy.minimum_confidence_for_probability_alert
    )

    if not material:
        explanation = (
            f"P({outcome_label}) moved {before_probability:.3f} → "
            f"{after_probability:.3f} ({delta:.3f}), below the {floor:.2f} "
            f"threshold for {'a severe' if is_severe_outcome else 'this'} "
            f"outcome. Not worth an interruption."
        )
    else:
        explanation = (
            f"P({outcome_label}) moved {before_probability:.0%} → "
            f"{after_probability:.0%}"
            + (
                f", crossing the {alert_threshold:.0%} alert threshold"
                if threshold_crossed else ""
            )
            + "."
        )
        if low_confidence:
            explanation += (
                f" The estimate changed materially, but its confidence is "
                f"{probability_confidence:.2f} — treat it as a signal to "
                f"investigate, not as a firm number."
            )

    return ScenarioShiftAssessment(
        outcome_label=outcome_label,
        before_probability=before_probability,
        after_probability=after_probability,
        absolute_delta=delta,
        material=material,
        threshold_crossed=threshold_crossed,
        severe_outcome=is_severe_outcome,
        probability_confidence=probability_confidence,
        low_confidence_qualifier=material and low_confidence,
        explanation=explanation,
    )


def assess_scenario_shifts(
    *,
    before: dict[str, float],
    after: dict[str, float],
    policy: ScenarioMaterialityPolicy,
    severe_outcome_labels: list[str] | None = None,
    alert_thresholds: dict[str, float] | None = None,
    probability_confidence: float = 0.0,
) -> list[ScenarioShiftAssessment]:
    """Judge every outcome whose probability moved."""
    severe = set(severe_outcome_labels or [])
    thresholds = alert_thresholds or {}
    labels = sorted(set(before) | set(after))
    return [
        assess_scenario_probability_materiality(
            outcome_label=label,
            before_probability=before.get(label, 0.0),
            after_probability=after.get(label, 0.0),
            policy=policy,
            is_severe_outcome=label in severe,
            alert_threshold=thresholds.get(label),
            probability_confidence=probability_confidence,
        )
        for label in labels
    ]


def assess_artifact_materiality(
    *,
    artifact: RecomputedArtifact,
    policy: ScenarioMaterialityPolicy,
    severe_outcome_labels: list[str] | None = None,
    alert_thresholds: dict[str, float] | None = None,
    already_answered: bool = False,
    already_resolved: bool = False,
    duplicate: bool = False,
) -> IntelligenceMaterialityAssessment:
    """Decide whether one recomputed artifact is worth anyone's time.

    The default is no. Reasons accumulate only where there is something
    a person could actually act on or genuinely needs to know.
    """
    reasons: list[MaterialityReason] = []
    no_action: list[NoActionReason] = []
    shift_explanations: list[str] = []
    low_confidence = False

    if duplicate:
        no_action.append(NoActionReason.DUPLICATE)
    if already_resolved:
        no_action.append(NoActionReason.ALREADY_RESOLVED)
    if already_answered:
        no_action.append(NoActionReason.ALREADY_ANSWERED)

    if not artifact.changed:
        no_action.append(NoActionReason.NON_MATERIAL)
        return IntelligenceMaterialityAssessment(
            material=False,
            score=0.0,
            reasons=[],
            affected_project_ids=list(artifact.project_ids),
            human_attention_required=False,
            explanation=(
                f"{artifact.artifact_type.value} was recomputed and is "
                f"unchanged. State updated; nobody needs to hear about it."
            ),
            no_action_reasons=_dedupe_reasons(no_action),
        )

    if artifact.outcome_probabilities or artifact.previous_outcome_probabilities:
        shifts = assess_scenario_shifts(
            before=artifact.previous_outcome_probabilities,
            after=artifact.outcome_probabilities,
            policy=policy,
            severe_outcome_labels=severe_outcome_labels,
            alert_thresholds=alert_thresholds,
            probability_confidence=artifact.probability_confidence,
        )
        material_shifts = [s for s in shifts if s.material]
        for shift in material_shifts:
            shift_explanations.append(shift.explanation)
            if shift.severe_outcome:
                reasons.append(MaterialityReason.SEVERE_FAILURE_RISK_CHANGE)
            else:
                reasons.append(MaterialityReason.SCENARIO_PROBABILITY_SHIFT)
            if shift.threshold_crossed:
                reasons.append(MaterialityReason.KPI_THRESHOLD_CROSSED)
            low_confidence = low_confidence or shift.low_confidence_qualifier
        if not material_shifts and not shift_explanations:
            no_action.append(NoActionReason.NON_MATERIAL)

    artifact_reason = _ARTIFACT_REASONS.get(artifact.artifact_type)
    if artifact_reason is not None:
        reasons.append(artifact_reason)

    if artifact.decision_required:
        reasons.append(MaterialityReason.DECISION_REQUIRED)

    if artifact.severity in ("severe", "critical"):
        reasons.append(MaterialityReason.SEVERE_FAILURE_RISK_CHANGE)

    reasons = _dedupe_material_reasons(reasons)
    if not reasons:
        no_action.append(NoActionReason.NON_MATERIAL)
        return IntelligenceMaterialityAssessment(
            material=False,
            score=0.0,
            reasons=[],
            affected_project_ids=list(artifact.project_ids),
            human_attention_required=False,
            explanation=(
                f"{artifact.artifact_type.value} changed, but not by enough "
                f"to matter. State updated; no attention required."
            ),
            no_action_reasons=_dedupe_reasons(no_action),
        )

    score = round(max(_REASON_WEIGHTS.get(r, 0.3) for r in reasons), 6)
    attention = any(r in _ATTENTION_REASONS for r in reasons)
    if already_answered or already_resolved:
        attention = False
        no_action.append(NoActionReason.ALREADY_ANSWERED)

    explanation_parts = [
        f"{artifact.artifact_type.value} changed materially.",
        *shift_explanations,
    ]
    if artifact.decision_required:
        explanation_parts.append("A decision is required.")
    if not attention:
        explanation_parts.append(
            "No human judgment is required; internal state was updated."
        )
        no_action.append(NoActionReason.NO_HUMAN_JUDGMENT_REQUIRED)

    return IntelligenceMaterialityAssessment(
        material=True,
        score=score,
        reasons=reasons,
        affected_project_ids=list(artifact.project_ids),
        human_attention_required=attention,
        recommended_surface_types=_surface_types(reasons),
        explanation=" ".join(explanation_parts),
        low_confidence_qualifier=low_confidence,
        no_action_reasons=_dedupe_reasons(no_action),
    )


def _dedupe_material_reasons(
    reasons: list[MaterialityReason],
) -> list[MaterialityReason]:
    seen: list[MaterialityReason] = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    return sorted(seen, key=lambda r: (-_REASON_WEIGHTS.get(r, 0.0), r.value))


def _dedupe_reasons(reasons: list[NoActionReason]) -> list[NoActionReason]:
    seen: list[NoActionReason] = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    return seen


def _surface_types(reasons: list[MaterialityReason]) -> list[str]:
    surfaces: list[str] = []
    if MaterialityReason.DECISION_REQUIRED in reasons:
        surfaces.append("decision_request")
    if MaterialityReason.SEVERE_FAILURE_RISK_CHANGE in reasons:
        surfaces.append("risk_alert")
    if MaterialityReason.RECOMMENDATION_CHANGED in reasons:
        surfaces.append("recommendation_update")
    if MaterialityReason.NEW_PORTFOLIO_BOTTLENECK in reasons or (
        MaterialityReason.PORTFOLIO_PRIORITY_CHANGED in reasons
    ):
        surfaces.append("portfolio_update")
    if not surfaces:
        surfaces.append("status_update")
    return surfaces


def non_material_assessment(
    *,
    project_ids: list[str],
    reason: NoActionReason,
    explanation: str,
) -> IntelligenceMaterialityAssessment:
    """A first-class 'nothing to do here'."""
    return IntelligenceMaterialityAssessment(
        material=False,
        score=0.0,
        affected_project_ids=list(project_ids),
        human_attention_required=False,
        explanation=explanation,
        no_action_reasons=[reason],
    )


def should_research_before_asking(
    *,
    probability_confidence: float,
    expected_information_gain: float,
    rounds_used: int,
    budget: object,
) -> tuple[bool, str]:
    """Prefer finding out over asking.

    A person is the most expensive source of information in the
    organization, so internal research runs first — but only while it is
    still buying something. Once the expected gain falls below the floor
    or the round budget is spent, the loop stops rather than grinding.
    """
    maximum_rounds = getattr(
        budget, "maximum_research_rounds_per_causal_event", 3,
    )
    minimum_gain = getattr(budget, "minimum_expected_information_gain", 0.10)

    if rounds_used >= maximum_rounds:
        return False, (
            f"Research budget spent ({rounds_used}/{maximum_rounds} rounds); "
            f"further rounds would recompute without learning anything."
        )
    if expected_information_gain < minimum_gain:
        return False, (
            f"Expected information gain {expected_information_gain:.2f} is "
            f"below the {minimum_gain:.2f} floor; more research would not "
            f"change the answer."
        )
    return True, (
        f"Confidence is {probability_confidence:.2f} and the highest-value "
        f"unknown is worth {expected_information_gain:.2f}; research "
        f"internally before consuming anyone's attention."
    )
