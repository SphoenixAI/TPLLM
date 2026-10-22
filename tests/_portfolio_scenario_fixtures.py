"""Fixtures for cross-project scenario tests.

Extends the canonical portfolio situation — four projects, one Platform
Architecture approval queue with room for about two decisions before
Friday — with the shared uncertainties those projects genuinely have in
common.

The shared factors are the point: A, B, and C all sit behind the same
Platform queue, so "Platform saturates" is *one* uncertainty for three
projects, not three uncertainties. D's exposure is genuinely its own.
"""

from __future__ import annotations

from tpllm.portfolio import (
    PortfolioSimulationState,
    SharedScenarioFactor,
    allocation_from_capacity,
    allocation_from_order,
)
from tpllm.scenarios import ScenarioHorizon

from tests._portfolio_fixtures import (  # noqa: F401 — re-exported
    ACCOUNT_GRAPH,
    FRIDAY,
    PLATFORM,
    T0,
    approval_window,
    demand,
    four_approval_demands,
    four_project_graph_inputs,
)


def platform_saturation_factor(
    *, probability: float = 0.40, factor_id: str = "factor-platform-saturated",
    group: str | None = None,
) -> SharedScenarioFactor:
    """One uncertainty shared by every project behind the queue."""
    return SharedScenarioFactor(
        id=factor_id,
        factor_type="capacity_constraint",
        description="Platform Architecture queue saturates before Friday",
        affected_project_ids=["project-a", "project-b", "project-c"],
        affected_resource_ids=[PLATFORM],
        probability=probability,
        probability_confidence=0.5,
        evidence_ids=["ev-approval-queue"],
        independent_group=group,
    )


def account_graph_factor(
    *, probability: float = 0.25,
) -> SharedScenarioFactor:
    return SharedScenarioFactor(
        id="factor-account-graph-incident",
        factor_type="shared_service_incident",
        description="Account Graph incident consumes integration support",
        affected_project_ids=["project-a", "project-c"],
        affected_resource_ids=[ACCOUNT_GRAPH],
        probability=probability,
        probability_confidence=0.6,
        evidence_ids=["ev-account-graph-incident"],
    )


def partner_ops_factor(*, probability: float = 0.30) -> SharedScenarioFactor:
    """D's own exposure — genuinely independent of the Platform queue."""
    return SharedScenarioFactor(
        id="factor-partner-ops-slip",
        factor_type="approval_slip",
        description="Partner Ops review slips past the window",
        affected_project_ids=["project-d"],
        affected_resource_ids=["Partner Ops"],
        probability=probability,
        probability_confidence=0.5,
        evidence_ids=["ev-partner-ops"],
    )


def shared_factors() -> list[SharedScenarioFactor]:
    return [
        account_graph_factor(),
        partner_ops_factor(),
        platform_saturation_factor(),
    ]


def horizon(
    *,
    maximum_depth: int = 4,
    minimum_branch_probability: float = 0.01,
    maximum_active_states: int = 100,
) -> ScenarioHorizon:
    return ScenarioHorizon(
        start_at=T0,
        end_at=FRIDAY,
        maximum_depth=maximum_depth,
        maximum_active_states=maximum_active_states,
        minimum_branch_probability=minimum_branch_probability,
    )


def portfolio_state(
    *,
    factors: list[SharedScenarioFactor] | None = None,
    demands=None,
    capacity: float = 2.0,
    project_ids: list[str] | None = None,
) -> PortfolioSimulationState:
    return PortfolioSimulationState(
        id="pstate-canonical",
        as_of=T0,
        project_ids=list(
            project_ids
            if project_ids is not None
            else ["project-a", "project-b", "project-c", "project-d"]
        ),
        demands=list(demands or four_approval_demands()),
        capacity_windows=[approval_window(capacity=capacity)],
        shared_factors=list(
            factors if factors is not None else shared_factors()
        ),
        evidence_ids=["ev-approval-queue"],
    )


def weighted_allocation(state: PortfolioSimulationState | None = None):
    """The portfolio order: serve whoever is most expensive to displace."""
    state = state or portfolio_state()
    return allocation_from_capacity(state=state, resource_id=PLATFORM)


def stranding_allocation(state: PortfolioSimulationState | None = None):
    """Deliberately serves the two projects that had somewhere else to go,
    stranding the one that did not."""
    state = state or portfolio_state()
    return allocation_from_order(
        state=state,
        resource_id=PLATFORM,
        ordered_demand_ids=["dem-b", "dem-d", "dem-a", "dem-c"],
        available_units=2.0,
        allocation_id="alloc-strands-project-c",
    )


class CountingRandom:
    """Records how many draws were taken, so 'sampled once' is checked
    rather than assumed."""

    def __init__(self, values: list[float]):
        self.values = list(values)
        self.calls = 0

    def random(self) -> float:
        value = self.values[self.calls % len(self.values)]
        self.calls += 1
        return value
