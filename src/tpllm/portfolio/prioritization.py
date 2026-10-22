"""Portfolio prioritization and leverage.

When a shared resource cannot serve everyone, something must wait. The
non-negotiable rule here is that nothing is displaced invisibly: every
recommendation states what happens to the project that yields, and a
project with a viable workaround is preferred for displacement over one
with a single critical path.
"""

from __future__ import annotations

from tpllm.portfolio.models import (
    OrganizationalCapacityWindow,
    PortfolioBottleneck,
    PortfolioCapacityDemand,
    PortfolioInterventionLeverage,
    PortfolioOpportunityCost,
    PortfolioPriorityRecommendation,
)

# How much a viable alternative route reduces the cost of waiting.
_WORKAROUND_COST_RELIEF = 0.6
_ALTERNATIVE_ROUTE_RELIEF = 0.4


def displacement_cost(demand: PortfolioCapacityDemand) -> float:
    """What it costs the portfolio to make this demand wait.

    Criticality sets the baseline; a workaround or an alternative
    resource reduces it, because the project has somewhere else to go.
    """
    cost = demand.criticality
    if demand.has_workaround:
        cost *= 1.0 - _WORKAROUND_COST_RELIEF
    if demand.alternative_resource_ids:
        cost *= 1.0 - _ALTERNATIVE_ROUTE_RELIEF
    return round(max(0.0, cost), 6)


def prioritization_weight(demand: PortfolioCapacityDemand) -> float:
    """Higher weight is served first.

    A project with no alternative route gains weight — not because it is
    more important, but because delaying it has nowhere to go.
    """
    return displacement_cost(demand)


def build_priority_recommendation(
    *,
    bottleneck: PortfolioBottleneck,
    demands: list[PortfolioCapacityDemand],
    capacity_window: OrganizationalCapacityWindow,
    evidence_ids: list[str] | None = None,
) -> PortfolioPriorityRecommendation:
    """Order competing demands and state the cost of every displacement.

    A recommendation that named an order without naming what the
    displaced projects pay would be exactly the invisible trade-off this
    layer exists to prevent.
    """
    competing = sorted(
        (d for d in demands if d.required_resource_id == bottleneck.resource_id),
        key=lambda d: (-prioritization_weight(d), d.id),
    )
    available = capacity_window.available_capacity_units

    served: list[PortfolioCapacityDemand] = []
    displaced: list[PortfolioCapacityDemand] = []
    remaining = available
    for demand in competing:
        if remaining >= demand.requested_units:
            served.append(demand)
            remaining -= demand.requested_units
        else:
            displaced.append(demand)

    opportunity_costs: list[PortfolioOpportunityCost] = []
    displaced_impacts: dict[str, float] = {}
    alternative_routes: dict[str, list[str]] = {}

    for yielded in displaced:
        cost = displacement_cost(yielded)
        displaced_impacts[yielded.id] = cost
        routes: list[str] = list(yielded.alternative_resource_ids)
        if yielded.has_workaround and yielded.workaround_description:
            routes.append(yielded.workaround_description)
        alternative_routes[yielded.id] = routes

        for selected in served:
            opportunity_costs.append(
                PortfolioOpportunityCost(
                    selected_demand_id=selected.id,
                    displaced_demand_id=yielded.id,
                    selected_project_benefit=selected.criticality,
                    displaced_project_cost=cost,
                    net_portfolio_effect=round(
                        selected.criticality - cost, 6,
                    ),
                    displaced_has_alternative=bool(routes),
                    explanation=(
                        f"Serving {selected.project_id} ahead of "
                        f"{yielded.project_id} costs the latter "
                        f"{cost:.2f}"
                        + (
                            f", mitigated by: {'; '.join(routes)}."
                            if routes
                            else " with no alternative route available."
                        )
                    ),
                )
            )

    outcome_change = {
        d.project_id: round(d.criticality, 6) for d in served
    }
    for yielded in displaced:
        outcome_change[yielded.project_id] = round(
            -displacement_cost(yielded), 6,
        )

    rationale_parts = [
        f"{bottleneck.resource_id} can serve approximately "
        f"{available:.0f} of {bottleneck.demand_units:.0f} requested "
        f"unit(s) (capacity confidence "
        f"{capacity_window.confidence:.2f}).",
    ]
    if served:
        rationale_parts.append(
            "Prioritize " + ", ".join(d.project_id for d in served) + "."
        )
    for yielded in displaced:
        routes = alternative_routes.get(yielded.id) or []
        rationale_parts.append(
            f"{yielded.project_id} waits"
            + (
                f", routed via {routes[0]}."
                if routes
                else " and has no alternative route, so its cost is borne "
                     "directly."
            )
        )

    return PortfolioPriorityRecommendation(
        id=f"priority-{bottleneck.resource_id}",
        resource_id=bottleneck.resource_id,
        ordered_demand_ids=[d.id for d in served + displaced],
        rationale=" ".join(rationale_parts),
        expected_portfolio_outcome_change=outcome_change,
        displaced_demand_impacts=displaced_impacts,
        alternative_routes=alternative_routes,
        opportunity_costs=opportunity_costs,
        evidence_ids=list(evidence_ids or bottleneck.evidence_ids),
        confidence=capacity_window.confidence,
    )


def assess_intervention_leverage(
    *,
    intervention_id: str,
    description: str,
    bottlenecks: list[PortfolioBottleneck],
    coordination_cost: float = 0.2,
    risk_reduction_per_project: float = 0.3,
) -> PortfolioInterventionLeverage:
    """One action across several projects beats several separate pings.

    Leverage rises with the number of projects a single intervention
    unblocks and falls with what it costs to coordinate.
    """
    project_ids: list[str] = []
    for bottleneck in bottlenecks:
        for project_id in bottleneck.affected_project_ids:
            if project_id not in project_ids:
                project_ids.append(project_id)

    total_reduction = risk_reduction_per_project * len(project_ids)
    leverage = (
        total_reduction / coordination_cost
        if coordination_cost > 0
        else total_reduction
    )

    return PortfolioInterventionLeverage(
        intervention_id=intervention_id,
        description=description,
        affected_project_ids=sorted(project_ids),
        estimated_total_risk_reduction=round(total_reduction, 6),
        affected_bottleneck_ids=sorted(b.id for b in bottlenecks),
        coordination_cost=coordination_cost,
        leverage_score=round(leverage, 6),
        explanation=(
            f"'{description}' addresses {len(bottlenecks)} bottleneck(s) "
            f"across {len(project_ids)} project(s) at a coordination cost "
            f"of {coordination_cost:.2f}, for a leverage of "
            f"{leverage:.2f}."
        ),
    )


def rank_interventions_by_leverage(
    interventions: list[PortfolioInterventionLeverage],
) -> list[PortfolioInterventionLeverage]:
    return sorted(
        interventions,
        key=lambda i: (-i.leverage_score, i.intervention_id),
    )
