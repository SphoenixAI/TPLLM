"""Interaction effects and preventive coordination actions.

Risks compound. A capacity-constrained approval team plus a lost-context
request plus unclear authority delays far more than any one factor
alone, so this module models explicit interaction effects rather than
assuming independence.

It also enforces the no-needless-meeting rule: when a context packet, a
decision capsule, or an already-existing answer would reduce the risk, a
meeting is never the recommendation.
"""

from __future__ import annotations

from tpllm.scenarios.interaction_models import (
    CoordinationPreventiveAction,
    InteractionEffect,
    MiscommunicationRisk,
    MiscommunicationType,
)

# Intervention costs, cheapest first. A meeting is deliberately the most
# expensive option so it is only reached when nothing else applies.
INTERVENTION_COSTS: dict[str, float] = {
    "existing_answer": 0.05,
    "context_packet": 0.15,
    "decision_capsule": 0.2,
    "async_context_brief": 0.25,
    "scoped_clarification": 0.3,
    "direct_routing": 0.3,
    "kpi_definition_reconciliation": 0.35,
    "glossary_entry": 0.2,
    "meeting": 0.9,
}

_CHEAPER_THAN_MEETING = {
    k for k, v in INTERVENTION_COSTS.items() if v < INTERVENTION_COSTS["meeting"]
}

# Which cheap intervention resolves which risk class.
_RISK_INTERVENTIONS: dict[MiscommunicationType, list[tuple[str, str, float]]] = {
    MiscommunicationType.STALE_DECISION_PROPAGATION: [
        ("decision_capsule",
         "Send the current decision capsule to entities lacking context.",
         0.6),
        ("context_packet",
         "Attach the latest conclusive decision to the workstream context.",
         0.4),
    ],
    MiscommunicationType.CONFLICTING_TEAM_ASSUMPTIONS: [
        ("scoped_clarification",
         "Ask one scoped question that resolves the divergent assumption.",
         0.55),
        ("context_packet",
         "Distribute a shared context packet stating the current position.",
         0.35),
    ],
    MiscommunicationType.LOST_CONTEXT: [
        ("context_packet",
         "Attach a context packet so the ask carries its own context.",
         0.5),
        ("direct_routing",
         "Route the ask directly to the decision authority.", 0.4),
    ],
    MiscommunicationType.AUTHORITY_CONFUSION: [
        ("direct_routing",
         "Resolve and route to the correct decision authority.", 0.6),
    ],
    MiscommunicationType.KPI_DEFINITION_MISMATCH: [
        ("kpi_definition_reconciliation",
         "Reconcile the KPI definition and units across teams.", 0.65),
    ],
    MiscommunicationType.TERMINOLOGY_AMBIGUITY: [
        ("glossary_entry",
         "Publish a canonical glossary entry for the ambiguous term.", 0.5),
    ],
}


def combine_interaction_effects(
    *,
    base_probability: float,
    effects: list[InteractionEffect],
) -> float:
    """Apply compounding multipliers. Effects are explicitly *not*
    independent — that is the point of modeling them."""
    probability = base_probability
    for effect in sorted(effects, key=lambda e: e.factor_ids):
        probability *= effect.multiplier
    return max(0.0, min(0.99, probability))


def build_compound_delay_effect(
    *,
    factor_ids: list[str],
    evidence_ids: list[str] | None = None,
    confidence: float = 0.5,
) -> InteractionEffect:
    """Compounding grows super-linearly with the number of simultaneous
    coordination hazards."""
    count = len(factor_ids)
    multiplier = 1.0 + 0.35 * max(0, count - 1)
    return InteractionEffect(
        factor_ids=sorted(factor_ids),
        effect_type="compound_coordination_delay",
        multiplier=multiplier,
        evidence_ids=list(evidence_ids or []),
        confidence=confidence,
    )


def recommend_preventive_actions(
    *,
    risk: MiscommunicationRisk,
    answer_already_exists: bool = False,
) -> list[CoordinationPreventiveAction]:
    """Cheapest sufficient intervention first.

    If the answer already exists, that is the recommendation — no ping,
    no meeting. A meeting is only proposed when no cheaper intervention
    addresses the risk class.
    """
    actions: list[CoordinationPreventiveAction] = []

    if answer_already_exists:
        return [
            CoordinationPreventiveAction(
                id=f"prev-{risk.id}-existing",
                risk_id=risk.id,
                action_type="existing_answer",
                description=(
                    "The answer already exists in evidence; return it with "
                    "its provenance instead of asking anyone."
                ),
                expected_probability_reduction=min(0.9, risk.probability),
                expected_cost=INTERVENTION_COSTS["existing_answer"],
                evidence_ids=list(risk.evidence_ids),
                requires_human_attention=False,
            )
        ]

    for index, (action_type, description, reduction) in enumerate(
        _RISK_INTERVENTIONS.get(risk.risk_type, [])
    ):
        actions.append(
            CoordinationPreventiveAction(
                id=f"prev-{risk.id}-{index}",
                risk_id=risk.id,
                action_type=action_type,
                description=description,
                expected_probability_reduction=reduction,
                expected_cost=INTERVENTION_COSTS[action_type],
                evidence_ids=list(risk.evidence_ids),
                requires_human_attention=action_type in (
                    "scoped_clarification", "direct_routing",
                ),
            )
        )

    if not actions:
        actions.append(
            CoordinationPreventiveAction(
                id=f"prev-{risk.id}-meeting",
                risk_id=risk.id,
                action_type="meeting",
                description=(
                    "No asynchronous intervention addresses this risk; a "
                    "synchronous discussion is warranted."
                ),
                expected_probability_reduction=0.5,
                expected_cost=INTERVENTION_COSTS["meeting"],
                evidence_ids=list(risk.evidence_ids),
                requires_human_attention=True,
            )
        )

    return sorted(
        actions,
        key=lambda a: (a.expected_cost or 0.0, a.id),
    )


def apply_preventive_action(
    *,
    risk: MiscommunicationRisk,
    action: CoordinationPreventiveAction,
) -> MiscommunicationRisk:
    """Return the risk as it would stand after the intervention.

    This models the expected reduction; it does not claim the action was
    taken or that it succeeded.
    """
    reduced = risk.probability * (1.0 - action.expected_probability_reduction)
    return risk.model_copy(
        update={
            "probability": max(0.0, min(1.0, reduced)),
            "explanation": (
                f"{risk.explanation} After '{action.action_type}', modeled "
                f"probability falls from {risk.probability:.2f} to "
                f"{reduced:.2f}."
            ),
        }
    )


def cheapest_action(
    actions: list[CoordinationPreventiveAction],
) -> CoordinationPreventiveAction | None:
    return actions[0] if actions else None


def recommends_meeting(
    actions: list[CoordinationPreventiveAction],
) -> bool:
    cheapest = cheapest_action(actions)
    return cheapest is not None and cheapest.action_type == "meeting"
