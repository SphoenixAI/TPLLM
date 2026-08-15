"""Miscommunication risk detection (Commit 25).

Detects structural coordination hazards: divergent team beliefs, stale
decision propagation, authority ambiguity, KPI definition mismatch,
terminology ambiguity, and context lost across handoffs.

Every measure here is about *structure* — how many hands a request
passed through, whether two teams hold incompatible claims, whether a
decision is present in downstream state. None of it measures a person.
"""

from __future__ import annotations

from datetime import datetime

from lltpm.scenarios.interaction_models import (
    DecisionPropagationAssessment,
    MiscommunicationRisk,
    MiscommunicationType,
    OrganizationalInteractionEdge,
    TeamBeliefState,
)

# Handoff depth beyond which context loss becomes materially likely.
LOST_CONTEXT_DEPTH_THRESHOLD = 2
_DEPTH_RISK_PER_HOP = 0.18
_MAX_DEPTH_RISK = 0.85


def calculate_context_handoff_depth(
    *,
    edge: OrganizationalInteractionEdge | None = None,
    relay_chain: list[str] | None = None,
) -> int:
    """How many hands a request passes through before the decision maker.

    original request -> forwarded by A -> summarized by B -> relayed by C
    -> decision maker is depth 3. This measures the *structure* of the
    handoff, not the people in it.
    """
    if relay_chain is not None:
        return max(0, len(relay_chain) - 1)
    return edge.handoff_depth if edge is not None else 0


def calculate_belief_divergence(states: list[TeamBeliefState]) -> float:
    """How far apart teams are on one topic, in [0, 1].

    Zero when every team holds the same claims and assumes the same
    decision; rises as assumed decisions and claim sets diverge.
    """
    relevant = [s for s in states if s is not None]
    if len(relevant) < 2:
        return 0.0

    assumed = {s.assumed_decision_id for s in relevant}
    # Distinct assumed decisions, normalized by the maximum possible.
    decision_divergence = (len(assumed) - 1) / max(1, len(relevant) - 1)

    claim_sets = [frozenset(s.active_claim_ids) for s in relevant]
    union = set().union(*claim_sets) if claim_sets else set()
    intersection = set(claim_sets[0]).intersection(*claim_sets[1:])
    claim_divergence = (
        1.0 - (len(intersection) / len(union)) if union else 0.0
    )

    return max(0.0, min(1.0, 0.6 * decision_divergence + 0.4 * claim_divergence))


def assess_decision_propagation(
    *,
    decision_id: str,
    expected_recipients: list[str],
    downstream_state_decisions: dict[str, str | None],
    superseded_decision_ids: list[str] | None = None,
) -> DecisionPropagationAssessment:
    """Coverage is measured from downstream *state*, never from delivery.

    ``downstream_state_decisions`` maps an entity to the decision its
    current state actually reflects. An entity absent from that map has
    unknown context — it is not assumed to have received anything.
    """
    superseded = set(superseded_decision_ids or [])
    confirmed: list[str] = []
    stale: list[str] = []
    unknown: list[str] = []

    for entity in sorted(expected_recipients):
        if entity not in downstream_state_decisions:
            unknown.append(entity)
            continue
        reflected = downstream_state_decisions[entity]
        if reflected == decision_id:
            confirmed.append(entity)
        elif reflected is None:
            unknown.append(entity)
        else:
            stale.append(entity)

    total = len(expected_recipients)
    coverage = len(confirmed) / total if total else 0.0

    return DecisionPropagationAssessment(
        decision_id=decision_id,
        expected_recipient_entity_ids=sorted(expected_recipients),
        confirmed_context_entity_ids=confirmed,
        stale_context_entity_ids=stale,
        unknown_context_entity_ids=unknown,
        propagation_coverage=round(coverage, 4),
        explanation=(
            f"{len(confirmed)}/{total} recipient(s) show the current "
            f"decision in their downstream state; {len(stale)} reflect a "
            f"superseded decision and {len(unknown)} have no evidence of "
            f"current context. Delivery is not treated as comprehension."
        ),
    )


def _risk(
    risk_id: str,
    risk_type: MiscommunicationType,
    *,
    entities: list[str],
    topic: str,
    probability: float,
    confidence: float,
    impact: float,
    conditions: list[str],
    evidence_ids: list[str],
    mitigations: list[str],
    explanation: str,
) -> MiscommunicationRisk:
    return MiscommunicationRisk(
        id=risk_id,
        risk_type=risk_type,
        affected_entity_ids=sorted(entities),
        topic=topic,
        probability=max(0.0, min(1.0, probability)),
        probability_confidence=max(0.0, min(1.0, confidence)),
        impact=max(0.0, min(1.0, impact)),
        triggering_conditions=conditions,
        evidence_ids=evidence_ids,
        mitigation_options=mitigations,
        explanation=explanation,
    )


def detect_belief_divergence_risk(
    *,
    states: list[TeamBeliefState],
    topic: str,
    impact: float = 0.6,
) -> MiscommunicationRisk | None:
    divergence = calculate_belief_divergence(states)
    if divergence <= 0.0:
        return None
    teams = sorted({s.team_id for s in states})
    beliefs = "; ".join(
        f"{s.team_id} assumes {s.assumed_decision_id or 'no decision'}"
        for s in sorted(states, key=lambda s: s.team_id)
    )
    return _risk(
        f"risk-divergence-{topic}",
        MiscommunicationType.CONFLICTING_TEAM_ASSUMPTIONS,
        entities=teams,
        topic=topic,
        probability=divergence,
        confidence=0.7,
        impact=impact,
        conditions=[
            f"{len(teams)} teams hold different assumptions about {topic}",
        ],
        evidence_ids=sorted({c for s in states for c in s.active_claim_ids}),
        mitigations=[
            "Attach the latest conclusive decision to each team's context",
            "Issue a scoped clarification to the diverging teams",
        ],
        explanation=(
            f"Belief divergence on '{topic}' is {divergence:.2f}. {beliefs}."
        ),
    )


def detect_stale_propagation_risk(
    *,
    assessment: DecisionPropagationAssessment,
    topic: str,
    impact: float = 0.65,
) -> MiscommunicationRisk | None:
    at_risk = (
        assessment.stale_context_entity_ids
        + assessment.unknown_context_entity_ids
    )
    if not at_risk:
        return None
    probability = 1.0 - assessment.propagation_coverage
    return _risk(
        f"risk-stale-{assessment.decision_id}",
        MiscommunicationType.STALE_DECISION_PROPAGATION,
        entities=at_risk,
        topic=topic,
        probability=probability,
        confidence=0.65,
        impact=impact,
        conditions=[
            f"decision {assessment.decision_id} is absent from downstream "
            f"state for {len(at_risk)} entity(ies)",
        ],
        evidence_ids=[assessment.decision_id],
        mitigations=[
            "Send the decision capsule to entities lacking current context",
            "Attach the latest conclusive decision to the workstream state",
        ],
        explanation=(
            f"Propagation coverage for {assessment.decision_id} is "
            f"{assessment.propagation_coverage:.0%}; "
            f"{', '.join(at_risk)} may still be acting on older guidance."
        ),
    )


def detect_lost_context_risk(
    *,
    edge: OrganizationalInteractionEdge,
    topic: str,
    impact: float = 0.5,
) -> MiscommunicationRisk | None:
    """Handoff depth — not message volume — drives context-loss risk."""
    depth = calculate_context_handoff_depth(edge=edge)
    if depth < LOST_CONTEXT_DEPTH_THRESHOLD:
        return None
    probability = min(_MAX_DEPTH_RISK, depth * _DEPTH_RISK_PER_HOP)
    return _risk(
        f"risk-lostcontext-{edge.id}",
        MiscommunicationType.LOST_CONTEXT,
        entities=[edge.source_entity_id, edge.target_entity_id],
        topic=topic,
        probability=probability,
        confidence=0.55,
        impact=impact,
        conditions=[f"request passes through {depth} handoff(s)"],
        evidence_ids=list(edge.evidence_ids),
        mitigations=[
            "Attach a context packet so the ask carries its own context",
            "Route directly to the decision authority",
        ],
        explanation=(
            f"The ask traverses {depth} handoff(s) before reaching a "
            f"decision maker, so original context is likely to thin."
        ),
    )


def detect_authority_confusion_risk(
    *,
    topic: str,
    candidate_authority_ids: list[str],
    resolved_authority_id: str | None,
    evidence_ids: list[str] | None = None,
    impact: float = 0.7,
) -> MiscommunicationRisk | None:
    if resolved_authority_id is not None:
        return None
    if len(candidate_authority_ids) == 1:
        return None
    probability = 0.5 if not candidate_authority_ids else min(
        0.85, 0.35 + 0.15 * len(candidate_authority_ids)
    )
    return _risk(
        f"risk-authority-{topic}",
        MiscommunicationType.AUTHORITY_CONFUSION,
        entities=list(candidate_authority_ids),
        topic=topic,
        probability=probability,
        confidence=0.6,
        impact=impact,
        conditions=[
            f"{len(candidate_authority_ids)} candidate authorities and no "
            f"resolved decision authority",
        ],
        evidence_ids=list(evidence_ids or []),
        mitigations=[
            "Resolve the decision authority before routing the ask",
            "Route directly to the system owner with approval rights",
        ],
        explanation=(
            f"No decision authority is resolved for '{topic}' while "
            f"{len(candidate_authority_ids)} candidates exist; the ask may "
            f"reach someone without authority to answer it."
        ),
    )


def detect_kpi_definition_mismatch(
    *,
    kpi_name: str,
    definitions_by_team: dict[str, dict],
    impact: float = 0.55,
) -> MiscommunicationRisk | None:
    """Two teams reporting 'activation' in different units are not
    discussing the same number."""
    units = {
        team: str(spec.get("unit", "")).strip().lower()
        for team, spec in definitions_by_team.items()
    }
    distinct = {u for u in units.values() if u}
    if len(distinct) <= 1:
        return None
    detail = "; ".join(
        f"{team} measures in {unit or 'unspecified'}"
        for team, unit in sorted(units.items())
    )
    return _risk(
        f"risk-kpi-{kpi_name}",
        MiscommunicationType.KPI_DEFINITION_MISMATCH,
        entities=sorted(definitions_by_team),
        topic=kpi_name,
        probability=min(0.9, 0.45 + 0.2 * (len(distinct) - 1)),
        confidence=0.75,
        impact=impact,
        conditions=[f"{len(distinct)} incompatible units for {kpi_name}"],
        evidence_ids=[],
        mitigations=[
            "Reconcile the KPI definition before comparing values",
        ],
        explanation=(
            f"'{kpi_name}' is defined incompatibly across teams: {detail}."
        ),
    )


def detect_terminology_ambiguity(
    *,
    term: str,
    meanings_by_entity: dict[str, str],
    impact: float = 0.45,
) -> MiscommunicationRisk | None:
    distinct = {m.strip().lower() for m in meanings_by_entity.values() if m}
    if len(distinct) <= 1:
        return None
    return _risk(
        f"risk-term-{term}",
        MiscommunicationType.TERMINOLOGY_AMBIGUITY,
        entities=sorted(meanings_by_entity),
        topic=term,
        probability=min(0.85, 0.4 + 0.2 * (len(distinct) - 1)),
        confidence=0.6,
        impact=impact,
        conditions=[f"'{term}' carries {len(distinct)} distinct meanings"],
        evidence_ids=[],
        mitigations=[
            "Publish a canonical glossary entry for the term",
            "Disambiguate the term in the next context packet",
        ],
        explanation=(
            f"'{term}' means different things to "
            f"{', '.join(sorted(meanings_by_entity))}."
        ),
    )
