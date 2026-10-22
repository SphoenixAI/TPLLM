"""Optionality and recoverability.

Optionality asks how many viable futures an option leaves open. An
option that forecloses alternatives is more expensive than its immediate
score suggests, because the organization loses the ability to respond to
what it learns next.

Recoverability asks a narrower question: when this option leads somewhere
bad, can the organization climb back out?

Both are computed from the scenario lattice, so they are grounded in
modeled paths rather than asserted.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lltpm.scenarios.models import ScenarioEventType, ScenarioLattice
from lltpm.scenarios.probability import enumerate_paths

# Events that leave the organization with more room to manoeuvre.
_RECOVERY_EVENTS = {
    ScenarioEventType.WORKAROUND_ADOPTED,
    ScenarioEventType.RECOVERY_ACTION_SUCCEEDS,
    ScenarioEventType.SCOPE_REDUCES,
    ScenarioEventType.RESOURCE_CAPACITY_IMPROVES,
}

# Events that close doors behind them.
_IRREVERSIBLE_EVENTS = {
    ScenarioEventType.SCOPE_EXPANDS,
    ScenarioEventType.EXTERNAL_COMMITMENT_CHANGES,
    ScenarioEventType.DEPENDENCY_FAILED,
}

_FAILURE_LABELS = {"launch_slips"}


class OptionalityAssessment(BaseModel):
    option_id: str
    future_viable_option_count: int
    reversible_branch_count: int
    irreversible_commitment_count: int
    optionality_score: float
    explanation: str = ""


class RecoverabilityAssessment(BaseModel):
    option_id: str
    failure_path_count: int
    recoverable_failure_path_count: int
    mean_recovery_probability: float
    mean_recovery_cost: float | None = None
    recoverability_score: float
    dominant_recovery_paths: list[str] = Field(default_factory=list)
    explanation: str = ""


def assess_optionality(
    *,
    option_id: str,
    lattice: ScenarioLattice,
) -> OptionalityAssessment:
    """How much future choice this option preserves."""
    paths = enumerate_paths(lattice=lattice)

    viable = {
        tuple(sorted(p.outcome_labels))
        for p in paths
        if not (set(p.outcome_labels) & _FAILURE_LABELS)
        and p.outcome_labels
    }
    reversible = sum(
        1 for t in lattice.transitions
        if t.event_type in _RECOVERY_EVENTS or t.controllable
    )
    irreversible = sum(
        1 for t in lattice.transitions
        if t.event_type in _IRREVERSIBLE_EVENTS
    )

    total_branches = max(1, reversible + irreversible)
    reversible_share = reversible / total_branches
    viability = min(1.0, len(viable) / 3.0)
    score = max(0.0, min(1.0, 0.6 * reversible_share + 0.4 * viability))

    return OptionalityAssessment(
        option_id=option_id,
        future_viable_option_count=len(viable),
        reversible_branch_count=reversible,
        irreversible_commitment_count=irreversible,
        optionality_score=round(score, 6),
        explanation=(
            f"{len(viable)} distinct non-failing future(s) remain reachable; "
            f"{reversible} branch(es) stay reversible against {irreversible} "
            f"irreversible commitment(s)."
        ),
    )


def assess_recoverability(
    *,
    option_id: str,
    lattice: ScenarioLattice,
) -> RecoverabilityAssessment:
    """When this option goes wrong, how often is there a way back?"""
    paths = enumerate_paths(lattice=lattice)
    failure_paths = [
        p for p in paths if set(p.outcome_labels) & _FAILURE_LABELS
    ]

    recoverable: list[str] = []
    recovery_probabilities: list[float] = []
    for path in failure_paths:
        events = {
            lattice.transition(tid).event_type
            for tid in path.transition_ids
            if lattice.transition(tid) is not None
        }
        controllable = any(
            lattice.transition(tid).controllable
            for tid in path.transition_ids
            if lattice.transition(tid) is not None
        )
        if events & _RECOVERY_EVENTS or controllable:
            recoverable.append(path.id)
            recovery_probabilities.append(path.path_probability)

    # Recovery routes that reach a good outcome despite a setback.
    dominant = [
        p.id for p in paths
        if not (set(p.outcome_labels) & _FAILURE_LABELS)
        and any(
            lattice.transition(tid) is not None
            and lattice.transition(tid).event_type in _RECOVERY_EVENTS
            for tid in p.transition_ids
        )
    ][:5]

    mean_recovery = (
        sum(recovery_probabilities) / len(recovery_probabilities)
        if recovery_probabilities
        else 0.0
    )
    if failure_paths:
        share = len(recoverable) / len(failure_paths)
    else:
        # No modeled failure paths at all is a strong position, but it is
        # not proof of recoverability — report it as high, not certain.
        share = 0.9

    # Two distinct things matter: whether the failure paths themselves
    # carry a way out, and whether the option leaves any route that still
    # reaches a good outcome after a setback. An option with an available
    # recovery route is meaningfully recoverable even when a particular
    # failure path has no escape of its own.
    route_availability = 1.0 if dominant else 0.0
    score = max(0.0, min(1.0, 0.5 * share + 0.5 * route_availability))

    return RecoverabilityAssessment(
        option_id=option_id,
        failure_path_count=len(failure_paths),
        recoverable_failure_path_count=len(recoverable),
        mean_recovery_probability=round(mean_recovery, 6),
        recoverability_score=round(score, 6),
        dominant_recovery_paths=dominant,
        explanation=(
            f"{len(recoverable)} of {len(failure_paths)} modeled failure "
            f"path(s) retain a recovery route; {len(dominant)} route(s) "
            f"still reach a good outcome after a setback."
        ),
    )
