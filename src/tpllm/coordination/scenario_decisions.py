"""Scenario-aware decision optimization.

Agent recommendations ask "which option looks best on current
evidence?". The scenario lattice asks "what futures are plausible?".

This module composes them to ask the question that actually matters:

    Which option creates the strongest distribution of plausible futures?

An option can score lower right now and still be the better decision
because it carries less tail risk, stays reversible, and preserves the
organization's ability to respond to what it learns next.

The static assessment is not replaced. It remains the default
preference, and scenario analysis must clear an explicit advantage
margin — and a probability-confidence floor — before overturning it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from tpllm.coordination.decision_capsules import DecisionCapsule, DecisionOption
from tpllm.coordination.decision_timing import (
    DecisionTimingAction,
    DecisionTimingAssessment,
)
from tpllm.coordination.decision_utility import (
    ScenarioOutcomeUtility,
    calculate_expected_option_utility,
    normalize_utility_to_unit,
)
from tpllm.coordination.optionality import (
    OptionalityAssessment,
    RecoverabilityAssessment,
    assess_optionality,
    assess_recoverability,
)
from tpllm.coordination.recommendations import (
    AgentDecisionRecommendation,
    AgentRecommendationStatus,
    DecisionOptionAssessment,
    DecisionRationale,
)
from tpllm.coordination.regret import DecisionRegretAssessment
from tpllm.scenarios.models import ScenarioLattice
from tpllm.scenarios.probability import (
    enumerate_paths,
    omitted_probability_mass,
    probability_of_outcome,
)
from tpllm.temporal.models import ProjectTemporalState


class DecisionPosture(str, Enum):
    """How trade-offs are weighed. Posture changes emphasis, never
    evidence."""
    EXPECTED_VALUE = "expected_value"
    MINIMIZE_REGRET = "minimize_regret"
    RISK_AVERSE = "risk_averse"
    MAXIMIZE_SUCCESS_PROBABILITY = "maximize_success_probability"
    PRESERVE_OPTIONALITY = "preserve_optionality"
    MINIMIZE_COST_OF_DELAY = "minimize_cost_of_delay"


class ScenarioDecisionPolicy(BaseModel):
    posture: DecisionPosture = DecisionPosture.PRESERVE_OPTIONALITY
    severe_failure_threshold: float = 0.20
    maximum_acceptable_severe_failure_probability: float = 0.15
    tail_risk_weight: float = 0.30
    expected_value_weight: float = 0.25
    success_probability_weight: float = 0.20
    reversibility_weight: float = 0.10
    recoverability_weight: float = 0.10
    optionality_weight: float = 0.05
    minimum_scenario_probability_confidence: float = 0.40
    minimum_option_advantage: float = 0.05
    allow_research_before_decision: bool = True
    value_of_information_threshold: float = 0.20

    @model_validator(mode="after")
    def _check(self) -> "ScenarioDecisionPolicy":
        weights = {
            "tail_risk_weight": self.tail_risk_weight,
            "expected_value_weight": self.expected_value_weight,
            "success_probability_weight": self.success_probability_weight,
            "reversibility_weight": self.reversibility_weight,
            "recoverability_weight": self.recoverability_weight,
            "optionality_weight": self.optionality_weight,
        }
        for name, value in weights.items():
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be within [0, 1].")
        total = sum(weights.values())
        if not (0.95 <= total <= 1.05):
            raise ValueError(
                f"Scoring weights must sum to approximately 1.0; got "
                f"{total:.3f}. Weighting is explicit policy, not a hidden "
                f"constant."
            )
        for name in (
            "minimum_scenario_probability_confidence",
            "minimum_option_advantage",
            "value_of_information_threshold",
            "severe_failure_threshold",
            "maximum_acceptable_severe_failure_probability",
        ):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be within [0, 1].")
        return self


class SevereOutcomeDefinition(BaseModel):
    """What counts as severe is configured, never hardcoded."""
    id: str
    description: str
    outcome_predicates: list[str] = Field(default_factory=list)
    severity: float
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "SevereOutcomeDefinition":
        if not (0.0 <= self.severity <= 1.0):
            raise ValueError("severity must be within [0, 1].")
        return self


def default_severe_outcomes() -> list[SevereOutcomeDefinition]:
    return [
        SevereOutcomeDefinition(
            id="severe-slip",
            description="Launch slips beyond the acceptable window.",
            outcome_predicates=["launch_slips"],
            severity=0.8,
        ),
    ]


# ── Counterfactual construction ──────────────────────────────────────

class DecisionOptionCounterfactual(BaseModel):
    option_id: str
    root_temporal_state_id: str
    counterfactual_state_id: str
    assumed_decision_effects: list[str] = Field(default_factory=list)
    changed_state_fields: dict[str, Any] = Field(default_factory=dict)
    preserved_state_fields: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class CounterfactualOverreachError(ValueError):
    """Raised when a counterfactual would assume downstream success.

    Choosing an option settles the decision; it does not make the
    integration pass, the KPI recover, or the milestone land. Those stay
    probabilistic future behavior.
    """


# State dimensions a decision may legitimately set directly. Everything
# else is downstream and must remain probabilistic.
_DECISION_SETTABLE_FIELDS = {"decision_states", "blockers_resolved"}

# Substrings that mark a field as an outcome rather than a decision.
_OUTCOME_FIELD_MARKERS = (
    "integration", "kpi", "milestone", "outcome", "launch", "workstream_health",
)


def _validate_effects(effects: dict[str, Any]) -> None:
    for key in effects:
        if key in _DECISION_SETTABLE_FIELDS:
            continue
        lowered = key.lower()
        if any(marker in lowered for marker in _OUTCOME_FIELD_MARKERS):
            raise CounterfactualOverreachError(
                f"A counterfactual may not assert '{key}'. Approving an "
                f"option settles the decision; it does not make the "
                f"downstream outcome succeed."
            )
        raise CounterfactualOverreachError(
            f"'{key}' is not a decision-settable state dimension. "
            f"Permitted: {sorted(_DECISION_SETTABLE_FIELDS)}."
        )


def build_option_counterfactual(
    *,
    base_state: ProjectTemporalState,
    capsule: DecisionCapsule,
    option: DecisionOption,
    assessment: DecisionOptionAssessment,
    as_of: datetime,
    additional_effects: dict[str, Any] | None = None,
) -> DecisionOptionCounterfactual:
    """The world in which this option was chosen — and nothing more.

    Only dimensions the decision itself settles are changed. Downstream
    dimensions are listed as preserved, so the option's future remains
    genuinely uncertain rather than assumed successful.
    """
    effects: dict[str, Any] = {
        "decision_states": {capsule.id: option.id},
    }
    # Choosing settles the ask that was blocking; it resolves nothing else.
    if base_state.open_action_request_ids:
        effects["blockers_resolved"] = list(base_state.active_blocker_ids)

    if additional_effects:
        _validate_effects(additional_effects)
        for key, value in additional_effects.items():
            if key == "decision_states" and isinstance(value, dict):
                effects["decision_states"] = {
                    **effects["decision_states"], **value,
                }
            else:
                effects[key] = value

    preserved = [
        "kpi_states",
        "integration_state",
        "milestone_state",
        "workstream_health",
    ]

    return DecisionOptionCounterfactual(
        option_id=option.id,
        root_temporal_state_id=base_state.state_fingerprint,
        counterfactual_state_id=(
            f"cf-{base_state.state_fingerprint}-{option.id}"
        ),
        assumed_decision_effects=[
            f"The decision '{capsule.decision_question}' is settled as "
            f"{option.label}.",
        ],
        changed_state_fields=effects,
        preserved_state_fields=preserved,
        assumptions=list(assessment.assumptions),
        evidence_ids=list(assessment.supporting_evidence_ids),
    )


# ── Option scenario analysis ─────────────────────────────────────────

class OptionScenarioAnalysis(BaseModel):
    option_id: str
    lattice_id: str
    target_success_probability: float
    severe_failure_probability: float
    expected_delay_days: float
    probability_delay_gt_7_days: float
    expected_value: float
    recoverability: float
    reversibility: float
    optionality: float
    worst_credible_outcome: str | None = None
    dominant_success_paths: list[str] = Field(default_factory=list)
    dominant_failure_paths: list[str] = Field(default_factory=list)
    highest_sensitivity_factor_ids: list[str] = Field(default_factory=list)
    valuable_information_unknown_ids: list[str] = Field(default_factory=list)
    probability_confidence: float = 0.0
    tail_probability_mass: float = 0.0


def analyse_option_lattice(
    *,
    option_id: str,
    lattice: ScenarioLattice,
    assessment: DecisionOptionAssessment,
    severe_outcomes: list[SevereOutcomeDefinition] | None = None,
    success_outcome: str = "launch_on_time",
    outcome_utilities: list[ScenarioOutcomeUtility] | None = None,
    expected_delay_days: float = 0.0,
    probability_delay_gt_7_days: float = 0.0,
) -> OptionScenarioAnalysis:
    """Summarize one option's modeled future distribution."""
    severe_outcomes = severe_outcomes or default_severe_outcomes()

    success_probability, success_confidence = probability_of_outcome(
        lattice=lattice, outcome_label=success_outcome,
    )

    severe_probability = 0.0
    worst: str | None = None
    worst_severity = -1.0
    for definition in severe_outcomes:
        for predicate in definition.outcome_predicates:
            probability, _ = probability_of_outcome(
                lattice=lattice, outcome_label=predicate,
            )
            severe_probability = max(severe_probability, probability)
            if probability > 0 and definition.severity > worst_severity:
                worst_severity = definition.severity
                worst = definition.description

    paths = enumerate_paths(lattice=lattice)
    success_paths = [
        p.id for p in paths if success_outcome in p.outcome_labels
    ][:5]
    failure_paths = [
        p.id for p in paths
        if any(
            predicate in p.outcome_labels
            for d in severe_outcomes
            for predicate in d.outcome_predicates
        )
    ][:5]

    optionality = assess_optionality(option_id=option_id, lattice=lattice)
    recoverability = assess_recoverability(
        option_id=option_id, lattice=lattice,
    )
    reversibility = (
        1.0 if assessment.reversible is True
        else 0.0 if assessment.reversible is False
        else 0.5
    )

    root = lattice.state(lattice.root_state_id)
    unknowns = list(root.unresolved_unknowns) if root else []

    analysis = OptionScenarioAnalysis(
        option_id=option_id,
        lattice_id=lattice.id,
        target_success_probability=round(success_probability, 6),
        severe_failure_probability=round(severe_probability, 6),
        expected_delay_days=expected_delay_days,
        probability_delay_gt_7_days=probability_delay_gt_7_days,
        expected_value=0.0,
        recoverability=recoverability.recoverability_score,
        reversibility=reversibility,
        optionality=optionality.optionality_score,
        worst_credible_outcome=worst,
        dominant_success_paths=success_paths,
        dominant_failure_paths=failure_paths,
        valuable_information_unknown_ids=unknowns,
        probability_confidence=round(success_confidence, 6),
        tail_probability_mass=round(omitted_probability_mass(lattice), 6),
    )

    expected_value = calculate_expected_option_utility(
        analysis=analysis,
        outcome_utilities=outcome_utilities or [],
    )
    return analysis.model_copy(
        update={"expected_value": round(expected_value, 6)}
    )


# ── Posture-weighted scoring ─────────────────────────────────────────

class ScenarioAdjustedOptionScore(BaseModel):
    option_id: str
    static_score: float
    scenario_score: float
    expected_value_component: float
    success_probability_component: float
    tail_risk_component: float
    reversibility_component: float
    recoverability_component: float
    optionality_component: float
    expected_regret: float
    posture: DecisionPosture
    explanation: str = ""


# Emphasis multipliers per posture, applied to the policy weights and
# then renormalized. Posture shifts what matters; it never edits evidence.
_POSTURE_EMPHASIS: dict[DecisionPosture, dict[str, float]] = {
    DecisionPosture.EXPECTED_VALUE: {"expected_value": 3.0},
    DecisionPosture.RISK_AVERSE: {"tail_risk": 2.5, "recoverability": 1.5},
    DecisionPosture.MAXIMIZE_SUCCESS_PROBABILITY: {
        "success_probability": 3.0,
    },
    DecisionPosture.PRESERVE_OPTIONALITY: {
        "reversibility": 3.0, "optionality": 3.0, "recoverability": 2.0,
    },
    DecisionPosture.MINIMIZE_REGRET: {"expected_value": 1.5, "tail_risk": 1.5},
    DecisionPosture.MINIMIZE_COST_OF_DELAY: {"success_probability": 2.0},
}


def resolve_posture_weights(
    *, policy: ScenarioDecisionPolicy,
) -> dict[str, float]:
    """Policy weights adjusted for posture, renormalized to sum to 1."""
    base = {
        "expected_value": policy.expected_value_weight,
        "success_probability": policy.success_probability_weight,
        "tail_risk": policy.tail_risk_weight,
        "reversibility": policy.reversibility_weight,
        "recoverability": policy.recoverability_weight,
        "optionality": policy.optionality_weight,
    }
    emphasis = _POSTURE_EMPHASIS.get(policy.posture, {})
    adjusted = {
        key: value * emphasis.get(key, 1.0) for key, value in base.items()
    }
    total = sum(adjusted.values()) or 1.0
    return {key: value / total for key, value in adjusted.items()}


def score_option_for_posture(
    *,
    option_analysis: OptionScenarioAnalysis,
    static_assessment: DecisionOptionAssessment,
    regret: DecisionRegretAssessment,
    optionality: OptionalityAssessment,
    recoverability: RecoverabilityAssessment,
    policy: ScenarioDecisionPolicy,
    static_score: float,
) -> ScenarioAdjustedOptionScore:
    """Combine the option's future distribution into one comparable
    score under the active posture."""
    weights = resolve_posture_weights(policy=policy)

    # Expected value is anchored on the static score and moved by the
    # modeled distribution, so an option cannot win on scenario grounds
    # alone while being weak on present evidence.
    modeled_value = normalize_utility_to_unit(option_analysis.expected_value)
    expected_value_component = 0.5 * static_score + 0.5 * modeled_value

    success_component = option_analysis.target_success_probability
    tail_component = option_analysis.severe_failure_probability
    reversibility_component = option_analysis.reversibility
    recoverability_component = recoverability.recoverability_score
    optionality_component = optionality.optionality_score

    score = (
        weights["expected_value"] * expected_value_component
        + weights["success_probability"] * success_component
        - weights["tail_risk"] * tail_component
        + weights["reversibility"] * reversibility_component
        + weights["recoverability"] * recoverability_component
        + weights["optionality"] * optionality_component
    )

    if policy.posture == DecisionPosture.MINIMIZE_REGRET:
        # Regret dominates under this posture, applied as a penalty so
        # the components above remain visible.
        score -= regret.expected_regret

    return ScenarioAdjustedOptionScore(
        option_id=option_analysis.option_id,
        static_score=round(static_score, 6),
        scenario_score=round(score, 6),
        expected_value_component=round(expected_value_component, 6),
        success_probability_component=round(success_component, 6),
        tail_risk_component=round(tail_component, 6),
        reversibility_component=round(reversibility_component, 6),
        recoverability_component=round(recoverability_component, 6),
        optionality_component=round(optionality_component, 6),
        expected_regret=regret.expected_regret,
        posture=policy.posture,
        explanation=(
            f"{option_analysis.option_id} under {policy.posture.value}: "
            f"P(success) {success_component:.2f}, P(severe failure) "
            f"{tail_component:.2f}, reversibility "
            f"{reversibility_component:.2f}, recoverability "
            f"{recoverability_component:.2f}, optionality "
            f"{optionality_component:.2f}; static {static_score:.2f}."
        ),
    )


# ── Scenario-aware recommendation ────────────────────────────────────

class ScenarioAwareDecisionRecommendation(BaseModel):
    recommendation_id: str
    base_recommendation_id: str
    recommended_option_id: str | None = None
    posture: DecisionPosture
    status: AgentRecommendationStatus
    static_preferred_option_id: str | None = None
    scenario_preferred_option_id: str | None = None
    recommendation_changed_by_scenario_analysis: bool = False
    option_scores: list[ScenarioAdjustedOptionScore] = Field(
        default_factory=list
    )
    timing: DecisionTimingAssessment
    rationale: DecisionRationale = Field(default_factory=DecisionRationale)
    scenario_rationale: str = ""
    dominant_success_paths: list[str] = Field(default_factory=list)
    dominant_failure_paths: list[str] = Field(default_factory=list)
    important_unknown_ids: list[str] = Field(default_factory=list)
    scenario_probability_confidence: float = 0.0
    omitted_probability_mass: float = 0.0
    generated_at: datetime


def build_scenario_aware_recommendation(
    *,
    base_recommendation: AgentDecisionRecommendation,
    option_scores: list[ScenarioAdjustedOptionScore],
    option_analyses: list[OptionScenarioAnalysis],
    timing: DecisionTimingAssessment,
    policy: ScenarioDecisionPolicy,
    now: datetime,
) -> ScenarioAwareDecisionRecommendation:
    """Decide whether the modeled futures justify overturning the static
    preference.

    The static preference is the default. Scenario analysis must beat it
    by ``minimum_option_advantage`` *and* rest on probability confidence
    above the policy floor before the recommendation changes.
    """
    analyses = {a.option_id: a for a in option_analyses}

    static_preferred = base_recommendation.recommended_option_id
    if static_preferred is None and option_scores:
        static_preferred = sorted(
            option_scores, key=lambda s: (-s.static_score, s.option_id),
        )[0].option_id

    if not option_scores:
        return ScenarioAwareDecisionRecommendation(
            recommendation_id=f"scenrec-{base_recommendation.id}",
            base_recommendation_id=base_recommendation.id,
            recommended_option_id=static_preferred,
            posture=policy.posture,
            status=base_recommendation.status,
            static_preferred_option_id=static_preferred,
            scenario_preferred_option_id=None,
            recommendation_changed_by_scenario_analysis=False,
            timing=timing,
            rationale=base_recommendation.rationale,
            scenario_rationale=(
                "No option lattices were analysed; the static "
                "recommendation stands unchanged."
            ),
            generated_at=now,
        )

    ranked = sorted(
        option_scores, key=lambda s: (-s.scenario_score, s.option_id),
    )
    scenario_preferred = ranked[0].option_id
    by_option = {s.option_id: s for s in option_scores}

    # An option absent from the analysed set was disqualified upstream —
    # a hard-constraint violation is the usual reason. It must never be
    # recommended by falling through as the static preference.
    if static_preferred is not None and static_preferred not in by_option:
        return ScenarioAwareDecisionRecommendation(
            recommendation_id=f"scenrec-{base_recommendation.id}",
            base_recommendation_id=base_recommendation.id,
            recommended_option_id=scenario_preferred,
            posture=policy.posture,
            status=base_recommendation.status,
            static_preferred_option_id=static_preferred,
            scenario_preferred_option_id=scenario_preferred,
            recommendation_changed_by_scenario_analysis=True,
            option_scores=ranked,
            timing=timing,
            rationale=base_recommendation.rationale,
            scenario_rationale=(
                f"'{static_preferred}' is not among the eligible options — "
                f"it was disqualified before scenario analysis and cannot "
                f"be recommended. {scenario_preferred} is the best eligible "
                f"option."
            ),
            dominant_success_paths=list(
                analyses[scenario_preferred].dominant_success_paths
                if scenario_preferred in analyses else []
            ),
            dominant_failure_paths=list(
                analyses[scenario_preferred].dominant_failure_paths
                if scenario_preferred in analyses else []
            ),
            important_unknown_ids=list(
                analyses[scenario_preferred].valuable_information_unknown_ids
                if scenario_preferred in analyses else []
            ),
            scenario_probability_confidence=round(
                min(
                    (a.probability_confidence for a in option_analyses),
                    default=0.0,
                ), 6,
            ),
            omitted_probability_mass=round(
                max(
                    (a.tail_probability_mass for a in option_analyses),
                    default=0.0,
                ), 6,
            ),
            generated_at=now,
        )

    confidence = min(
        (a.probability_confidence for a in option_analyses), default=0.0
    )
    omitted = max(
        (a.tail_probability_mass for a in option_analyses), default=0.0
    )

    changed = False
    recommended = static_preferred
    reasons: list[str] = []

    if scenario_preferred != static_preferred and static_preferred in by_option:
        advantage = (
            by_option[scenario_preferred].scenario_score
            - by_option[static_preferred].scenario_score
        )
        if confidence < policy.minimum_scenario_probability_confidence:
            reasons.append(
                f"Scenario analysis favours {scenario_preferred} by "
                f"{advantage:.3f}, but probability confidence "
                f"{confidence:.2f} is below the "
                f"{policy.minimum_scenario_probability_confidence:.2f} "
                f"floor, so the static recommendation stands."
            )
        elif advantage < policy.minimum_option_advantage:
            reasons.append(
                f"Scenario advantage {advantage:.3f} does not clear the "
                f"{policy.minimum_option_advantage:.3f} margin required to "
                f"overturn the static recommendation."
            )
        else:
            changed = True
            recommended = scenario_preferred
            static_analysis = analyses.get(static_preferred)
            scenario_analysis = analyses.get(scenario_preferred)
            reasons.append(
                f"{scenario_preferred} scores {advantage:.3f} higher once "
                f"the modeled future distribution is considered."
            )
            if static_analysis and scenario_analysis:
                reasons.append(
                    f"Severe-failure probability falls from "
                    f"{static_analysis.severe_failure_probability:.2f} to "
                    f"{scenario_analysis.severe_failure_probability:.2f}; "
                    f"reversibility rises from "
                    f"{static_analysis.reversibility:.2f} to "
                    f"{scenario_analysis.reversibility:.2f}; recoverability "
                    f"from {static_analysis.recoverability:.2f} to "
                    f"{scenario_analysis.recoverability:.2f}."
                )
    else:
        reasons.append(
            f"Scenario analysis agrees with the static recommendation "
            f"({static_preferred})."
        )

    if omitted > 0:
        reasons.append(
            f"{omitted:.1%} of probability mass was pruned and is not "
            f"represented in these paths."
        )

    chosen_analysis = analyses.get(recommended)
    return ScenarioAwareDecisionRecommendation(
        recommendation_id=f"scenrec-{base_recommendation.id}",
        base_recommendation_id=base_recommendation.id,
        recommended_option_id=recommended,
        posture=policy.posture,
        status=base_recommendation.status,
        static_preferred_option_id=static_preferred,
        scenario_preferred_option_id=scenario_preferred,
        recommendation_changed_by_scenario_analysis=changed,
        option_scores=ranked,
        timing=timing,
        rationale=base_recommendation.rationale,
        scenario_rationale=" ".join(reasons),
        dominant_success_paths=(
            list(chosen_analysis.dominant_success_paths)
            if chosen_analysis else []
        ),
        dominant_failure_paths=(
            list(chosen_analysis.dominant_failure_paths)
            if chosen_analysis else []
        ),
        important_unknown_ids=(
            list(chosen_analysis.valuable_information_unknown_ids)
            if chosen_analysis else []
        ),
        scenario_probability_confidence=round(confidence, 6),
        omitted_probability_mass=round(omitted, 6),
        generated_at=now,
    )


def scenario_recommendation_is_autonomy_eligible(
    *,
    recommendation: ScenarioAwareDecisionRecommendation,
    policy: ScenarioDecisionPolicy,
) -> tuple[bool, str]:
    """Extra gate for bounded autonomy.

    When scenario analysis materially changed the choice, the underlying
    probability model must itself be trustworthy. A weak model must not
    drive an autonomous decision — and this can only ever *narrow*
    eligibility; authority scope remains the authority policy's to decide.
    """
    if recommendation.timing.recommended_action != (
        DecisionTimingAction.DECIDE_NOW
    ):
        return False, (
            f"Timing analysis recommends "
            f"{recommendation.timing.recommended_action.value} rather than "
            f"deciding now."
        )
    if recommendation.recommendation_changed_by_scenario_analysis:
        if recommendation.scenario_probability_confidence < (
            policy.minimum_scenario_probability_confidence
        ):
            return False, (
                f"Scenario analysis changed the recommendation, but its "
                f"probability confidence "
                f"{recommendation.scenario_probability_confidence:.2f} is "
                f"below the {policy.minimum_scenario_probability_confidence:.2f} "
                f"floor required to act on it autonomously."
            )
    return True, (
        "Scenario-aware recommendation is stable enough for autonomous "
        "consideration; the authority policy still decides authority."
    )
