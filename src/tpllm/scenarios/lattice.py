"""Lattice inspection helpers."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tpllm.scenarios.models import ScenarioLattice, ScenarioState
from tpllm.scenarios.probability import (
    enumerate_paths,
    omitted_probability_mass,
    total_displayed_probability,
)


class LatticeIntegrityReport(BaseModel):
    """Honest accounting of what the lattice does and does not cover."""
    state_count: int
    transition_count: int
    path_count: int
    convergent_state_ids: list[str] = Field(default_factory=list)
    displayed_probability: float
    omitted_probability: float
    residual_uncertainty: float
    method: str
    explanation: str


def find_convergent_states(lattice: ScenarioLattice) -> list[ScenarioState]:
    """States reachable by more than one route — the defining feature of
    a lattice as opposed to a tree."""
    return [
        s for s in lattice.states
        if len(s.parent_state_ids) > 1 or len(lattice.incoming(s.id)) > 1
    ]


def assess_lattice_integrity(
    lattice: ScenarioLattice,
) -> LatticeIntegrityReport:
    """Reports displayed versus omitted probability, so a reader is never
    misled into believing the modeled space was exhaustive."""
    paths = enumerate_paths(lattice=lattice)
    displayed = total_displayed_probability(paths)
    omitted = omitted_probability_mass(lattice)
    convergent = find_convergent_states(lattice)
    residual = max(0.0, 1.0 - displayed)

    return LatticeIntegrityReport(
        state_count=len(lattice.states),
        transition_count=len(lattice.transitions),
        path_count=len(paths),
        convergent_state_ids=sorted(s.id for s in convergent),
        displayed_probability=round(displayed, 6),
        omitted_probability=round(omitted, 6),
        residual_uncertainty=round(residual, 6),
        method=lattice.method.value,
        explanation=(
            f"Displayed paths carry {displayed:.1%} of modeled probability; "
            f"{omitted:.1%} was pruned into tail mass. This is the material "
            f"modeled scenario space, not every possible future."
        ),
    )
