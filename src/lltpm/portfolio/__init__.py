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
from lltpm.portfolio.optimization import (
    PortfolioAllocationRecommendation,
    PortfolioRegretAssessment,
    assess_allocation_regret,
    compare_allocations,
    explain_portfolio_allocation,
    portfolio_value_by_allocation,
    recommend_portfolio_allocation,
)
from lltpm.portfolio.prioritization import (
    assess_intervention_leverage,
    build_priority_recommendation,
    displacement_cost,
    prioritization_weight,
    rank_interventions_by_leverage,
)
from lltpm.portfolio.propagation import (
    TRANSMISSION_BY_NODE_TYPE,
    CrossProjectImpact,
    combine_independent_transmissions,
    projects_at_risk_from,
    propagate_delay_across_projects,
    stranded_downstream_projects,
    summarize_cross_project_propagation,
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
from lltpm.portfolio.scenarios import (
    PortfolioAllocationDecision,
    PortfolioOutcomeSummary,
    PortfolioScenarioLattice,
    PortfolioScenarioState,
    PortfolioScenarioTransition,
    PortfolioSimulationState,
    ProjectScenarioStatus,
    apply_factor_outcomes,
    baseline_project_statuses,
    build_portfolio_lattice,
    convergent_portfolio_states,
    omitted_portfolio_probability_mass,
    portfolio_outcome_probability,
    project_status_value,
    state_probabilities,
    total_displayed_portfolio_probability,
)
from lltpm.portfolio.shared_factors import (
    SharedScenarioFactor,
    deduplicate_shared_factors,
    factors_affecting_project,
    projects_sharing_factor,
    sample_shared_factors,
    shared_factor_coverage,
)
from lltpm.portfolio.simulation import (
    PortfolioSimulationResult,
    allocation_from_capacity,
    allocation_from_order,
    allocation_from_recommendation,
    bottleneck_state,
    simulate_portfolio_allocation,
)

__all__ = [
    "TRANSMISSION_BY_NODE_TYPE",
    "CapacityResourceType",
    "CrossProjectImpact",
    "OrganizationalCapacityWindow",
    "PortfolioAllocationDecision",
    "PortfolioAllocationRecommendation",
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
    "PortfolioOutcomeSummary",
    "PortfolioPriorityRecommendation",
    "PortfolioRegretAssessment",
    "PortfolioScenarioLattice",
    "PortfolioScenarioState",
    "PortfolioScenarioTransition",
    "PortfolioSimulationResult",
    "PortfolioSimulationState",
    "ProjectScenarioStatus",
    "SharedFailureDomain",
    "SharedScenarioFactor",
    "aggregate_demand",
    "allocation_from_capacity",
    "allocation_from_order",
    "allocation_from_recommendation",
    "apply_factor_outcomes",
    "assess_allocation_regret",
    "assess_intervention_leverage",
    "baseline_project_statuses",
    "biggest_shared_bottleneck",
    "bottleneck_state",
    "build_portfolio_fingerprint",
    "build_portfolio_graph",
    "build_portfolio_lattice",
    "build_priority_recommendation",
    "colliding_launches",
    "combine_independent_transmissions",
    "compare_allocations",
    "convergent_portfolio_states",
    "deduplicate_shared_factors",
    "detect_bottlenecks",
    "detect_launch_window_collisions",
    "detect_resource_collisions",
    "detect_shared_failure_domains",
    "displacement_cost",
    "explain_bottleneck",
    "explain_portfolio_allocation",
    "explain_portfolio_risk",
    "factors_affecting_project",
    "highest_leverage_intervention",
    "invisible_portfolio_risk",
    "omitted_portfolio_probability_mass",
    "portfolio_outcome_probability",
    "portfolio_value_by_allocation",
    "prioritization_weight",
    "project_status_value",
    "projects_at_risk_from",
    "projects_competing_for_authority",
    "projects_sharing_factor",
    "projects_sharing_node",
    "projects_without_alternative_route",
    "propagate_delay_across_projects",
    "rank_interventions_by_leverage",
    "recommend_portfolio_allocation",
    "sample_shared_factors",
    "shared_factor_coverage",
    "shared_failure_services",
    "shared_nodes",
    "simulate_portfolio_allocation",
    "state_probabilities",
    "stranded_downstream_projects",
    "summarize_cross_project_propagation",
    "total_displayed_portfolio_probability",
    "who_can_be_delayed_safely",
]
