"""Portfolio graph construction.

Builds the cross-project projection. Canonical identity is
what makes this work: "Platform Architecture" in project A and
"platform-arch" in project C must resolve to the same node, or the
shared bottleneck stays invisible.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tpllm.portfolio.models import (
    PortfolioEdge,
    PortfolioEdgeType,
    PortfolioGraphSnapshot,
    PortfolioNode,
    PortfolioNodeType,
    build_portfolio_fingerprint,
)


def _canonical_id(
    *, raw_id: str, registry: object, node_type: PortfolioNodeType,
) -> str | None:
    """Resolve a raw reference to its canonical entity where possible.

    The registry is read duck-typed so the portfolio layer does not have
    to depend on the identity package.
    """
    if registry is None:
        return None
    entities = getattr(registry, "entities", None)
    if not isinstance(entities, dict):
        return None
    lowered = raw_id.strip().lower()
    for entity_id, entity in entities.items():
        if not getattr(entity, "active", True):
            continue
        names = [str(getattr(entity, "canonical_name", ""))]
        names.extend(str(a) for a in getattr(entity, "aliases", []) or [])
        if any(n.strip().lower() == lowered for n in names if n):
            return entity_id
        if entity_id.strip().lower() == lowered:
            return entity_id
    return None


def build_portfolio_graph(
    *,
    project_states: list[object],
    workstreams: list[object] | None = None,
    decisions: list[object] | None = None,
    kpis: list[object] | None = None,
    commitments: list[object] | None = None,
    identity_registry: object = None,
    approval_requirements: dict[str, list[str]] | None = None,
    service_usage: dict[str, list[str]] | None = None,
    team_assignments: dict[str, list[str]] | None = None,
    milestone_windows: dict[str, list[str]] | None = None,
    as_of: datetime | None = None,
) -> PortfolioGraphSnapshot:
    """Combine per-project state into one cross-project topology.

    ``approval_requirements`` maps project id -> authority ids; the other
    mappings follow the same shape. Shared references collapse onto a
    single node, which is what makes a common bottleneck visible.
    """
    workstreams = workstreams or []
    as_of = as_of or datetime.utcnow()

    nodes: dict[str, PortfolioNode] = {}
    edges: dict[str, PortfolioEdge] = {}

    def add_node(
        node_id: str,
        node_type: PortfolioNodeType,
        label: str,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PortfolioNode:
        canonical = _canonical_id(
            raw_id=label, registry=identity_registry, node_type=node_type,
        )
        key = canonical or node_id
        existing = nodes.get(key)
        if existing is None:
            existing = PortfolioNode(
                id=key,
                node_type=node_type,
                canonical_entity_id=canonical,
                label=label,
                project_ids=[project_id] if project_id else [],
                metadata=dict(metadata or {}),
            )
            nodes[key] = existing
        else:
            if project_id and project_id not in existing.project_ids:
                nodes[key] = existing.model_copy(
                    update={
                        "project_ids": [*existing.project_ids, project_id],
                    }
                )
        return nodes[key]

    def add_edge(
        source: str,
        target: str,
        edge_type: PortfolioEdgeType,
        evidence_ids: list[str] | None = None,
        confidence: float = 0.7,
    ) -> None:
        edge_id = f"pe-{source}-{edge_type.value}-{target}"
        if edge_id in edges:
            return
        edges[edge_id] = PortfolioEdge(
            id=edge_id, source_id=source, target_id=target,
            edge_type=edge_type,
            evidence_ids=list(evidence_ids or []),
            confidence=confidence,
        )

    project_ids: list[str] = []
    for state in project_states:
        project_id = str(getattr(state, "project_id", ""))
        if not project_id:
            continue
        project_ids.append(project_id)
        add_node(
            f"project:{project_id}", PortfolioNodeType.PROJECT,
            project_id, project_id,
        )

    for workstream in workstreams:
        project_id = str(getattr(workstream, "project_id", ""))
        workstream_id = str(getattr(workstream, "workstream_id", ""))
        if not workstream_id:
            continue
        add_node(
            f"workstream:{workstream_id}", PortfolioNodeType.WORKSTREAM,
            workstream_id, project_id,
        )
        add_edge(
            f"project:{project_id}", f"workstream:{workstream_id}",
            PortfolioEdgeType.SUPPORTS,
        )
        for kpi_id in getattr(workstream, "kpi_ids", []) or []:
            kpi_node = add_node(
                f"kpi:{kpi_id}", PortfolioNodeType.KPI, kpi_id, project_id,
            )
            add_edge(
                f"workstream:{workstream_id}", kpi_node.id,
                PortfolioEdgeType.AFFECTS_KPI,
            )

    for project_id, authorities in (approval_requirements or {}).items():
        for authority in authorities:
            node = add_node(
                f"authority:{authority}",
                PortfolioNodeType.DECISION_AUTHORITY,
                authority, project_id,
            )
            add_edge(
                f"project:{project_id}", node.id,
                PortfolioEdgeType.REQUIRES_APPROVAL_FROM,
            )

    for project_id, services in (service_usage or {}).items():
        for service in services:
            node = add_node(
                f"service:{service}", PortfolioNodeType.SERVICE,
                service, project_id,
            )
            add_edge(
                f"project:{project_id}", node.id,
                PortfolioEdgeType.SHARES_SERVICE,
            )

    for project_id, teams in (team_assignments or {}).items():
        for team in teams:
            node = add_node(
                f"team:{team}", PortfolioNodeType.TEAM, team, project_id,
            )
            add_edge(
                f"project:{project_id}", node.id,
                PortfolioEdgeType.SHARES_TEAM,
            )

    for project_id, milestones in (milestone_windows or {}).items():
        for milestone in milestones:
            node = add_node(
                f"milestone:{milestone}", PortfolioNodeType.MILESTONE,
                milestone, project_id,
            )
            add_edge(
                f"project:{project_id}", node.id,
                PortfolioEdgeType.SHARES_MILESTONE_WINDOW,
            )

    for commitment in commitments or []:
        commitment_id = str(getattr(commitment, "id", ""))
        workstream_id = str(getattr(commitment, "workstream_id", ""))
        if not commitment_id:
            continue
        add_node(
            f"commitment:{commitment_id}", PortfolioNodeType.COMMITMENT,
            commitment_id,
        )
        if workstream_id:
            add_edge(
                f"workstream:{workstream_id}",
                f"commitment:{commitment_id}",
                PortfolioEdgeType.SUPPORTS,
            )

    # Projects sharing a resource compete for its capacity.
    for node in list(nodes.values()):
        if node.node_type not in (
            PortfolioNodeType.DECISION_AUTHORITY,
            PortfolioNodeType.SERVICE,
            PortfolioNodeType.TEAM,
        ):
            continue
        if len(node.project_ids) < 2:
            continue
        for project_id in sorted(node.project_ids):
            add_edge(
                f"project:{project_id}", node.id,
                PortfolioEdgeType.COMPETES_FOR_CAPACITY,
                confidence=0.8,
            )

    ordered_nodes = [nodes[k] for k in sorted(nodes)]
    ordered_edges = [edges[k] for k in sorted(edges)]

    return PortfolioGraphSnapshot(
        id=f"portfolio-{as_of.isoformat()}",
        as_of=as_of,
        nodes=ordered_nodes,
        edges=ordered_edges,
        project_ids=sorted(set(project_ids)),
        state_fingerprint=build_portfolio_fingerprint(
            project_ids=project_ids,
            node_ids=[n.id for n in ordered_nodes],
            edge_ids=[e.id for e in ordered_edges],
        ),
    )


def projects_sharing_node(
    *, graph: PortfolioGraphSnapshot, node_id: str,
) -> list[str]:
    node = graph.node(node_id)
    return sorted(node.project_ids) if node else []


def shared_nodes(
    *,
    graph: PortfolioGraphSnapshot,
    node_type: PortfolioNodeType | None = None,
    minimum_projects: int = 2,
) -> list[PortfolioNode]:
    """Nodes that more than one project depends on — the places where a
    single failure becomes several projects' problem."""
    return [
        n for n in graph.nodes
        if len(set(n.project_ids)) >= minimum_projects
        and (node_type is None or n.node_type == node_type)
    ]
