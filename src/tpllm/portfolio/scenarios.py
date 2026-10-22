"""Cross-project scenario states and lattice.

The scenario lattice asks "what futures does *this* project have?". This module
asks the portfolio question: what futures does the organization have,
given that several projects draw on the same constrained resources?

The construction reuses the lattice's invariants rather than restating
them loosely:

- a state carrying an outcome label is terminal and never expands
- pruned branches keep their mass in ``ScenarioTailMass`` so displayed
  futures never falsely sum to 100%
- distinct branches that produce the same portfolio situation converge
  on one state with several parents, and that state's probability is the
  sum of the incoming paths — counted once each, not once per parent

A shared uncertainty is applied to every project it touches from a
single draw. Sampling "Platform is saturated" separately for A, B, and C
would invent diversification the organization does not have.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from tpllm.portfolio.models import (
    OrganizationalCapacityWindow,
    PortfolioCapacityDemand,
)
from tpllm.portfolio.prioritization import displacement_cost
from tpllm.portfolio.shared_factors import (
    SharedScenarioFactor,
    deduplicate_shared_factors,
    factors_affecting_project,
)
from tpllm.scenarios.models import (
    ScenarioHorizon,
    ScenarioProbabilityMethod,
    ScenarioTailMass,
    build_state_signature,
)


class ProjectScenarioStatus(str, Enum):
    """Coarse per-project situation inside a portfolio future.

    These describe the *project's* delivery situation, never anybody's
    performance.
    """
    IN_PROGRESS = "in_progress"
    ON_TRACK = "on_track"
    DELIVERED_VIA_WORKAROUND = "delivered_via_workaround"
    DELAYED = "delayed"
    DELAYED_BY_DISPLACEMENT = "delayed_by_displacement"


_RESOLVED_STATUSES = {
    ProjectScenarioStatus.ON_TRACK,
    ProjectScenarioStatus.DELIVERED_VIA_WORKAROUND,
    ProjectScenarioStatus.DELAYED,
    ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT,
}

# A fired shared factor degrades whatever the allocation had arranged.
_DEGRADATION = {
    ProjectScenarioStatus.ON_TRACK: ProjectScenarioStatus.DELAYED,
    ProjectScenarioStatus.DELIVERED_VIA_WORKAROUND: (
        ProjectScenarioStatus.DELAYED
    ),
    ProjectScenarioStatus.IN_PROGRESS: ProjectScenarioStatus.DELAYED,
    # Already waiting on a resource it cannot reach — a further shared
    # failure does not make it more delayed than delayed.
    ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT: (
        ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT
    ),
    ProjectScenarioStatus.DELAYED: ProjectScenarioStatus.DELAYED,
}


class PortfolioAllocationDecision(BaseModel):
    """Which competing demands a shared resource serves in a window.

    ``displaced_demand_ids`` is not an afterthought: every downstream
    artifact in this package is required to report what those projects
    pay.
    """
    id: str
    resource_id: str
    served_demand_ids: list[str] = Field(default_factory=list)
    displaced_demand_ids: list[str] = Field(default_factory=list)
    routed_demand_ids: dict[str, str] = Field(default_factory=dict)
    rationale: str = ""

    @model_validator(mode="after")
    def _check(self) -> "PortfolioAllocationDecision":
        overlap = set(self.served_demand_ids) & set(self.displaced_demand_ids)
        if overlap:
            raise ValueError(
                f"A demand cannot be both served and displaced: "
                f"{sorted(overlap)}."
            )
        unknown = set(self.routed_demand_ids) - set(self.displaced_demand_ids)
        if unknown:
            raise ValueError(
                f"Only displaced demands take an alternative route: "
                f"{sorted(unknown)}."
            )
        return self


class PortfolioScenarioState(BaseModel):
    """One possible situation for the whole portfolio.

    Multiple parents are allowed — several different combinations of
    shared failures can leave the organization in the same place.
    """
    id: str
    horizon_step: int
    simulated_at: datetime
    parent_state_ids: list[str] = Field(default_factory=list)
    project_statuses: dict[str, ProjectScenarioStatus] = Field(
        default_factory=dict
    )
    resolved_factor_ids: list[str] = Field(default_factory=list)
    fired_factor_ids: list[str] = Field(default_factory=list)
    allocation_id: str | None = None
    outcome_labels: list[str] = Field(default_factory=list)
    evidence_root_ids: list[str] = Field(default_factory=list)
    state_signature: str = ""

    @property
    def is_terminal(self) -> bool:
        return bool(self.outcome_labels)


class PortfolioScenarioTransition(BaseModel):
    id: str
    from_state_id: str
    to_state_id: str
    event_description: str
    shared_factor_id: str | None = None
    fired: bool = False
    affected_project_ids: list[str] = Field(default_factory=list)
    conditional_probability: float
    probability_confidence: float
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "PortfolioScenarioTransition":
        for name in ("conditional_probability", "probability_confidence"):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(
                    f"{name} must be within [0, 1] — probability and the "
                    f"confidence in that probability are separate."
                )
        return self


class PortfolioOutcomeSummary(BaseModel):
    """What one project's futures look like under a given allocation."""
    project_id: str
    outcome_probabilities: dict[str, float] = Field(default_factory=dict)
    expected_impact: float = 0.0
    probability_confidence: float = 0.0
    was_displaced: bool = False
    displacement_impact: float = 0.0
    alternative_routes: list[str] = Field(default_factory=list)
    explanation: str = ""


class PortfolioScenarioLattice(BaseModel):
    id: str
    root_state_id: str
    allocation_id: str | None = None
    project_ids: list[str] = Field(default_factory=list)
    states: list[PortfolioScenarioState] = Field(default_factory=list)
    transitions: list[PortfolioScenarioTransition] = Field(
        default_factory=list
    )
    tail_masses: list[ScenarioTailMass] = Field(default_factory=list)
    factors: list[SharedScenarioFactor] = Field(default_factory=list)
    method: ScenarioProbabilityMethod = ScenarioProbabilityMethod.HEURISTIC_V1
    generated_at: datetime
    horizon: ScenarioHorizon

    def state(self, state_id: str) -> PortfolioScenarioState | None:
        return next((s for s in self.states if s.id == state_id), None)

    def outgoing(self, state_id: str) -> list[PortfolioScenarioTransition]:
        return [t for t in self.transitions if t.from_state_id == state_id]

    def incoming(self, state_id: str) -> list[PortfolioScenarioTransition]:
        return [t for t in self.transitions if t.to_state_id == state_id]

    @property
    def terminal_states(self) -> list[PortfolioScenarioState]:
        return [s for s in self.states if s.is_terminal]


# ── Allocation → baseline project situation ──────────────────────────

def baseline_project_statuses(
    *,
    demands: list[PortfolioCapacityDemand],
    allocation: PortfolioAllocationDecision,
) -> dict[str, ProjectScenarioStatus]:
    """Where the allocation leaves each project before any uncertainty.

    A displaced project with somewhere else to go is not simply blocked;
    a displaced project with nowhere to go is.
    """
    served = set(allocation.served_demand_ids)
    displaced = set(allocation.displaced_demand_ids)
    statuses: dict[str, ProjectScenarioStatus] = {}

    for demand in sorted(demands, key=lambda d: d.id):
        if demand.id in served:
            status = ProjectScenarioStatus.ON_TRACK
        elif demand.id in displaced:
            has_route = bool(
                demand.alternative_resource_ids
                or demand.has_workaround
                or allocation.routed_demand_ids.get(demand.id)
            )
            status = (
                ProjectScenarioStatus.DELIVERED_VIA_WORKAROUND if has_route
                else ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT
            )
        else:
            status = ProjectScenarioStatus.IN_PROGRESS
        # The worst situation a project lands in across its demands wins.
        current = statuses.get(demand.project_id)
        statuses[demand.project_id] = _worse(current, status)
    return statuses


_SEVERITY_ORDER = [
    ProjectScenarioStatus.ON_TRACK,
    ProjectScenarioStatus.IN_PROGRESS,
    ProjectScenarioStatus.DELIVERED_VIA_WORKAROUND,
    ProjectScenarioStatus.DELAYED,
    ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT,
]


def _worse(
    a: ProjectScenarioStatus | None, b: ProjectScenarioStatus,
) -> ProjectScenarioStatus:
    if a is None:
        return b
    return max(a, b, key=_SEVERITY_ORDER.index)


def apply_factor_outcomes(
    *,
    baseline: dict[str, ProjectScenarioStatus],
    factors: list[SharedScenarioFactor],
    outcomes: dict[str, bool],
) -> dict[str, ProjectScenarioStatus]:
    """Degrade every project a fired factor touches — from one draw.

    ``outcomes`` holds a single value per distinct uncertainty; this
    function fans that one value out to every affected project rather
    than re-rolling per project.
    """
    statuses = dict(baseline)
    for factor in sorted(factors, key=lambda f: f.id):
        if not outcomes.get(factor.id, False):
            continue
        for project_id in factor.affected_project_ids:
            if project_id not in statuses:
                continue
            statuses[project_id] = _DEGRADATION[statuses[project_id]]
    return statuses


def _outcome_labels(
    statuses: dict[str, ProjectScenarioStatus],
) -> list[str]:
    labels = [
        f"{project_id}:{statuses[project_id].value}"
        for project_id in sorted(statuses)
    ]
    delayed = sum(
        1 for s in statuses.values()
        if s in (
            ProjectScenarioStatus.DELAYED,
            ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT,
        )
    )
    if delayed == 0:
        labels.append("portfolio_all_projects_progressing")
    elif delayed == 1:
        labels.append("portfolio_single_project_delayed")
    else:
        labels.append("portfolio_multiple_projects_delayed")
    return sorted(labels)


class PortfolioSimulationState(BaseModel):
    """The cross-project inputs a portfolio future is built from."""
    id: str
    as_of: datetime
    project_ids: list[str] = Field(default_factory=list)
    demands: list[PortfolioCapacityDemand] = Field(default_factory=list)
    capacity_windows: list[OrganizationalCapacityWindow] = Field(
        default_factory=list
    )
    shared_factors: list[SharedScenarioFactor] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    def demand(self, demand_id: str) -> PortfolioCapacityDemand | None:
        return next((d for d in self.demands if d.id == demand_id), None)

    def demands_for_project(
        self, project_id: str,
    ) -> list[PortfolioCapacityDemand]:
        return [d for d in self.demands if d.project_id == project_id]

    @property
    def known_project_ids(self) -> list[str]:
        return sorted(
            set(self.project_ids) | {d.project_id for d in self.demands}
        )


def project_status_value(
    *,
    status: ProjectScenarioStatus,
    demands: list[PortfolioCapacityDemand],
) -> float:
    """Portfolio value of leaving a project in this situation.

    Positive when the project moves, negative when it waits. Waiting
    costs less when there is another route — which is exactly why a
    project with no route is a poor candidate for displacement.
    """
    if not demands:
        return 0.0
    criticality = max(d.criticality for d in demands)
    cost = max(displacement_cost(d) for d in demands)
    if status == ProjectScenarioStatus.ON_TRACK:
        return round(criticality, 6)
    if status == ProjectScenarioStatus.DELIVERED_VIA_WORKAROUND:
        return round(-cost, 6)
    if status == ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT:
        return round(-cost, 6)
    if status == ProjectScenarioStatus.DELAYED:
        return round(-criticality, 6)
    return 0.0


# ── Lattice construction ─────────────────────────────────────────────

def build_portfolio_lattice(
    *,
    state: PortfolioSimulationState,
    allocation: PortfolioAllocationDecision,
    horizon: ScenarioHorizon,
    generated_at: datetime | None = None,
) -> PortfolioScenarioLattice:
    """Expand the shared uncertainties into an explicit portfolio lattice.

    Each step resolves one shared factor for the whole portfolio at once.
    Branches below ``horizon.minimum_branch_probability`` are pruned and
    their mass recorded, and states describing the same portfolio
    situation are merged so convergent paths stay one state.
    """
    generated_at = generated_at or state.as_of
    factors = deduplicate_shared_factors(state.shared_factors)
    depth = max(0, min(horizon.maximum_depth, len(factors)))
    factors = factors[:depth]
    if depth < len(deduplicate_shared_factors(state.shared_factors)):
        dropped = deduplicate_shared_factors(state.shared_factors)[depth:]
    else:
        dropped = []

    baseline = baseline_project_statuses(
        demands=state.demands, allocation=allocation,
    )
    for project_id in state.known_project_ids:
        baseline.setdefault(project_id, ProjectScenarioStatus.IN_PROGRESS)

    baseline_labels = {k: v.value for k, v in baseline.items()}
    root_signature = build_state_signature({"statuses": baseline_labels})
    root_id_signature = build_state_signature({
        "allocation": allocation.id,
        "statuses": baseline_labels,
        "as_of": state.as_of.isoformat(),
    })

    root = PortfolioScenarioState(
        id=f"pstate-root-{root_id_signature}",
        horizon_step=0,
        simulated_at=state.as_of,
        project_statuses=dict(baseline),
        allocation_id=allocation.id,
        evidence_root_ids=list(state.evidence_ids),
        state_signature=root_signature,
    )

    states: dict[str, PortfolioScenarioState] = {root.id: root}
    transitions: list[PortfolioScenarioTransition] = []
    tail_masses: list[ScenarioTailMass] = []

    # Each frontier entry: (state, cumulative probability, factor outcomes)
    frontier: list[tuple[PortfolioScenarioState, float, dict[str, bool]]] = [
        (root, 1.0, {})
    ]

    for step, factor in enumerate(factors, start=1):
        merged: dict[str, tuple[PortfolioScenarioState, float, dict]] = {}
        carried: list[tuple[PortfolioScenarioState, float, dict]] = []

        for parent, cumulative, outcomes in frontier:
            if parent.is_terminal:
                # Lattice invariant: a terminal outcome does not expand.
                carried.append((parent, cumulative, outcomes))
                continue

            for fired, branch_probability in (
                (True, factor.probability),
                (False, 1.0 - factor.probability),
            ):
                child_cumulative = cumulative * branch_probability
                if branch_probability <= 0.0:
                    continue
                if child_cumulative < horizon.minimum_branch_probability:
                    tail_masses.append(
                        ScenarioTailMass(
                            parent_state_id=parent.id,
                            omitted_probability_mass=round(
                                child_cumulative, 9,
                            ),
                            omitted_branch_count=1,
                            explanation=(
                                f"Branch '{factor.id} "
                                f"{'fires' if fired else 'does not fire'}' "
                                f"pruned below the "
                                f"{horizon.minimum_branch_probability:.2f} "
                                f"display threshold; its mass is retained "
                                f"here so displayed futures do not appear "
                                f"to sum to 100%."
                            ),
                        )
                    )
                    continue

                child_outcomes = {**outcomes, factor.id: fired}
                statuses = apply_factor_outcomes(
                    baseline=baseline,
                    factors=factors,
                    outcomes=child_outcomes,
                )
                resolved = _resolved_project_ids(
                    statuses=statuses,
                    factors=factors,
                    outcomes=child_outcomes,
                    project_ids=list(baseline),
                )
                terminal = set(resolved) == set(baseline)
                labels = _outcome_labels(statuses) if terminal else []

                signature = build_state_signature({
                    "step": step,
                    "terminal": terminal,
                    "statuses": {k: v.value for k, v in statuses.items()},
                })
                child = merged.get(signature)
                if child is None:
                    new_state = PortfolioScenarioState(
                        id=f"pstate-{step}-{signature}",
                        horizon_step=step,
                        simulated_at=state.as_of,
                        parent_state_ids=[parent.id],
                        project_statuses=dict(statuses),
                        resolved_factor_ids=sorted(child_outcomes),
                        fired_factor_ids=sorted(
                            k for k, v in child_outcomes.items() if v
                        ),
                        allocation_id=allocation.id,
                        outcome_labels=labels,
                        evidence_root_ids=list(state.evidence_ids),
                        state_signature=signature,
                    )
                    states[new_state.id] = new_state
                    merged[signature] = (
                        new_state, child_cumulative, child_outcomes,
                    )
                else:
                    existing, existing_mass, existing_outcomes = child
                    if parent.id not in existing.parent_state_ids:
                        existing.parent_state_ids.append(parent.id)
                    merged[signature] = (
                        existing,
                        existing_mass + child_cumulative,
                        existing_outcomes,
                    )

                target = merged[signature][0]
                affected = ", ".join(
                    sorted(set(factor.affected_project_ids))
                ) or "no modelled project"
                occurrence = "occurs" if fired else "does not occur"
                transitions.append(
                    PortfolioScenarioTransition(
                        id=(
                            f"ptrans-{parent.id}-{target.id}-"
                            f"{'fired' if fired else 'clear'}"
                        ),
                        from_state_id=parent.id,
                        to_state_id=target.id,
                        event_description=(
                            f"{factor.description or factor.factor_type} "
                            f"{occurrence}, affecting {affected}"
                        ),
                        shared_factor_id=factor.id,
                        fired=fired,
                        affected_project_ids=sorted(
                            set(factor.affected_project_ids)
                        ),
                        conditional_probability=round(branch_probability, 9),
                        probability_confidence=factor.probability_confidence,
                        evidence_ids=list(factor.evidence_ids),
                    )
                )

        frontier = carried + sorted(
            merged.values(), key=lambda entry: entry[0].id,
        )
        frontier, pruned = _cap_active_states(
            frontier=frontier,
            maximum=horizon.maximum_active_states,
        )
        for pruned_state, pruned_mass in pruned:
            # Remove the state and everything leading to it, so its mass
            # is reported once — as omitted — and never also as displayed.
            states.pop(pruned_state.id, None)
            transitions = [
                t for t in transitions if t.to_state_id != pruned_state.id
            ]
            tail_masses.append(
                ScenarioTailMass(
                    parent_state_id=pruned_state.id,
                    omitted_probability_mass=round(pruned_mass, 9),
                    omitted_branch_count=1,
                    explanation=(
                        f"Portfolio state pruned to stay within "
                        f"{horizon.maximum_active_states} active states; "
                        f"its mass is retained here rather than discarded."
                    ),
                )
            )

    # Anything still open at the horizon becomes terminal as it stands.
    for open_state, _, _ in frontier:
        if not open_state.is_terminal:
            open_state.outcome_labels = _outcome_labels(
                open_state.project_statuses
            )

    for factor in dropped:
        tail_masses.append(
            ScenarioTailMass(
                parent_state_id=root.id,
                omitted_probability_mass=0.0,
                omitted_branch_count=1,
                explanation=(
                    f"Shared factor '{factor.id}' was not expanded because "
                    f"the horizon depth of {horizon.maximum_depth} was "
                    f"reached; the modelled space is smaller than the real "
                    f"one."
                ),
            )
        )

    lattice_signature = build_state_signature({
        "root": root.state_signature,
        "factors": [f.id for f in factors],
    })

    return PortfolioScenarioLattice(
        id=f"plattice-{allocation.id}-{lattice_signature}",
        root_state_id=root.id,
        allocation_id=allocation.id,
        project_ids=sorted(baseline),
        states=[states[k] for k in sorted(states)],
        transitions=sorted(transitions, key=lambda t: t.id),
        tail_masses=tail_masses,
        factors=factors,
        generated_at=generated_at,
        horizon=horizon,
    )


def _resolved_project_ids(
    *,
    statuses: dict[str, ProjectScenarioStatus],
    factors: list[SharedScenarioFactor],
    outcomes: dict[str, bool],
    project_ids: list[str],
) -> list[str]:
    """Projects whose situation can no longer change in this lattice.

    A project is resolved once every shared factor touching it has been
    drawn — or once it is already in a settled situation.
    """
    resolved: list[str] = []
    for project_id in project_ids:
        touching = factors_affecting_project(
            factors=factors, project_id=project_id,
        )
        if all(f.id in outcomes for f in touching):
            resolved.append(project_id)
        elif statuses.get(project_id) == ProjectScenarioStatus.DELAYED:
            # Already delayed; further shared failures cannot worsen it.
            resolved.append(project_id)
    return resolved


def _cap_active_states(
    *,
    frontier: list[tuple[PortfolioScenarioState, float, dict[str, bool]]],
    maximum: int,
) -> tuple[
    list[tuple[PortfolioScenarioState, float, dict[str, bool]]],
    list[tuple[PortfolioScenarioState, float]],
]:
    """Keep the most probable states, returning what was dropped.

    The caller is responsible for recording the dropped mass — the point
    of returning it rather than discarding it here.
    """
    if maximum <= 0 or len(frontier) <= maximum:
        return frontier, []
    ordered = sorted(frontier, key=lambda entry: (-entry[1], entry[0].id))
    kept, dropped = ordered[:maximum], ordered[maximum:]
    return kept, [(entry[0], entry[1]) for entry in dropped]


# ── Reading probability out of the lattice ───────────────────────────

def state_probabilities(
    lattice: PortfolioScenarioLattice,
) -> dict[str, float]:
    """Forward-propagate probability over the lattice.

    A convergent state's probability is the sum over its *incoming
    transitions*, so a path that reaches it twice is not counted twice —
    each distinct path contributes exactly once.
    """
    probabilities: dict[str, float] = {lattice.root_state_id: 1.0}
    for state in sorted(lattice.states, key=lambda s: s.horizon_step):
        if state.id == lattice.root_state_id:
            continue
        total = 0.0
        for transition in lattice.incoming(state.id):
            total += (
                probabilities.get(transition.from_state_id, 0.0)
                * transition.conditional_probability
            )
        probabilities[state.id] = round(total, 12)
    return probabilities


def portfolio_outcome_probability(
    *, lattice: PortfolioScenarioLattice, label: str,
) -> float:
    probabilities = state_probabilities(lattice)
    return round(
        sum(
            probabilities.get(s.id, 0.0)
            for s in lattice.terminal_states
            if label in s.outcome_labels
        ),
        9,
    )


def total_displayed_portfolio_probability(
    lattice: PortfolioScenarioLattice,
) -> float:
    probabilities = state_probabilities(lattice)
    return round(
        sum(probabilities.get(s.id, 0.0) for s in lattice.terminal_states), 9,
    )


def omitted_portfolio_probability_mass(
    lattice: PortfolioScenarioLattice,
) -> float:
    return round(
        sum(t.omitted_probability_mass for t in lattice.tail_masses), 9,
    )


def convergent_portfolio_states(
    lattice: PortfolioScenarioLattice,
) -> list[PortfolioScenarioState]:
    """States several distinct futures arrive at — the reason this is a
    lattice and not a tree."""
    return [s for s in lattice.states if len(set(s.parent_state_ids)) > 1]
