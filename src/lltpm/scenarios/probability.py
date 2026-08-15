"""Transparent scenario probability (Commit 24).

Two rules dominate this module:

1. Correlated evidence is not independent evidence. Factors sharing an
   ``independent_group`` are collapsed to their strongest member — three
   messages about one integration failure move the estimate once.
2. Conditional transitions replace naive multiplication. P(integration
   fails | contract unresolved) is modeled directly rather than
   multiplying two marginals that are not independent.

Probability and probability-confidence are computed separately and never
merged.
"""

from __future__ import annotations

import math

from lltpm.scenarios.models import (
    ScenarioLattice,
    ScenarioPath,
    ScenarioProbabilityFactor,
    ScenarioTransition,
)

_MIN_P = 0.01
_MAX_P = 0.99


def clamp_probability(value: float) -> float:
    """Never 0.0 or 1.0 — the future is not certain in either direction."""
    return max(_MIN_P, min(_MAX_P, value))


def deduplicate_correlated_factors(
    factors: list[ScenarioProbabilityFactor],
) -> list[ScenarioProbabilityFactor]:
    """Collapse each independent_group to its single strongest factor.

    Factors with no group are treated as genuinely independent.
    """
    grouped: dict[str, ScenarioProbabilityFactor] = {}
    ungrouped: list[ScenarioProbabilityFactor] = []
    for factor in factors:
        if factor.independent_group is None:
            ungrouped.append(factor)
            continue
        current = grouped.get(factor.independent_group)
        weight = factor.magnitude * factor.confidence
        if current is None or weight > (
            current.magnitude * current.confidence
        ):
            grouped[factor.independent_group] = factor
    result = ungrouped + [grouped[k] for k in sorted(grouped)]
    return sorted(result, key=lambda f: f.id)


def combine_factors(
    *,
    base_probability: float,
    factors: list[ScenarioProbabilityFactor],
) -> tuple[float, float]:
    """Return (probability, probability_confidence).

    Factors adjust the base in log-odds space so repeated nudges saturate
    rather than exceeding 1.0. Confidence reflects how much independent,
    confident evidence supports the estimate — it is *not* the
    probability.
    """
    independent = deduplicate_correlated_factors(factors)

    base = clamp_probability(base_probability)
    logit = math.log(base / (1.0 - base))
    for factor in independent:
        weight = factor.magnitude * factor.confidence
        logit += weight if factor.direction == "increases" else -weight
    probability = clamp_probability(1.0 / (1.0 + math.exp(-logit)))

    if not independent:
        # No supporting factors: the base rate alone is a weak estimate.
        return probability, 0.25

    mean_confidence = sum(f.confidence for f in independent) / len(independent)
    # More independent signals -> a firmer estimate, saturating.
    breadth = 1.0 - math.exp(-len(independent) / 3.0)
    confidence = max(0.05, min(0.95, mean_confidence * (0.5 + 0.5 * breadth)))
    return probability, confidence


def normalize_sibling_transitions(
    transitions: list[ScenarioTransition],
) -> list[ScenarioTransition]:
    """Scale mutually exclusive sibling transitions to sum to 1.0.

    Applied only to a complete sibling set; pruning happens afterward so
    the removed mass is recorded rather than hidden.
    """
    total = sum(t.conditional_probability for t in transitions)
    if total <= 0:
        return transitions
    return [
        t.model_copy(
            update={
                "conditional_probability": clamp_probability(
                    t.conditional_probability / total
                )
            }
        )
        for t in transitions
    ]


def calculate_path_probability(
    *,
    lattice: ScenarioLattice,
    transition_ids: list[str],
) -> tuple[float, float]:
    """Chain conditional transition probabilities along one path.

    Each transition's probability is already conditioned on its source
    state, so chaining is correct — this is not multiplying independent
    marginals. Confidence is the weakest link, since a chain is no more
    trustworthy than its least certain step.
    """
    probability = 1.0
    confidence = 1.0
    for transition_id in transition_ids:
        transition = lattice.transition(transition_id)
        if transition is None:
            continue
        probability *= transition.conditional_probability
        confidence = min(confidence, transition.probability_confidence)
    if not transition_ids:
        return 1.0, 1.0
    return probability, confidence


def enumerate_paths(
    *,
    lattice: ScenarioLattice,
    max_paths: int = 500,
) -> list[ScenarioPath]:
    """All root-to-leaf paths through the lattice, deterministically
    ordered by descending probability."""
    paths: list[ScenarioPath] = []

    def walk(
        state_id: str,
        state_ids: list[str],
        transition_ids: list[str],
        seen: set[str],
    ) -> None:
        if len(paths) >= max_paths:
            return
        outgoing = sorted(lattice.outgoing(state_id), key=lambda t: t.id)
        if not outgoing:
            probability, confidence = calculate_path_probability(
                lattice=lattice, transition_ids=transition_ids,
            )
            terminal = lattice.state(state_id)
            paths.append(
                ScenarioPath(
                    id=f"path-{len(paths)}-{state_id}",
                    state_ids=list(state_ids),
                    transition_ids=list(transition_ids),
                    path_probability=probability,
                    probability_confidence=confidence,
                    outcome_labels=(
                        list(terminal.outcome_labels) if terminal else []
                    ),
                    outcome_summary=(
                        ", ".join(terminal.outcome_labels)
                        if terminal and terminal.outcome_labels
                        else "no labeled outcome"
                    ),
                )
            )
            return
        for transition in outgoing:
            if transition.to_state_id in seen:
                continue  # guard against cycles
            walk(
                transition.to_state_id,
                state_ids + [transition.to_state_id],
                transition_ids + [transition.id],
                seen | {transition.to_state_id},
            )

    walk(
        lattice.root_state_id,
        [lattice.root_state_id],
        [],
        {lattice.root_state_id},
    )
    return sorted(
        paths, key=lambda p: (-p.path_probability, p.id),
    )


def calculate_outcome_probability(
    *,
    lattice: ScenarioLattice,
    outcome_predicate,
) -> tuple[float, float]:
    """Aggregate probability of an outcome across *all* paths reaching it.

    Convergent paths are summed over distinct paths, not over states, so
    a state reachable by three routes is not triple-counted.
    """
    paths = enumerate_paths(lattice=lattice)
    matching = [p for p in paths if outcome_predicate(p, lattice)]
    if not matching:
        return 0.0, 0.0
    total = sum(p.path_probability for p in matching)
    # Confidence: probability-weighted mean of the matching paths.
    weighted = sum(
        p.path_probability * p.probability_confidence for p in matching
    )
    confidence = weighted / total if total > 0 else 0.0
    return min(1.0, total), confidence


def probability_of_outcome(
    *,
    lattice: ScenarioLattice,
    outcome_label: str,
) -> tuple[float, float]:
    """Probability that any path ends in a state carrying this label."""
    return calculate_outcome_probability(
        lattice=lattice,
        outcome_predicate=lambda path, _: outcome_label in path.outcome_labels,
    )


def total_displayed_probability(paths: list[ScenarioPath]) -> float:
    return sum(p.path_probability for p in paths)


def omitted_probability_mass(lattice: ScenarioLattice) -> float:
    """How much probability mass was pruned away — always disclosed."""
    return sum(t.omitted_probability_mass for t in lattice.tail_masses)
