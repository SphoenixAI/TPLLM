"""Portfolio and organizational topology intelligence (Commit 28).

Project-level intelligence can report three separate problems when there
is only one. Four projects waiting on the same approval authority will
each conclude, independently and correctly, that they are blocked — and
each will be wrong about why.

This layer models the organization as a shared dependency topology so
the common cause becomes visible, and it never recommends a priority
order without stating what the displaced project pays.

Capacity is a structural resource estimate carrying its own confidence.
Nothing here measures an individual's output.
"""

from lltpm.portfolio.capacity import (
    aggregate_demand,
    detect_bottlenecks,
    detect_shared_failure_domains,
    explain_bottleneck,
)
from lltpm.portfolio.collisions import (
    detect_launch_window_collisions,
    detect_resource_collisions,
)
from lltpm.portfolio.graph import (
    build_portfolio_graph,
    projects_sharing_node,
    shared_nodes,
)
from lltpm.portfolio.models import (
    CapacityResourceType,
    OrganizationalCapacityWindow,
    PortfolioBottleneck,
    PortfolioCapacityDemand,
    PortfolioCollision,
    PortfolioCollisionType,
    PortfolioEdge,
    PortfolioEdgeType,
    PortfolioGraphSnapshot,
    PortfolioInterventionLeverage,
    PortfolioNode,
    PortfolioNodeType,
    PortfolioOpportunityCost,
    PortfolioPriorityRecommendation,
    SharedFailureDomain,
    build_portfolio_fingerprint,
)
from lltpm.portfolio.prioritization import (
    assess_intervention_leverage,
    build_priority_recommendation,
    displacement_cost,
    prioritization_weight,
    rank_interventions_by_leverage,
)
from lltpm.portfolio.queries import (
    biggest_shared_bottleneck,
    colliding_launches,
    explain_portfolio_risk,
    highest_leverage_intervention,
    invisible_portfolio_risk,
    projects_competing_for_authority,
    projects_without_alternative_route,
    shared_failure_services,
    who_can_be_delayed_safely,
)

__all__ = [
    "CapacityResourceType",
    "OrganizationalCapacityWindow",
    "PortfolioBottleneck",
    "PortfolioCapacityDemand",
    "PortfolioCollision",
    "PortfolioCollisionType",
    "PortfolioEdge",
    "PortfolioEdgeType",
    "PortfolioGraphSnapshot",
    "PortfolioInterventionLeverage",
    "PortfolioNode",
    "PortfolioNodeType",
    "PortfolioOpportunityCost",
    "PortfolioPriorityRecommendation",
    "SharedFailureDomain",
    "aggregate_demand",
    "assess_intervention_leverage",
    "biggest_shared_bottleneck",
    "build_portfolio_fingerprint",
    "build_portfolio_graph",
    "build_priority_recommendation",
    "colliding_launches",
    "detect_bottlenecks",
    "detect_launch_window_collisions",
    "detect_resource_collisions",
    "detect_shared_failure_domains",
    "displacement_cost",
    "explain_bottleneck",
    "explain_portfolio_risk",
    "highest_leverage_intervention",
    "invisible_portfolio_risk",
    "prioritization_weight",
    "projects_competing_for_authority",
    "projects_sharing_node",
    "projects_without_alternative_route",
    "rank_interventions_by_leverage",
    "shared_failure_services",
    "shared_nodes",
    "who_can_be_delayed_safely",
]
