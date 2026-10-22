"""Seeded Monte Carlo simulation.

Simulation *supplements* the interpretable lattice; it never replaces
it. The explicit graph remains the auditable artifact — a simulation
that could not be traced back to named transitions would be opaque.

Runs are reproducible from a seed.
"""

from __future__ import annotations

import random

from pydantic import BaseModel, Field

from tpllm.scenarios.models import ScenarioLattice


class ScenarioSimulationReport(BaseModel):
    runs: int
    seed: int
    outcome_frequencies: dict[str, float] = Field(default_factory=dict)
    terminal_state_frequencies: dict[str, float] = Field(default_factory=dict)
    mean_path_length: float = 0.0
    method: str = ""
    explanation: str = ""


def simulate_scenario_lattice(
    *,
    lattice: ScenarioLattice,
    runs: int = 1000,
    seed: int = 12345,
) -> ScenarioSimulationReport:
    """Walk the lattice ``runs`` times, sampling transitions by their
    conditional probabilities. Deterministic for a given seed."""
    if runs <= 0:
        raise ValueError("runs must be positive.")

    rng = random.Random(seed)
    outcome_counts: dict[str, int] = {}
    terminal_counts: dict[str, int] = {}
    total_length = 0

    for _ in range(runs):
        current = lattice.root_state_id
        length = 0
        visited = {current}
        while True:
            outgoing = sorted(lattice.outgoing(current), key=lambda t: t.id)
            outgoing = [
                t for t in outgoing if t.to_state_id not in visited
            ]
            if not outgoing:
                break
            weights = [t.conditional_probability for t in outgoing]
            total = sum(weights)
            if total <= 0:
                break
            draw = rng.random() * total
            cumulative = 0.0
            chosen = outgoing[-1]
            for transition, weight in zip(outgoing, weights):
                cumulative += weight
                if draw <= cumulative:
                    chosen = transition
                    break
            current = chosen.to_state_id
            visited.add(current)
            length += 1

        terminal_counts[current] = terminal_counts.get(current, 0) + 1
        total_length += length
        state = lattice.state(current)
        if state is not None:
            for label in state.outcome_labels:
                outcome_counts[label] = outcome_counts.get(label, 0) + 1

    return ScenarioSimulationReport(
        runs=runs,
        seed=seed,
        outcome_frequencies={
            label: round(count / runs, 6)
            for label, count in sorted(outcome_counts.items())
        },
        terminal_state_frequencies={
            state_id: round(count / runs, 6)
            for state_id, count in sorted(terminal_counts.items())
        },
        mean_path_length=round(total_length / runs, 4),
        method=lattice.method.value,
        explanation=(
            f"{runs} seeded runs (seed={seed}) over the explicit lattice. "
            f"Simulation supplements the interpretable graph; it does not "
            f"replace it."
        ),
    )
