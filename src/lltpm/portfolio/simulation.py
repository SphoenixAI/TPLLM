"""Cross-project scenario simulation.

Simulates the portfolio under a specific allocation of a contended
resource, so a recommendation can be answered with "and here is what
happens to everyone else" rather than only "and here is what happens to
the project we optimized".

The load-bearing property is that a shared uncertainty is drawn once per
run and read by every project it touches. Drawing it per project would
make three correlated projects look like three independent bets and the
portfolio would appear far safer than it is.

Runs are reproducible from a seed. Simulation supplements the explicit
lattice in ``scenarios.py``; it does not replace it.
"""

from __future__ import annotations

import random

from pydantic import BaseModel, Field, model_validator

from lltpm.portfolio.models import (
    OrganizationalCapacityWindow,
    PortfolioBottleneck,
    PortfolioCapacityDemand,
    PortfolioPriorityRecommendation,
)
from lltpm.portfolio.prioritization import (
    displacement_cost,
    prioritization_weight,
)
from lltpm.portfolio.scenarios import (
    PortfolioAllocationDecision,
    PortfolioOutcomeSummary,
    PortfolioScenarioLattice,
    PortfolioSimulationState,
    ProjectScenarioStatus,
    apply_factor_outcomes,
    baseline_project_statuses,
    build_portfolio_lattice,
    omitted_portfolio_probability_mass,
    project_status_value,
)
from lltpm.portfolio.shared_factors import (
    SharedScenarioFactor,
    deduplicate_shared_factors,
    factors_affecting_project,
    sample_shared_factors,
)
from lltpm.scenarios.models import ScenarioHorizon


class PortfolioSimulationResult(BaseModel):
    """Outcome distribution for every project under one allocation.

    ``displaced_project_impacts`` must cover every displaced project. A
    result that optimized one project without stating what another paid
    would be precisely the invisible trade-off this layer exists to
    prevent, so it is rejected at construction.
    """
    id: str
    allocation_id: str
    resource_id: str
    runs: int
    seed: int
    project_outcomes: list[PortfolioOutcomeSummary] = Field(
        default_factory=list
    )
    portfolio_expected_value: float = 0.0
    worst_case_portfolio_value: float = 0.0
    portfolio_outcome_frequencies: dict[str, float] = Field(
        default_factory=dict
    )
    shared_factor_frequencies: dict[str, float] = Field(default_factory=dict)
    factor_co_movement: dict[str, float] = Field(default_factory=dict)
    displaced_project_ids: list[str] = Field(default_factory=list)
    displaced_project_impacts: dict[str, float] = Field(default_factory=dict)
    alternative_routes: dict[str, list[str]] = Field(default_factory=dict)
    unmodeled_probability_mass: float = 0.0
    method: str = "seeded_monte_carlo_over_shared_factors"
    explanation: str = ""

    @model_validator(mode="after")
    def _check(self) -> "PortfolioSimulationResult":
        missing = set(self.displaced_project_ids) - set(
            self.displaced_project_impacts
        )
        if missing:
            raise ValueError(
                f"Displaced projects must have their impact stated: "
                f"{sorted(missing)} were displaced without a stated cost."
            )
        return self

    def outcome(self, project_id: str) -> PortfolioOutcomeSummary | None:
        return next(
            (o for o in self.project_outcomes if o.project_id == project_id),
            None,
        )


def allocation_from_capacity(
    *,
    state: PortfolioSimulationState,
    resource_id: str,
    capacity_window: OrganizationalCapacityWindow | None = None,
    allocation_id: str | None = None,
) -> PortfolioAllocationDecision:
    """Greedy allocation by the portfolio prioritization weight.

    Serves the demands that would be most expensive to displace first —
    which favours projects with nowhere else to go, not projects that
    shouted loudest.
    """
    window = capacity_window or next(
        (w for w in state.capacity_windows if w.resource_id == resource_id),
        None,
    )
    if window is None:
        raise ValueError(
            f"No capacity window for resource '{resource_id}'."
        )

    competing = sorted(
        (d for d in state.demands if d.required_resource_id == resource_id),
        key=lambda d: (-prioritization_weight(d), d.id),
    )
    return allocation_from_order(
        state=state,
        resource_id=resource_id,
        ordered_demand_ids=[d.id for d in competing],
        available_units=window.available_capacity_units,
        allocation_id=allocation_id or f"alloc-{resource_id}-weighted",
    )


def allocation_from_order(
    *,
    state: PortfolioSimulationState,
    resource_id: str,
    ordered_demand_ids: list[str],
    available_units: float,
    allocation_id: str | None = None,
) -> PortfolioAllocationDecision:
    """Build an allocation by serving a caller-supplied order.

    Order is not cosmetic: the same capacity spent in a different order
    produces a different portfolio outcome, which is why allocations are
    compared rather than assumed.
    """
    served: list[str] = []
    displaced: list[str] = []
    routed: dict[str, str] = {}
    remaining = available_units

    for demand_id in ordered_demand_ids:
        demand = state.demand(demand_id)
        if demand is None or demand.required_resource_id != resource_id:
            continue
        if remaining >= demand.requested_units:
            served.append(demand_id)
            remaining -= demand.requested_units
        else:
            displaced.append(demand_id)
            route = _route_for(demand)
            if route:
                routed[demand_id] = route

    return PortfolioAllocationDecision(
        id=allocation_id or f"alloc-{resource_id}",
        resource_id=resource_id,
        served_demand_ids=served,
        displaced_demand_ids=displaced,
        routed_demand_ids=routed,
        rationale=(
            f"{resource_id} serves approximately {available_units:.0f} "
            f"unit(s); {len(served)} demand(s) proceed and "
            f"{len(displaced)} wait."
        ),
    )


def allocation_from_recommendation(
    *,
    state: PortfolioSimulationState,
    recommendation: PortfolioPriorityRecommendation,
    available_units: float,
) -> PortfolioAllocationDecision:
    """Turn a portfolio priority recommendation into a simulable
    allocation, preserving its order."""
    return allocation_from_order(
        state=state,
        resource_id=recommendation.resource_id,
        ordered_demand_ids=recommendation.ordered_demand_ids,
        available_units=available_units,
        allocation_id=f"alloc-{recommendation.id}",
    )


def _route_for(demand: PortfolioCapacityDemand) -> str | None:
    if demand.alternative_resource_ids:
        return demand.alternative_resource_ids[0]
    if demand.has_workaround:
        return demand.workaround_description or "workaround available"
    return None


def _routes_for(demand: PortfolioCapacityDemand) -> list[str]:
    routes = list(demand.alternative_resource_ids)
    if demand.has_workaround and demand.workaround_description:
        routes.append(demand.workaround_description)
    return routes


def simulate_portfolio_allocation(
    *,
    portfolio_state: PortfolioSimulationState,
    allocation: PortfolioAllocationDecision,
    horizon: ScenarioHorizon,
    runs: int = 1000,
    seed: int = 12345,
    lattice: PortfolioScenarioLattice | None = None,
) -> PortfolioSimulationResult:
    """Run the portfolio forward under one allocation.

    Each run draws every distinct shared uncertainty exactly once and
    fans that single value out to every project it touches. Independent
    per-project uncertainties keep their own draws, so genuine
    independence survives and false diversification is not manufactured.
    """
    if runs <= 0:
        raise ValueError("runs must be positive.")

    rng = random.Random(seed)
    factors = portfolio_state.shared_factors
    distinct = deduplicate_shared_factors(factors)
    baseline = baseline_project_statuses(
        demands=portfolio_state.demands, allocation=allocation,
    )
    for project_id in portfolio_state.known_project_ids:
        baseline.setdefault(project_id, ProjectScenarioStatus.IN_PROGRESS)
    project_ids = sorted(baseline)

    status_counts: dict[str, dict[str, int]] = {
        p: {} for p in project_ids
    }
    portfolio_label_counts: dict[str, int] = {}
    factor_fire_counts: dict[str, int] = {f.id: 0 for f in distinct}
    factor_co_moved: dict[str, int] = {f.id: 0 for f in distinct}
    value_totals: dict[str, float] = {p: 0.0 for p in project_ids}
    worst_case = None

    for _ in range(runs):
        outcomes = sample_shared_factors(factors=factors, rng=rng)
        statuses = apply_factor_outcomes(
            baseline=baseline, factors=factors, outcomes=outcomes,
        )

        # Read the draws back the way a consumer would — per project —
        # rather than trusting the sampler's own bookkeeping.
        exposure: dict[str, dict[str, bool]] = {
            project_id: {
                f.id: bool(outcomes.get(f.id))
                for f in factors_affecting_project(
                    factors=factors, project_id=project_id,
                )
            }
            for project_id in project_ids
        }
        for factor in distinct:
            if outcomes.get(factor.id):
                factor_fire_counts[factor.id] += 1
            seen = {
                exposure[p][factor.id]
                for p in factor.affected_project_ids
                if p in exposure and factor.id in exposure[p]
            }
            if len(seen) <= 1:
                factor_co_moved[factor.id] += 1

        run_value = 0.0
        for project_id in project_ids:
            status = statuses[project_id]
            status_counts[project_id][status.value] = (
                status_counts[project_id].get(status.value, 0) + 1
            )
            value = project_status_value(
                status=status,
                demands=portfolio_state.demands_for_project(project_id),
            )
            value_totals[project_id] += value
            run_value += value

        worst_case = (
            run_value if worst_case is None else min(worst_case, run_value)
        )
        label = _portfolio_label(statuses)
        portfolio_label_counts[label] = portfolio_label_counts.get(label, 0) + 1

    displaced_demands = [
        d for d in (
            portfolio_state.demand(i) for i in allocation.displaced_demand_ids
        ) if d is not None
    ]
    displaced_projects = sorted({d.project_id for d in displaced_demands})
    displaced_impacts = {
        d.project_id: displacement_cost(d) for d in displaced_demands
    }
    alternative_routes = {
        d.project_id: _routes_for(d) for d in displaced_demands
    }

    summaries: list[PortfolioOutcomeSummary] = []
    for project_id in project_ids:
        counts = status_counts[project_id]
        probabilities = {
            status: round(count / runs, 6)
            for status, count in sorted(counts.items())
        }
        expected = round(value_totals[project_id] / runs, 6)
        summaries.append(
            PortfolioOutcomeSummary(
                project_id=project_id,
                outcome_probabilities=probabilities,
                expected_impact=expected,
                probability_confidence=_project_confidence(
                    factors=factors,
                    project_id=project_id,
                    capacity_windows=portfolio_state.capacity_windows,
                ),
                was_displaced=project_id in displaced_projects,
                displacement_impact=displaced_impacts.get(project_id, 0.0),
                alternative_routes=alternative_routes.get(project_id, []),
                explanation=_project_explanation(
                    project_id=project_id,
                    probabilities=probabilities,
                    displaced=project_id in displaced_projects,
                    routes=alternative_routes.get(project_id, []),
                ),
            )
        )

    lattice = lattice or build_portfolio_lattice(
        state=portfolio_state, allocation=allocation, horizon=horizon,
    )

    return PortfolioSimulationResult(
        id=f"psim-{allocation.id}-{seed}",
        allocation_id=allocation.id,
        resource_id=allocation.resource_id,
        runs=runs,
        seed=seed,
        project_outcomes=summaries,
        portfolio_expected_value=round(
            sum(value_totals.values()) / runs, 6,
        ),
        worst_case_portfolio_value=round(worst_case or 0.0, 6),
        portfolio_outcome_frequencies={
            label: round(count / runs, 6)
            for label, count in sorted(portfolio_label_counts.items())
        },
        shared_factor_frequencies={
            factor_id: round(count / runs, 6)
            for factor_id, count in sorted(factor_fire_counts.items())
        },
        factor_co_movement={
            factor_id: round(count / runs, 6)
            for factor_id, count in sorted(factor_co_moved.items())
        },
        displaced_project_ids=displaced_projects,
        displaced_project_impacts=displaced_impacts,
        alternative_routes=alternative_routes,
        unmodeled_probability_mass=omitted_portfolio_probability_mass(lattice),
        explanation=_result_explanation(
            allocation=allocation,
            runs=runs,
            seed=seed,
            displaced_projects=displaced_projects,
            displaced_impacts=displaced_impacts,
            shared_count=sum(1 for f in distinct if f.is_shared),
        ),
    )


def _portfolio_label(
    statuses: dict[str, ProjectScenarioStatus],
) -> str:
    delayed = sum(
        1 for s in statuses.values()
        if s in (
            ProjectScenarioStatus.DELAYED,
            ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT,
        )
    )
    if delayed == 0:
        return "portfolio_all_projects_progressing"
    if delayed == 1:
        return "portfolio_single_project_delayed"
    return "portfolio_multiple_projects_delayed"


def _project_confidence(
    *,
    factors: list[SharedScenarioFactor],
    project_id: str,
    capacity_windows: list[OrganizationalCapacityWindow],
) -> float:
    """Confidence in this project's forecast — the weakest input wins.

    Kept separate from the probabilities themselves: a 70% chance held
    with 30% confidence is an honest statement, and averaging the two
    would destroy both.
    """
    touching = factors_affecting_project(
        factors=factors, project_id=project_id,
    )
    values = [f.probability_confidence for f in touching]
    values.extend(w.confidence for w in capacity_windows)
    return round(min(values), 6) if values else 0.0


def _project_explanation(
    *,
    project_id: str,
    probabilities: dict[str, float],
    displaced: bool,
    routes: list[str],
) -> str:
    ranked = sorted(probabilities.items(), key=lambda kv: (-kv[1], kv[0]))
    leading = ", ".join(f"{k} {v:.0%}" for k, v in ranked)
    base = f"{project_id}: {leading}."
    if not displaced:
        return base
    if routes:
        return (
            f"{base} This project yields the shared resource and proceeds "
            f"via {routes[0]}."
        )
    return (
        f"{base} This project yields the shared resource and has no "
        f"alternative route, so it absorbs the delay directly."
    )


def _result_explanation(
    *,
    allocation: PortfolioAllocationDecision,
    runs: int,
    seed: int,
    displaced_projects: list[str],
    displaced_impacts: dict[str, float],
    shared_count: int,
) -> str:
    parts = [
        f"{runs} seeded runs (seed={seed}) over allocation "
        f"'{allocation.id}' for {allocation.resource_id}.",
        f"{shared_count} shared uncertainty(ies) were drawn once per run "
        f"and applied to every project they affect, so correlated "
        f"projects move together rather than appearing independent.",
    ]
    if displaced_projects:
        costs = ", ".join(
            f"{p} ({displaced_impacts[p]:.2f})" for p in displaced_projects
        )
        parts.append(f"Displaced, with cost stated: {costs}.")
    else:
        parts.append("No project was displaced by this allocation.")
    return " ".join(parts)


def bottleneck_state(
    *,
    bottleneck: PortfolioBottleneck,
    demands: list[PortfolioCapacityDemand],
    capacity_windows: list[OrganizationalCapacityWindow],
    shared_factors: list[SharedScenarioFactor],
    as_of,
) -> PortfolioSimulationState:
    """Assemble the simulation inputs around one detected bottleneck."""
    return PortfolioSimulationState(
        id=f"pstate-{bottleneck.id}",
        as_of=as_of,
        project_ids=list(bottleneck.affected_project_ids),
        demands=list(demands),
        capacity_windows=list(capacity_windows),
        shared_factors=list(shared_factors),
        evidence_ids=list(bottleneck.evidence_ids),
    )
