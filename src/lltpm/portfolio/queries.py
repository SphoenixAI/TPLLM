"""Portfolio queries.

The questions that only become answerable once projects are viewed
together: what is the biggest shared bottleneck, which projects compete
for the same authority, which launches collide, who can be delayed
safely, and what risk is invisible when projects are viewed separately.
"""

from __future__ import annotations

from lltpm.portfolio.capacity import detect_shared_failure_domains
from lltpm.portfolio.graph import shared_nodes
from lltpm.portfolio.models import (
    PortfolioBottleneck,
    PortfolioCapacityDemand,
    PortfolioCollision,
    PortfolioGraphSnapshot,
    PortfolioInterventionLeverage,
    PortfolioNode,
    PortfolioNodeType,
    SharedFailureDomain,
)
from lltpm.portfolio.prioritization import (
    displacement_cost,
    rank_interventions_by_leverage,
)


def biggest_shared_bottleneck(
    bottlenecks: list[PortfolioBottleneck],
) -> PortfolioBottleneck | None:
    """Ranked by how many projects it holds and how far demand exceeds
    capacity — not by which project complained loudest."""
    if not bottlenecks:
        return None
    return sorted(
        bottlenecks,
        key=lambda b: (
            -len(b.affected_project_ids), -b.overload_ratio, b.id,
        ),
    )[0]


def projects_competing_for_authority(
    *, graph: PortfolioGraphSnapshot,
) -> dict[str, list[str]]:
    """Authority node -> the projects contending for it."""
    return {
        node.id: sorted(set(node.project_ids))
        for node in shared_nodes(
            graph=graph, node_type=PortfolioNodeType.DECISION_AUTHORITY,
        )
    }


def colliding_launches(
    collisions: list[PortfolioCollision],
) -> list[PortfolioCollision]:
    from lltpm.portfolio.models import PortfolioCollisionType

    return [
        c for c in collisions
        if c.collision_type == PortfolioCollisionType.LAUNCH_WINDOW
    ]


def shared_failure_services(
    *, graph: PortfolioGraphSnapshot,
) -> list[PortfolioNode]:
    return shared_nodes(graph=graph, node_type=PortfolioNodeType.SERVICE)


def highest_leverage_intervention(
    interventions: list[PortfolioInterventionLeverage],
) -> PortfolioInterventionLeverage | None:
    ranked = rank_interventions_by_leverage(interventions)
    return ranked[0] if ranked else None


def who_can_be_delayed_safely(
    demands: list[PortfolioCapacityDemand],
) -> list[PortfolioCapacityDemand]:
    """Demands with somewhere else to go, cheapest displacement first.

    Having a workaround is not the same as being unimportant — it means
    delay is survivable, which is the only thing being asserted here.
    """
    return sorted(
        (
            d for d in demands
            if d.has_workaround or d.alternative_resource_ids
        ),
        key=lambda d: (displacement_cost(d), d.id),
    )


def projects_without_alternative_route(
    demands: list[PortfolioCapacityDemand],
) -> list[str]:
    return sorted({
        d.project_id for d in demands
        if not d.has_workaround and not d.alternative_resource_ids
    })


def invisible_portfolio_risk(
    *,
    graph: PortfolioGraphSnapshot,
    demands: list[PortfolioCapacityDemand],
) -> list[SharedFailureDomain]:
    """Risk that only exists at the portfolio level.

    Each project sees a local dependency; only the combined view shows
    that one failure would take several of them down together.
    """
    domains = detect_shared_failure_domains(demands=demands)
    return [d for d in domains if len(d.project_ids) >= 2]


def explain_portfolio_risk(
    domains: list[SharedFailureDomain],
) -> str:
    if not domains:
        return (
            "No shared failure domain spans more than one project in the "
            "modeled portfolio."
        )
    lines = []
    for domain in domains:
        lines.append(
            f"{domain.resource_id}: {len(domain.project_ids)} project(s) "
            f"depend on it"
            + (
                f", and {', '.join(domain.projects_without_alternative)} "
                f"have no alternative route."
                if domain.projects_without_alternative
                else ", all with alternative routes available."
            )
        )
    return " ".join(lines)
