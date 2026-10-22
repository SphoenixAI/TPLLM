"""Scenario queries — the six reasoning directions.

Forward, backward, upstream, downstream, lateral, and recovery. A
forecast that explored only the happy path and the direct failure path
may not claim comprehensive coverage, and ``assess_scenario_coverage``
enforces that.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from lltpm.scenarios.models import (
    InformationValueEstimate,
    ScenarioCoverageAssessment,
    ScenarioDirection,
    ScenarioEventType,
    ScenarioHorizon,
    ScenarioLattice,
    ScenarioPath,
    ScenarioRiskSummary,
    ScenarioSensitivity,
)
from lltpm.scenarios.probability import (
    calculate_outcome_probability,
    enumerate_paths,
    omitted_probability_mass,
    probability_of_outcome,
)

# Event types that represent an organization recovering from a setback.
_RECOVERY_EVENTS = {
    ScenarioEventType.WORKAROUND_ADOPTED,
    ScenarioEventType.RECOVERY_ACTION_SUCCEEDS,
    ScenarioEventType.SCOPE_REDUCES,
    ScenarioEventType.RESOURCE_CAPACITY_IMPROVES,
}

# Event types originating outside the workstream's own control.
_UPSTREAM_EVENTS = {
    ScenarioEventType.PRIORITY_CHANGE,
    ScenarioEventType.EXTERNAL_COMMITMENT_CHANGES,
    ScenarioEventType.SCOPE_EXPANDS,
    ScenarioEventType.DECISION_REOPENED,
}

# Event types describing a neighbouring team or system interaction.
_LATERAL_EVENTS = {
    ScenarioEventType.MISCOMMUNICATION_OCCURS,
    ScenarioEventType.COMMUNICATION_SUCCEEDS,
    ScenarioEventType.ASSUMPTION_INVALIDATED,
    ScenarioEventType.ASSUMPTION_VALIDATED,
}

# Event types that expose downstream systems.
_DOWNSTREAM_EVENTS = {
    ScenarioEventType.KPI_DETERIORATES,
    ScenarioEventType.KPI_IMPROVES,
    ScenarioEventType.MILESTONE_SLIPS,
    ScenarioEventType.MILESTONE_MET,
    ScenarioEventType.INTEGRATION_FAILS,
    ScenarioEventType.INTEGRATION_PASSES,
}


class ScenarioQuestionType(str, Enum):
    MOST_LIKELY_NEXT = "most_likely_next"
    PATHS_TO_OUTCOME = "paths_to_outcome"
    FAILURE_PATHS = "failure_paths"
    SUCCESS_PATHS = "success_paths"
    UPSTREAM_CAUSES = "upstream_causes"
    DOWNSTREAM_IMPACT = "downstream_impact"
    WHAT_IF = "what_if"
    SENSITIVITY = "sensitivity"
    VALUE_OF_INFORMATION = "value_of_information"
    RECOVERY_OPTIONS = "recovery_options"
    LOW_PROBABILITY_HIGH_IMPACT = "low_probability_high_impact"
    MISCOMMUNICATION_RISK = "miscommunication_risk"


class ScenarioQuestion(BaseModel):
    question_type: ScenarioQuestionType
    project_id: str
    focal_entity_ids: list[str] = Field(default_factory=list)
    target_outcome: str | None = None
    hypothetical_conditions: list[str] = Field(default_factory=list)
    horizon: ScenarioHorizon | None = None


class ScenarioAnswer(BaseModel):
    """An auditable scenario rationale — never private chain-of-thought."""
    summary: str
    probability: float | None = None
    probability_confidence: float | None = None
    relevant_path_ids: list[str] = Field(default_factory=list)
    supporting_factor_ids: list[str] = Field(default_factory=list)
    counterfactors: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    intervention_points: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    omitted_probability_mass: float = 0.0


# ── Directional queries ──────────────────────────────────────────────

def find_most_likely_next(
    *, lattice: ScenarioLattice, from_state_id: str | None = None,
) -> list:
    """FORWARD: if we are here, what happens next?"""
    state_id = from_state_id or lattice.root_state_id
    return sorted(
        lattice.outgoing(state_id),
        key=lambda t: (-t.conditional_probability, t.id),
    )


def find_paths_to_outcome(
    *, lattice: ScenarioLattice, target_outcome: str,
) -> list[ScenarioPath]:
    """BACKWARD: what would have to happen for this outcome to occur?"""
    return [
        p for p in enumerate_paths(lattice=lattice)
        if target_outcome in p.outcome_labels
    ]


def find_alternate_success_paths(
    *, lattice: ScenarioLattice, target_outcome: str,
) -> list[ScenarioPath]:
    """RECOVERY: paths reaching the goal that pass through a setback.

    These are the routes worth surfacing when the primary plan fails —
    a compatibility layer, a scope cut, an alternate owner.
    """
    paths = find_paths_to_outcome(
        lattice=lattice, target_outcome=target_outcome,
    )
    alternates: list[ScenarioPath] = []
    for path in paths:
        events = {
            lattice.transition(tid).event_type
            for tid in path.transition_ids
            if lattice.transition(tid) is not None
        }
        if events & _RECOVERY_EVENTS:
            recovery = sorted(
                e.value for e in events & _RECOVERY_EVENTS
            )
            alternates.append(
                path.model_copy(update={"recovery_opportunities": recovery})
            )
    return alternates


def find_upstream_precursors(
    *, lattice: ScenarioLattice, outcome: str,
) -> list[dict]:
    """UPSTREAM: what tends to have to happen first for this to become
    likely? Ranked by how much of the outcome's mass flows through them."""
    paths = find_paths_to_outcome(lattice=lattice, target_outcome=outcome)
    total = sum(p.path_probability for p in paths) or 1.0
    contribution: dict[str, float] = {}
    details: dict[str, dict] = {}

    for path in paths:
        for transition_id in path.transition_ids:
            transition = lattice.transition(transition_id)
            if transition is None:
                continue
            key = transition.event_description
            contribution[key] = contribution.get(key, 0.0) + (
                path.path_probability
            )
            details.setdefault(key, {
                "event_type": transition.event_type.value,
                "description": key,
                "is_upstream_class": (
                    transition.event_type in _UPSTREAM_EVENTS
                ),
                "controllable": transition.controllable,
            })

    ranked = []
    for key in sorted(contribution, key=lambda k: (-contribution[k], k)):
        entry = dict(details[key])
        entry["share_of_outcome"] = round(contribution[key] / total, 4)
        ranked.append(entry)
    return ranked


def find_downstream_exposure(
    *, lattice: ScenarioLattice, event_or_state_id: str,
) -> list[dict]:
    """DOWNSTREAM: if this occurs, what else becomes exposed?"""
    start_states: set[str] = set()
    transition = lattice.transition(event_or_state_id)
    if transition is not None:
        start_states.add(transition.to_state_id)
    if lattice.state(event_or_state_id) is not None:
        start_states.add(event_or_state_id)
    if not start_states:
        return []

    exposed: dict[str, dict] = {}
    frontier = list(start_states)
    seen = set(start_states)
    while frontier:
        current = frontier.pop()
        for outgoing in lattice.outgoing(current):
            entry = exposed.setdefault(outgoing.id, {
                "transition_id": outgoing.id,
                "event_type": outgoing.event_type.value,
                "description": outgoing.event_description,
                "conditional_probability": outgoing.conditional_probability,
                "impact": outgoing.impact,
                "is_downstream_class": (
                    outgoing.event_type in _DOWNSTREAM_EVENTS
                ),
            })
            successor = lattice.state(outgoing.to_state_id)
            if successor is not None:
                entry["exposed_outcomes"] = list(successor.outcome_labels)
                entry["exposed_kpis"] = sorted(successor.kpi_states)
            if outgoing.to_state_id not in seen:
                seen.add(outgoing.to_state_id)
                frontier.append(outgoing.to_state_id)

    return [exposed[k] for k in sorted(exposed)]


def find_lateral_interactions(*, lattice: ScenarioLattice) -> list[dict]:
    """LATERAL: neighbouring team/system interactions that change a path."""
    return [
        {
            "transition_id": t.id,
            "event_type": t.event_type.value,
            "description": t.event_description,
            "conditional_probability": t.conditional_probability,
        }
        for t in sorted(lattice.transitions, key=lambda t: t.id)
        if t.event_type in _LATERAL_EVENTS
    ]


# ── Risk ranking ─────────────────────────────────────────────────────

def summarize_path_risk(
    *,
    lattice: ScenarioLattice,
    path: ScenarioPath,
) -> ScenarioRiskSummary:
    """Probability and impact stay separate, so a rare catastrophe ranks
    above a likely inconvenience."""
    impacts = [
        lattice.transition(tid).impact
        for tid in path.transition_ids
        if lattice.transition(tid) is not None
    ]
    impact = max(impacts) if impacts else 0.0

    recovery_available = any(
        lattice.transition(tid).event_type in _RECOVERY_EVENTS
        for tid in path.transition_ids
        if lattice.transition(tid) is not None
    )
    controllable = [
        tid for tid in path.transition_ids
        if lattice.transition(tid) is not None
        and lattice.transition(tid).controllable
    ]

    recoverability = 0.8 if recovery_available else 0.3
    detectability = min(1.0, 0.3 + 0.1 * len(path.transition_ids))
    time_to_intervene = float(len(path.transition_ids))
    expected_loss = path.path_probability * impact
    # Priority raises hard-to-recover, hard-to-detect exposure.
    priority = expected_loss * (2.0 - recoverability) * (
        2.0 - detectability
    )

    return ScenarioRiskSummary(
        path_id=path.id,
        probability=path.path_probability,
        impact=impact,
        expected_loss=expected_loss,
        recoverability=recoverability,
        detectability=detectability,
        time_to_intervene=time_to_intervene,
        priority=priority,
    )


def rank_paths_by_quadrant(
    *, lattice: ScenarioLattice, quadrant: str,
) -> list[ScenarioPath]:
    """Views: most_likely, most_damaging, most_preventable, most_uncertain,
    best_recovery."""
    paths = enumerate_paths(lattice=lattice)
    summaries = {
        p.id: summarize_path_risk(lattice=lattice, path=p) for p in paths
    }

    if quadrant == "most_likely":
        return sorted(paths, key=lambda p: (-p.path_probability, p.id))
    if quadrant == "most_damaging":
        return sorted(
            paths, key=lambda p: (-summaries[p.id].impact, p.id),
        )
    if quadrant == "most_preventable":
        def controllable_count(path: ScenarioPath) -> int:
            return sum(
                1 for tid in path.transition_ids
                if lattice.transition(tid) is not None
                and lattice.transition(tid).controllable
            )
        return sorted(
            paths,
            key=lambda p: (-controllable_count(p), -p.path_probability, p.id),
        )
    if quadrant == "most_uncertain":
        return sorted(
            paths, key=lambda p: (p.probability_confidence, p.id),
        )
    if quadrant == "best_recovery":
        return sorted(
            paths,
            key=lambda p: (-summaries[p.id].recoverability,
                           -p.path_probability, p.id),
        )
    raise ValueError(f"Unknown scenario quadrant '{quadrant}'.")


def find_low_probability_high_impact_paths(
    *,
    lattice: ScenarioLattice,
    probability_ceiling: float = 0.15,
    impact_floor: float = 0.6,
) -> list[ScenarioRiskSummary]:
    """The 4% catastrophe that matters more than the 60% inconvenience."""
    summaries = [
        summarize_path_risk(lattice=lattice, path=p)
        for p in enumerate_paths(lattice=lattice)
    ]
    return sorted(
        (
            s for s in summaries
            if s.probability <= probability_ceiling
            and s.impact >= impact_floor
        ),
        key=lambda s: (-s.priority, s.path_id),
    )


# ── Sensitivity and value of information ─────────────────────────────

def calculate_scenario_sensitivity(
    *,
    lattice: ScenarioLattice,
    outcome_label: str,
    perturbation: float = 0.2,
) -> list[ScenarioSensitivity]:
    """Which factors move the forecast most? Perturb each factor's
    transitions and re-measure the outcome."""
    baseline, _ = probability_of_outcome(
        lattice=lattice, outcome_label=outcome_label,
    )
    results: list[ScenarioSensitivity] = []

    for factor in lattice.factors:
        affected = [
            t for t in lattice.transitions if factor.id in t.factor_ids
        ]
        if not affected:
            continue
        perturbed_transitions = []
        for transition in lattice.transitions:
            if factor.id in transition.factor_ids:
                new_p = max(
                    0.01,
                    min(0.99, transition.conditional_probability *
                        (1.0 - perturbation)),
                )
                perturbed_transitions.append(
                    transition.model_copy(
                        update={"conditional_probability": new_p}
                    )
                )
            else:
                perturbed_transitions.append(transition)
        perturbed_lattice = lattice.model_copy(
            update={"transitions": perturbed_transitions}
        )
        perturbed, _ = probability_of_outcome(
            lattice=perturbed_lattice, outcome_label=outcome_label,
        )
        results.append(
            ScenarioSensitivity(
                factor_id=factor.id,
                baseline_outcome_probability=baseline,
                perturbed_outcome_probability=perturbed,
                absolute_delta=abs(baseline - perturbed),
                explanation=(
                    f"Reducing '{factor.description}' by "
                    f"{perturbation:.0%} moves P({outcome_label}) from "
                    f"{baseline:.2f} to {perturbed:.2f}."
                ),
            )
        )
    return sorted(
        results, key=lambda s: (-s.absolute_delta, s.factor_id),
    )


def estimate_value_of_information(
    *,
    lattice: ScenarioLattice,
    outcome_label: str,
) -> list[InformationValueEstimate]:
    """Which unresolved question would most reduce uncertainty?

    Feeds self-research: the highest-priority unknown becomes
    the next research target.
    """
    root = lattice.state(lattice.root_state_id)
    unknowns = list(root.unresolved_unknowns) if root else []
    if not unknowns:
        return []

    estimates: list[InformationValueEstimate] = []
    for unknown_id in unknowns:
        related_factors = [
            f for f in lattice.factors if unknown_id in f.source_ids
        ]
        affected_transitions = [
            t for t in lattice.transitions
            if any(f.id in t.factor_ids for f in related_factors)
        ]
        if affected_transitions:
            mass = sum(
                t.conditional_probability for t in affected_transitions
            ) / max(1, len(affected_transitions))
            mean_conf = sum(
                t.probability_confidence for t in affected_transitions
            ) / len(affected_transitions)
        else:
            mass = 0.0
            mean_conf = 1.0
        uncertainty = 1.0 - mean_conf
        reduction = uncertainty * mass
        estimates.append(
            InformationValueEstimate(
                unknown_id=unknown_id,
                current_uncertainty=round(uncertainty, 4),
                expected_probability_reduction=round(reduction, 4),
                affected_outcomes=[outcome_label] if affected_transitions
                else [],
                priority=round(reduction * (1.0 + len(affected_transitions)), 4),
                explanation=(
                    f"Resolving {unknown_id} would sharpen "
                    f"{len(affected_transitions)} transition(s) currently "
                    f"estimated at {mean_conf:.2f} confidence."
                ),
            )
        )
    return sorted(
        estimates, key=lambda e: (-e.priority, e.unknown_id),
    )


# ── Coverage ─────────────────────────────────────────────────────────

def assess_scenario_coverage(
    *, lattice: ScenarioLattice,
) -> ScenarioCoverageAssessment:
    """A forecast that explored only the happy path and the direct
    failure path cannot claim comprehensive coverage."""
    event_types = {t.event_type for t in lattice.transitions}
    paths = enumerate_paths(lattice=lattice)

    forward = min(1.0, len(lattice.transitions) / 4.0)
    backward = min(1.0, len(paths) / 4.0)
    upstream = 1.0 if event_types & _UPSTREAM_EVENTS else 0.0
    downstream = 1.0 if event_types & _DOWNSTREAM_EVENTS else 0.0
    lateral = 1.0 if event_types & _LATERAL_EVENTS else 0.0
    recovery = 1.0 if event_types & _RECOVERY_EVENTS else 0.0

    missing: list[str] = []
    if upstream == 0.0:
        missing.append(ScenarioDirection.UPSTREAM.value)
    if downstream == 0.0:
        missing.append(ScenarioDirection.DOWNSTREAM.value)
    if lateral == 0.0:
        missing.append(ScenarioDirection.LATERAL.value)
    if recovery == 0.0:
        missing.append(ScenarioDirection.RECOVERY.value)
    if backward < 0.5:
        missing.append(ScenarioDirection.BACKWARD.value)
    if forward < 0.5:
        missing.append(ScenarioDirection.FORWARD.value)

    values = [forward, backward, upstream, downstream, lateral, recovery]
    overall = sum(values) / len(values)

    return ScenarioCoverageAssessment(
        forward_coverage=forward,
        backward_coverage=backward,
        upstream_coverage=upstream,
        downstream_coverage=downstream,
        lateral_coverage=lateral,
        recovery_coverage=recovery,
        overall_coverage=overall,
        missing_scenario_classes=missing,
        explanation=(
            f"Explored {len(paths)} path(s) across {len(event_types)} event "
            f"type(s)."
            + (
                f" Unexplored directions: {', '.join(missing)}."
                if missing
                else " All six reasoning directions are represented."
            )
        ),
    )


def answer_scenario_question(
    *,
    question: ScenarioQuestion,
    lattice: ScenarioLattice,
) -> ScenarioAnswer:
    """Route a question to its analysis and return an auditable answer.

    The residual pruned mass is always disclosed, so displayed paths
    never imply the modeled space was exhaustive.
    """
    omitted = omitted_probability_mass(lattice)
    root = lattice.state(lattice.root_state_id)
    unknowns = list(root.unresolved_unknowns) if root else []
    qtype = question.question_type

    if qtype == ScenarioQuestionType.MOST_LIKELY_NEXT:
        transitions = find_most_likely_next(lattice=lattice)
        if not transitions:
            return ScenarioAnswer(
                summary="No modeled transitions from the current state.",
                omitted_probability_mass=omitted,
            )
        top = transitions[0]
        return ScenarioAnswer(
            summary=(
                f"Most likely next: {top.event_description} "
                f"(P={top.conditional_probability:.2f}, confidence "
                f"{top.probability_confidence:.2f})."
            ),
            probability=top.conditional_probability,
            probability_confidence=top.probability_confidence,
            supporting_factor_ids=list(top.factor_ids),
            intervention_points=list(top.possible_intervention_ids),
            unknowns=unknowns,
            omitted_probability_mass=omitted,
        )

    if qtype in (
        ScenarioQuestionType.PATHS_TO_OUTCOME,
        ScenarioQuestionType.SUCCESS_PATHS,
        ScenarioQuestionType.FAILURE_PATHS,
    ):
        target = question.target_outcome or ""
        probability, confidence = calculate_outcome_probability(
            lattice=lattice,
            outcome_predicate=lambda p, _: target in p.outcome_labels,
        )
        paths = find_paths_to_outcome(lattice=lattice, target_outcome=target)
        return ScenarioAnswer(
            summary=(
                f"{len(paths)} modeled path(s) reach '{target}' with "
                f"aggregate probability {probability:.2f} (confidence "
                f"{confidence:.2f})."
            ),
            probability=probability,
            probability_confidence=confidence,
            relevant_path_ids=[p.id for p in paths],
            unknowns=unknowns,
            omitted_probability_mass=omitted,
        )

    if qtype == ScenarioQuestionType.RECOVERY_OPTIONS:
        target = question.target_outcome or ""
        alternates = find_alternate_success_paths(
            lattice=lattice, target_outcome=target,
        )
        return ScenarioAnswer(
            summary=(
                f"{len(alternates)} recovery route(s) still reach "
                f"'{target}' after a setback."
            ),
            relevant_path_ids=[p.id for p in alternates],
            intervention_points=sorted({
                i for p in alternates for i in p.recovery_opportunities
            }),
            unknowns=unknowns,
            omitted_probability_mass=omitted,
        )

    if qtype == ScenarioQuestionType.UPSTREAM_CAUSES:
        precursors = find_upstream_precursors(
            lattice=lattice, outcome=question.target_outcome or "",
        )
        return ScenarioAnswer(
            summary=(
                f"{len(precursors)} precursor(s) precede "
                f"'{question.target_outcome}'."
            ),
            counterfactors=[p["description"] for p in precursors[:5]],
            unknowns=unknowns,
            omitted_probability_mass=omitted,
        )

    if qtype == ScenarioQuestionType.DOWNSTREAM_IMPACT:
        focus = (
            question.focal_entity_ids[0]
            if question.focal_entity_ids
            else lattice.root_state_id
        )
        exposure = find_downstream_exposure(
            lattice=lattice, event_or_state_id=focus,
        )
        return ScenarioAnswer(
            summary=f"{len(exposure)} downstream exposure(s) identified.",
            counterfactors=[e["description"] for e in exposure[:5]],
            unknowns=unknowns,
            omitted_probability_mass=omitted,
        )

    if qtype == ScenarioQuestionType.SENSITIVITY:
        sensitivities = calculate_scenario_sensitivity(
            lattice=lattice, outcome_label=question.target_outcome or "",
        )
        return ScenarioAnswer(
            summary=(
                "Forecast is most sensitive to: "
                + "; ".join(s.explanation for s in sensitivities[:3])
                if sensitivities
                else "No factors influence this outcome in the model."
            ),
            supporting_factor_ids=[s.factor_id for s in sensitivities[:5]],
            unknowns=unknowns,
            omitted_probability_mass=omitted,
        )

    if qtype == ScenarioQuestionType.VALUE_OF_INFORMATION:
        estimates = estimate_value_of_information(
            lattice=lattice, outcome_label=question.target_outcome or "",
        )
        return ScenarioAnswer(
            summary=(
                f"Most valuable unresolved question: "
                f"{estimates[0].unknown_id} — {estimates[0].explanation}"
                if estimates
                else "No unresolved unknowns are modeled."
            ),
            unknowns=[e.unknown_id for e in estimates],
            omitted_probability_mass=omitted,
        )

    if qtype == ScenarioQuestionType.LOW_PROBABILITY_HIGH_IMPACT:
        tail = find_low_probability_high_impact_paths(lattice=lattice)
        return ScenarioAnswer(
            summary=(
                f"{len(tail)} low-probability high-impact path(s) preserved."
            ),
            relevant_path_ids=[s.path_id for s in tail],
            unknowns=unknowns,
            omitted_probability_mass=omitted,
        )

    return ScenarioAnswer(
        summary=f"Question type '{qtype.value}' has no modeled analysis yet.",
        unknowns=unknowns,
        omitted_probability_mass=omitted,
    )
