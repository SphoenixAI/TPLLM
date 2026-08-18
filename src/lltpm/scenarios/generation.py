"""Scenario lattice generation (Commit 24).

Beam expansion with probability pruning, impact preservation, and tail
aggregation. "Cover every path" means covering the *material* scenario
space — not enumerating millions of meaningless combinations.

Two rules protect honesty during pruning:
- A pruned branch's probability mass is recorded in ScenarioTailMass, so
  displayed branches never falsely appear to sum to 1.0.
- A high-impact branch is preserved even when its probability falls below
  the prune threshold: a 3% catastrophe must not vanish because a 60%
  inconvenience outranked it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from lltpm.scenarios.models import (
    ScenarioEventCandidate,
    ScenarioHorizon,
    ScenarioLattice,
    ScenarioProbabilityFactor,
    ScenarioProbabilityMethod,
    ScenarioState,
    ScenarioTailMass,
    ScenarioTransition,
    build_state_signature,
)
from lltpm.scenarios.probability import (
    clamp_probability,
    combine_factors,
    normalize_sibling_transitions,
)
from lltpm.temporal.models import ProjectTemporalState

# A branch this impactful survives pruning regardless of probability.
HIGH_IMPACT_PRESERVATION_THRESHOLD = 0.7


class ScenarioCandidateGenerator(Protocol):
    def generate_events(
        self,
        *,
        temporal_state: ProjectTemporalState,
        horizon: ScenarioHorizon,
        context: object,
    ) -> list[ScenarioEventCandidate]:
        ...


def build_root_state(
    *,
    temporal_state: ProjectTemporalState,
    horizon: ScenarioHorizon,
) -> ScenarioState:
    """The lattice root is the reconstructed historical state — this is
    what prevents the forecast from seeing the future."""
    parts = {
        "project": temporal_state.project_id,
        "as_of": temporal_state.as_of.isoformat(),
        "blockers": temporal_state.active_blocker_ids,
        "decisions": temporal_state.active_decision_ids,
        "unknowns": temporal_state.unresolved_unknown_ids,
        # The reconstructed state's own fingerprint is part of the root's
        # identity: two counterfactual roots representing different chosen
        # options are different roots even where their surface content
        # coincides.
        "fingerprint": temporal_state.state_fingerprint,
    }
    return ScenarioState(
        id=f"state-root-{temporal_state.state_fingerprint}",
        project_id=temporal_state.project_id,
        horizon_step=0,
        simulated_at=horizon.start_at,
        parent_state_ids=[],
        decision_states={
            d: "authoritative" for d in temporal_state.active_decision_ids
        },
        active_blockers=list(temporal_state.active_blocker_ids),
        unresolved_unknowns=list(temporal_state.unresolved_unknown_ids),
        evidence_root_ids=list(temporal_state.active_claim_ids),
        state_signature=build_state_signature(parts),
    )


def _apply_changes(
    state: ScenarioState,
    candidate: ScenarioEventCandidate,
    *,
    step: int,
    simulated_at,
) -> ScenarioState:
    """Deterministically derive the successor state. Identical successor
    content yields an identical signature, which is what lets distinct
    paths converge onto one lattice node."""
    changes = candidate.resulting_state_changes
    workstream_health = dict(state.workstream_health)
    workstream_health.update(changes.get("workstream_health", {}))
    kpi_states = dict(state.kpi_states)
    kpi_states.update(changes.get("kpi_states", {}))
    dependency_states = dict(state.dependency_states)
    dependency_states.update(changes.get("dependency_states", {}))
    decision_states = dict(state.decision_states)
    decision_states.update(changes.get("decision_states", {}))

    blockers = list(state.active_blockers)
    for opened in changes.get("blockers_opened", []):
        if opened not in blockers:
            blockers.append(opened)
    for resolved in changes.get("blockers_resolved", []):
        if resolved in blockers:
            blockers.remove(resolved)

    unknowns = list(state.unresolved_unknowns)
    for resolved in changes.get("unknowns_resolved", []):
        if resolved in unknowns:
            unknowns.remove(resolved)

    assumptions = list(state.assumptions)
    for assumption in changes.get("assumptions", []):
        if assumption not in assumptions:
            assumptions.append(assumption)

    labels = list(state.outcome_labels)
    for label in candidate.outcome_labels:
        if label not in labels:
            labels.append(label)

    signature = build_state_signature({
        "project": state.project_id,
        "step": step,
        "workstream_health": workstream_health,
        "kpi_states": kpi_states,
        "dependency_states": dependency_states,
        "decision_states": decision_states,
        "blockers": blockers,
        "unknowns": unknowns,
        "labels": labels,
    })

    return ScenarioState(
        id=f"state-{signature}",
        project_id=state.project_id,
        horizon_step=step,
        simulated_at=simulated_at,
        parent_state_ids=[state.id],
        workstream_health=workstream_health,
        kpi_states=kpi_states,
        dependency_states=dependency_states,
        decision_states=decision_states,
        active_blockers=sorted(blockers),
        unresolved_unknowns=sorted(unknowns),
        assumptions=assumptions,
        evidence_root_ids=list(state.evidence_root_ids),
        outcome_labels=labels,
        state_signature=signature,
    )


def _conditions_met(
    candidate: ScenarioEventCandidate, state: ScenarioState
) -> bool:
    """Prerequisites are expressed as 'key=value' over the state's maps —
    this is what makes transitions *conditional* rather than marginal."""
    for condition in candidate.prerequisite_state_conditions:
        if "=" not in condition:
            continue
        key, expected = condition.split("=", 1)
        key, expected = key.strip(), expected.strip()
        merged = {
            **state.dependency_states,
            **state.decision_states,
            **state.kpi_states,
            **state.workstream_health,
        }
        if key == "blocker_open":
            if expected not in state.active_blockers:
                return False
        elif key == "blocker_absent":
            if expected in state.active_blockers:
                return False
        elif merged.get(key) != expected:
            return False
    return True


def generate_scenario_lattice(
    *,
    temporal_state: ProjectTemporalState,
    generator: ScenarioCandidateGenerator,
    horizon: ScenarioHorizon,
    context: object = None,
    method: ScenarioProbabilityMethod = (
        ScenarioProbabilityMethod.HEURISTIC_V1
    ),
    extra_factors: list[ScenarioProbabilityFactor] | None = None,
) -> ScenarioLattice:
    """Expand the material scenario space by beam search."""
    root = build_root_state(temporal_state=temporal_state, horizon=horizon)
    states: dict[str, ScenarioState] = {root.id: root}
    transitions: list[ScenarioTransition] = []
    tail_masses: list[ScenarioTailMass] = []
    all_factors: dict[str, ScenarioProbabilityFactor] = {
        f.id: f for f in (extra_factors or [])
    }

    candidates = generator.generate_events(
        temporal_state=temporal_state, horizon=horizon, context=context,
    )

    frontier = [root.id]
    for step in range(1, horizon.maximum_depth + 1):
        if not frontier:
            break
        simulated_at = horizon.start_at + timedelta(
            days=horizon.step_days * step
        )
        next_frontier: list[str] = []

        for state_id in frontier:
            state = states[state_id]
            # A state carrying an outcome label is terminal: the scenario
            # reached a modeled conclusion. Expanding past it would let one
            # path accumulate contradictory outcomes and would double-count
            # probability across outcomes.
            if state.outcome_labels:
                continue
            applicable = [c for c in candidates if _conditions_met(c, state)]
            if not applicable:
                continue

            # Build sibling transitions, then normalize as a set.
            drafts: list[tuple[ScenarioEventCandidate, float, float]] = []
            for candidate in applicable:
                factors = [
                    all_factors[fid]
                    for fid in candidate.factor_ids
                    if fid in all_factors
                ]
                probability, confidence = combine_factors(
                    base_probability=candidate.base_probability,
                    factors=factors,
                )
                drafts.append((candidate, probability, confidence))

            total = sum(p for _, p, _ in drafts)
            if total <= 0:
                continue
            normalized = [
                (candidate, clamp_probability(p / total), c)
                for candidate, p, c in drafts
            ]

            kept: list[tuple[ScenarioEventCandidate, float, float]] = []
            pruned_mass = 0.0
            pruned_count = 0
            for candidate, probability, confidence in normalized:
                keep = (
                    probability >= horizon.minimum_branch_probability
                    or candidate.impact >= HIGH_IMPACT_PRESERVATION_THRESHOLD
                )
                if keep:
                    kept.append((candidate, probability, confidence))
                else:
                    pruned_mass += probability
                    pruned_count += 1

            if pruned_count:
                tail_masses.append(
                    ScenarioTailMass(
                        parent_state_id=state.id,
                        omitted_probability_mass=round(pruned_mass, 6),
                        omitted_branch_count=pruned_count,
                        explanation=(
                            f"{pruned_count} branch(es) below the "
                            f"{horizon.minimum_branch_probability:.0%} "
                            f"threshold were omitted; their combined "
                            f"probability was {pruned_mass:.1%}."
                        ),
                    )
                )

            for candidate, probability, confidence in sorted(
                kept, key=lambda item: item[0].id
            ):
                successor = _apply_changes(
                    state, candidate, step=step, simulated_at=simulated_at,
                )
                existing = states.get(successor.id)
                if existing is None:
                    states[successor.id] = successor
                    if len(states) <= horizon.maximum_active_states:
                        next_frontier.append(successor.id)
                else:
                    # Convergence: another path reached the same state.
                    if state.id not in existing.parent_state_ids:
                        states[successor.id] = existing.model_copy(
                            update={
                                "parent_state_ids": [
                                    *existing.parent_state_ids, state.id,
                                ]
                            }
                        )

                transitions.append(
                    ScenarioTransition(
                        id=f"tr-{state.id}-{candidate.id}",
                        from_state_id=state.id,
                        to_state_id=successor.id,
                        event_type=candidate.event_type,
                        event_description=candidate.description,
                        conditional_probability=probability,
                        probability_confidence=confidence,
                        factor_ids=list(candidate.factor_ids),
                        assumptions=list(
                            candidate.resulting_state_changes.get(
                                "assumptions", []
                            )
                        ),
                        controllable=candidate.controllable,
                        possible_intervention_ids=list(
                            candidate.intervention_ids
                        ),
                        impact=candidate.impact,
                    )
                )

        frontier = sorted(set(next_frontier))

    return ScenarioLattice(
        # The root fingerprint is part of the identity: two lattices built
        # for the same project at the same moment from different
        # counterfactual roots are different lattices and must not collide.
        id=f"lattice-{temporal_state.project_id}-"
           f"{temporal_state.as_of.isoformat()}-{root.state_signature}",
        project_id=temporal_state.project_id,
        root_state_id=root.id,
        states=sorted(states.values(), key=lambda s: (s.horizon_step, s.id)),
        transitions=sorted(transitions, key=lambda t: t.id),
        tail_masses=tail_masses,
        factors=sorted(all_factors.values(), key=lambda f: f.id),
        method=method,
        generated_at=horizon.start_at,
        horizon=horizon,
        evidence_cutoff_at=temporal_state.evidence_cutoff_at,
    )
