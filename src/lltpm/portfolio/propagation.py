"""Cross-project failure propagation (Commit 29).

A slip in one project is rarely contained inside it. If project A holds
the Platform approval slot longer than expected, B and C do not simply
carry on — they wait behind it. If A's incident consumes the Account
Graph team, C's integration work stalls too.

This module traces those routes explicitly through the portfolio graph
so the second-order effect is named rather than discovered later.

Two rules keep the arithmetic honest:

- Two routes through the *same* shared resource describe one mechanism
  and are counted once, not twice.
- Genuinely distinct mechanisms combine by noisy-OR, which can never
  exceed 1.0 — adding probabilities would produce impossible numbers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from lltpm.portfolio.models import (
    PortfolioCapacityDemand,
    PortfolioEdgeType,
    PortfolioGraphSnapshot,
    PortfolioNodeType,
)

# How readily a delay travels through each kind of shared node. These are
# structural transmission weights over resource contention — they are not
# statements about any team's reliability.
TRANSMISSION_BY_NODE_TYPE = {
    PortfolioNodeType.DECISION_AUTHORITY: 0.65,
    PortfolioNodeType.SERVICE: 0.45,
    PortfolioNodeType.TEAM: 0.40,
    PortfolioNodeType.MILESTONE: 0.30,
    PortfolioNodeType.KPI: 0.20,
}

_SHARING_EDGE_TYPES = {
    PortfolioEdgeType.REQUIRES_APPROVAL_FROM,
    PortfolioEdgeType.SHARES_SERVICE,
    PortfolioEdgeType.SHARES_TEAM,
    PortfolioEdgeType.SHARES_DEPENDENCY,
    PortfolioEdgeType.SHARES_MILESTONE_WINDOW,
    PortfolioEdgeType.COMPETES_FOR_CAPACITY,
    PortfolioEdgeType.AFFECTS_KPI,
}

_HOP_DECAY = 0.55


class CrossProjectImpact(BaseModel):
    """One project's trouble arriving at another project's door."""
    id: str
    source_project_id: str
    affected_project_id: str
    transmission_probability: float
    probability_confidence: float = 0.5
    hops: int = 1
    route_resource_ids: list[str] = Field(default_factory=list)
    mechanisms: list[str] = Field(default_factory=list)
    affected_has_alternative_route: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str = ""

    @model_validator(mode="after")
    def _check(self) -> "CrossProjectImpact":
        for name in ("transmission_probability", "probability_confidence"):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be within [0, 1].")
        if self.source_project_id == self.affected_project_id:
            raise ValueError(
                "A project does not propagate a delay to itself."
            )
        return self


def combine_independent_transmissions(probabilities: list[float]) -> float:
    """Noisy-OR over distinct mechanisms.

    Two separate routes each carrying 0.6 give 0.84, not 1.2. Summing
    would have produced a probability greater than one — which is not a
    probability.

    **No leak term, deliberately.** The canonical noisy-OR of the
    Bayesian-network literature usually carries a leak: a standing
    probability that the effect occurs even when every modelled cause is
    absent, representing causes nobody enumerated. That is a true
    statement about the world — a project can slip for reasons outside
    this graph — and it is still left out here, because a leak would
    give every project a non-zero propagated impact with no mechanism
    behind it, and this system's job is to say what the structure
    implies rather than to manufacture risk.

    The consequence is stated rather than hidden: these probabilities
    under-estimate total risk rather than over-estimating it. For a tool
    that asks a person to act on them, that is the safer direction to be
    wrong in, and "no modelled mechanism" reads as no claim rather than
    as a floor the system invented.
    """
    survival = 1.0
    for probability in probabilities:
        survival *= 1.0 - max(0.0, min(1.0, probability))
    return round(1.0 - survival, 9)


def _shared_resource_nodes(
    *, graph: PortfolioGraphSnapshot, project_id: str,
) -> list:
    """Resource nodes this project reaches that at least one other
    project also depends on."""
    node_id = f"project:{project_id}"
    reached: dict[str, object] = {}
    for edge in graph.edges_from(node_id):
        if edge.edge_type not in _SHARING_EDGE_TYPES:
            continue
        node = graph.node(edge.target_id)
        if node is None or node.node_type not in TRANSMISSION_BY_NODE_TYPE:
            continue
        if len(set(node.project_ids)) < 2:
            continue
        reached[node.id] = node
    return [reached[k] for k in sorted(reached)]


def propagate_delay_across_projects(
    *,
    graph: PortfolioGraphSnapshot,
    source_project_id: str,
    demands: list[PortfolioCapacityDemand] | None = None,
    source_severity: float = 1.0,
    maximum_hops: int = 2,
    confidence: float = 0.5,
) -> list[CrossProjectImpact]:
    """Trace where a delay in one project lands elsewhere.

    Routes are grouped by the shared resource they travel through, so a
    project reached twice through the same approval queue is counted once
    — that is one contended resource, not two independent risks.
    """
    if maximum_hops < 1:
        raise ValueError("maximum_hops must be at least one.")
    demands = demands or []
    severity = max(0.0, min(1.0, source_severity))

    # affected project -> resource id -> strongest transmission via it
    routes: dict[str, dict[str, tuple[float, int, str]]] = {}
    visited_projects = {source_project_id}
    frontier = [(source_project_id, severity, 1, [])]

    while frontier:
        next_frontier: list[tuple[str, float, int, list[str]]] = []
        for project_id, carried, hop, path in frontier:
            if hop > maximum_hops:
                continue
            for node in _shared_resource_nodes(
                graph=graph, project_id=project_id,
            ):
                weight = TRANSMISSION_BY_NODE_TYPE[node.node_type]
                transmitted = carried * weight * (_HOP_DECAY ** (hop - 1))
                for neighbour in sorted(set(node.project_ids)):
                    if neighbour == source_project_id:
                        continue
                    if neighbour == project_id:
                        continue
                    by_resource = routes.setdefault(neighbour, {})
                    mechanism = (
                        f"shares {node.node_type.value.replace('_', ' ')} "
                        f"'{node.label}' with {project_id}"
                    )
                    existing = by_resource.get(node.id)
                    if existing is None or transmitted > existing[0]:
                        by_resource[node.id] = (
                            round(transmitted, 9), hop, mechanism,
                        )
                    if (
                        neighbour not in visited_projects
                        and hop + 1 <= maximum_hops
                    ):
                        visited_projects.add(neighbour)
                        next_frontier.append(
                            (neighbour, transmitted, hop + 1, path + [node.id])
                        )
        frontier = next_frontier

    alternatives = _projects_with_alternative_routes(demands)
    impacts: list[CrossProjectImpact] = []
    evidence = _demand_evidence(demands)

    for affected in sorted(routes):
        by_resource = routes[affected]
        combined = combine_independent_transmissions(
            [entry[0] for entry in by_resource.values()]
        )
        resource_ids = sorted(by_resource)
        mechanisms = [by_resource[r][2] for r in resource_ids]
        hops = min(by_resource[r][1] for r in resource_ids)
        has_alternative = affected in alternatives
        impacts.append(
            CrossProjectImpact(
                id=f"xproj-{source_project_id}-{affected}",
                source_project_id=source_project_id,
                affected_project_id=affected,
                transmission_probability=combined,
                probability_confidence=confidence,
                hops=hops,
                route_resource_ids=resource_ids,
                mechanisms=mechanisms,
                affected_has_alternative_route=has_alternative,
                evidence_ids=evidence,
                explanation=(
                    f"A delay in {source_project_id} reaches {affected} with "
                    f"probability {combined:.2f} (confidence "
                    f"{confidence:.2f}) via "
                    f"{len(resource_ids)} shared resource(s): "
                    f"{', '.join(resource_ids)}."
                    + (
                        " The affected project has an alternative route, so "
                        "the delay is survivable locally."
                        if has_alternative
                        else " The affected project has no alternative route, "
                             "so it absorbs the delay directly."
                    )
                ),
            )
        )

    return sorted(
        impacts,
        key=lambda i: (-i.transmission_probability, i.affected_project_id),
    )


def _projects_with_alternative_routes(
    demands: list[PortfolioCapacityDemand],
) -> set[str]:
    return {
        d.project_id for d in demands
        if d.alternative_resource_ids or d.has_workaround
    }


def _demand_evidence(demands: list[PortfolioCapacityDemand]) -> list[str]:
    evidence: list[str] = []
    for demand in demands:
        for eid in demand.evidence_ids:
            if eid not in evidence:
                evidence.append(eid)
    return evidence


def projects_at_risk_from(
    *,
    impacts: list[CrossProjectImpact],
    minimum_probability: float = 0.25,
) -> list[str]:
    return sorted({
        i.affected_project_id for i in impacts
        if i.transmission_probability >= minimum_probability
    })


def stranded_downstream_projects(
    impacts: list[CrossProjectImpact],
) -> list[str]:
    """Reached projects that have nowhere else to go."""
    return sorted({
        i.affected_project_id for i in impacts
        if not i.affected_has_alternative_route
    })


def summarize_cross_project_propagation(
    impacts: list[CrossProjectImpact],
) -> str:
    if not impacts:
        return (
            "No modelled route carries this project's delay to another "
            "project in the current portfolio topology."
        )
    source = impacts[0].source_project_id
    parts = [
        f"A delay in {source} propagates to {len(impacts)} other "
        f"project(s)."
    ]
    for impact in impacts:
        parts.append(
            f"{impact.affected_project_id}: "
            f"{impact.transmission_probability:.2f} via "
            f"{', '.join(impact.route_resource_ids)}"
            + ("" if impact.affected_has_alternative_route
               else " (no alternative route)")
            + "."
        )
    stranded = stranded_downstream_projects(impacts)
    if stranded:
        verb = "absorbs" if len(stranded) == 1 else "absorb"
        parts.append(
            f"{', '.join(stranded)} {verb} the delay directly and should be "
            f"told before the fact, not after."
        )
    return " ".join(parts)
