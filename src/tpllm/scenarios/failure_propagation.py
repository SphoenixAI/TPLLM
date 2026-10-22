"""Interaction failure propagation.

Traces how a coordination failure travels through the organization:

    lost decision context
    -> wrong contract assumption
    -> integration failure
    -> KPI deterioration
    -> milestone risk

This is the "around, above, below" reasoning requirement — a
miscommunication upstream becomes a technical failure downstream, and
each hop becomes a scenario branch the lattice can evaluate.
"""

from __future__ import annotations

from tpllm.scenarios.interaction_models import (
    MiscommunicationRisk,
    MiscommunicationType,
)
from tpllm.scenarios.models import (
    ScenarioEventCandidate,
    ScenarioEventType,
)

# How a coordination failure typically cascades. Each hop names the
# event type it produces and how much of the upstream probability
# survives to the next hop.
_PROPAGATION_CHAINS: dict[
    MiscommunicationType, list[tuple[ScenarioEventType, str, float, float]]
] = {
    MiscommunicationType.STALE_DECISION_PROPAGATION: [
        (ScenarioEventType.MISCOMMUNICATION_OCCURS,
         "A team continues implementing against a superseded decision.",
         1.0, 0.4),
        (ScenarioEventType.ASSUMPTION_INVALIDATED,
         "The downstream contract assumption proves wrong.",
         0.75, 0.55),
        (ScenarioEventType.INTEGRATION_FAILS,
         "Integration fails against the mismatched contract.",
         0.65, 0.7),
        (ScenarioEventType.KPI_DETERIORATES,
         "Validation KPI deteriorates as integrations fail.",
         0.6, 0.6),
        (ScenarioEventType.MILESTONE_SLIPS,
         "The integration milestone slips.",
         0.55, 0.8),
    ],
    MiscommunicationType.CONFLICTING_TEAM_ASSUMPTIONS: [
        (ScenarioEventType.MISCOMMUNICATION_OCCURS,
         "Teams proceed on incompatible assumptions.",
         1.0, 0.4),
        (ScenarioEventType.INTEGRATION_FAILS,
         "Incompatible assumptions surface as an integration failure.",
         0.6, 0.7),
        (ScenarioEventType.MILESTONE_SLIPS,
         "Rework pushes the milestone.",
         0.5, 0.75),
    ],
    MiscommunicationType.AUTHORITY_CONFUSION: [
        (ScenarioEventType.DECISION_DELAYED,
         "The ask waits because no authority owns it.",
         1.0, 0.5),
        (ScenarioEventType.MILESTONE_SLIPS,
         "Dependent work waits behind the unresolved decision.",
         0.55, 0.7),
    ],
    MiscommunicationType.KPI_DEFINITION_MISMATCH: [
        (ScenarioEventType.MISCOMMUNICATION_OCCURS,
         "Teams compare incompatible KPI values.",
         1.0, 0.35),
        (ScenarioEventType.KPI_DETERIORATES,
         "A real regression is masked or a false alarm is raised.",
         0.5, 0.55),
    ],
    MiscommunicationType.LOST_CONTEXT: [
        (ScenarioEventType.DECISION_DELAYED,
         "The decision maker lacks the context to decide.",
         1.0, 0.45),
        (ScenarioEventType.MILESTONE_SLIPS,
         "The delayed decision pushes dependent work.",
         0.5, 0.7),
    ],
    MiscommunicationType.TERMINOLOGY_AMBIGUITY: [
        (ScenarioEventType.MISCOMMUNICATION_OCCURS,
         "An ambiguous term is read two ways.",
         1.0, 0.3),
        (ScenarioEventType.ASSUMPTION_INVALIDATED,
         "Work proceeds on the wrong reading.",
         0.45, 0.5),
    ],
}


def miscommunication_to_scenario_candidate(
    *,
    risk: MiscommunicationRisk,
    hop_index: int = 0,
) -> ScenarioEventCandidate:
    """Convert a miscommunication risk into the first branch it creates."""
    chain = _PROPAGATION_CHAINS.get(
        risk.risk_type,
        [(ScenarioEventType.MISCOMMUNICATION_OCCURS, risk.explanation,
          1.0, risk.impact)],
    )
    hop_index = max(0, min(hop_index, len(chain) - 1))
    event_type, description, survival, impact = chain[hop_index]

    return ScenarioEventCandidate(
        id=f"cand-{risk.id}-hop{hop_index}",
        event_type=event_type,
        description=description,
        prerequisite_state_conditions=[],
        resulting_state_changes={
            "assumptions": [f"Arises from {risk.risk_type.value}"],
        },
        factor_ids=[f"fac-{risk.id}"],
        controllable=True,
        intervention_ids=[f"prev-{risk.id}-0"],
        base_probability=max(0.01, min(0.99, risk.probability * survival)),
        impact=impact,
    )


def propagate_interaction_failure(
    *,
    initial_risk: MiscommunicationRisk,
    ontology_graph: object = None,
    scenario_context: object = None,
) -> list[ScenarioEventCandidate]:
    """Trace the full cascade from a coordination failure to downstream
    technical and schedule exposure.

    Each hop's probability decays from the one before, so a low-
    probability miscommunication does not produce a high-probability
    milestone slip.
    """
    chain = _PROPAGATION_CHAINS.get(initial_risk.risk_type)
    if not chain:
        return [
            miscommunication_to_scenario_candidate(risk=initial_risk),
        ]

    candidates: list[ScenarioEventCandidate] = []
    previous_state = "origin"
    for hop_index, (event_type, description, survival, impact) in enumerate(
        chain
    ):
        probability = max(
            0.01, min(0.99, initial_risk.probability * survival)
        )
        state_key = f"propagation_{initial_risk.id}"
        candidates.append(
            ScenarioEventCandidate(
                id=f"cand-{initial_risk.id}-hop{hop_index}",
                event_type=event_type,
                description=description,
                prerequisite_state_conditions=(
                    [] if hop_index == 0
                    else [f"{state_key}={previous_state}"]
                ),
                resulting_state_changes={
                    "dependency_states": {state_key: f"hop{hop_index}"},
                    "assumptions": [
                        f"Cascade from {initial_risk.risk_type.value} on "
                        f"'{initial_risk.topic}'"
                    ],
                },
                factor_ids=[f"fac-{initial_risk.id}"],
                controllable=hop_index == 0,
                intervention_ids=(
                    [f"prev-{initial_risk.id}-0"] if hop_index == 0 else []
                ),
                base_probability=probability,
                impact=impact,
                outcome_labels=(
                    ["launch_slips"]
                    if event_type == ScenarioEventType.MILESTONE_SLIPS
                    else []
                ),
            )
        )
        previous_state = f"hop{hop_index}"

    return candidates


def summarize_propagation(
    candidates: list[ScenarioEventCandidate],
) -> str:
    """Human-readable cascade, e.g. lost context -> wrong assumption ->
    integration failure -> KPI deterioration -> milestone risk."""
    if not candidates:
        return "No propagation modeled."
    return " -> ".join(c.event_type.value for c in candidates)
