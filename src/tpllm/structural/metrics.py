"""Metric engine — pure NetworkX topology measurements, no LLM.

Five core structural metrics, each returning a ``GraphMetricObservation``
that mirrors the shape of a ``KPIObservation`` (and composes ``EvidenceBacked``)
so structural findings flow through the same measurement and validation
machinery as every other KPI:

    - betweenness_centrality   — bottleneck nodes (people AND non-people)
    - scope_variance           — distance from owned work to modified work
    - dependency_blast_radius  — downstream dependents reachable via blocks
    - active_context_entropy   — spread of a person's active work across domains
    - review_concentration     — how much review load funnels through one person

Each metric is deterministic and explainable — the prediction layer
(intervention simulation) re-runs these on mutated graph copies.
"""

from __future__ import annotations

import math
from datetime import datetime

import networkx as nx
from pydantic import Field

from tpllm.ontology.interfaces import EvidenceBacked
from tpllm.ontology.measurement import HealthStatus
from tpllm.structural.graph import (
    PROPAGATING_EDGE_TYPES,
    NodeType,
    node_type_of,
)
from tpllm.structural.ownership import OwnershipContext

# Distance assigned when a modified node is unreachable from any owned node —
# i.e. it lives in an unrelated part of the program. Caps "infinity" so the
# average stays meaningful.
UNRELATED_DISTANCE: float = 5.0


# ── Observation Model ───────────────────────────────────────────────

class GraphMetricObservation(EvidenceBacked):
    """One structural measurement at one moment — evidence-backed.

    Mirrors KPIObservation so it can be cited as evidence by a KPIInsight
    and carried through the validation gate.
    """
    id: str = ""
    metric_name: str = ""
    measured_node_id: str = ""
    value: float = 0.0
    baseline_value: float = 0.0
    delta_from_baseline: float = 0.0
    interpretation: HealthStatus = HealthStatus.UNKNOWN
    observed_at: datetime = Field(default_factory=datetime.utcnow)
    detail: str = ""


# ── Helpers ─────────────────────────────────────────────────────────

def _propagation_subgraph(graph: nx.MultiDiGraph) -> nx.DiGraph:
    """A simple DiGraph of only propagating (blocks/depends_on) edges."""
    sub = nx.DiGraph()
    sub.add_nodes_from(graph.nodes())
    for frm, to, data in graph.edges(data=True):
        etype = data.get("edge_type")
        etype_val = getattr(etype, "value", etype)
        if etype_val in PROPAGATING_EDGE_TYPES:
            sub.add_edge(frm, to)
    return sub


def _classify(value: float, watch: float, warning: float, critical: float) -> HealthStatus:
    """Higher value = worse. Map a value onto a health band."""
    if value >= critical:
        return HealthStatus.CRITICAL
    if value >= warning:
        return HealthStatus.WARNING
    if value >= watch:
        return HealthStatus.WATCH
    return HealthStatus.HEALTHY


# ── 1. Betweenness Centrality (bottlenecks) ─────────────────────────

def betweenness_centrality(graph: nx.MultiDiGraph) -> list[GraphMetricObservation]:
    """Rank nodes by betweenness centrality — how much shortest-path flow
    passes through them. A high-betweenness node is a structural bottleneck,
    and it is emphatically NOT always a person: an API contract that four
    workstreams route through is the bottleneck, not any engineer.

    Returns observations sorted by value descending.
    """
    scores = nx.betweenness_centrality(graph)
    observations: list[GraphMetricObservation] = []
    for node_id, score in scores.items():
        observations.append(
            GraphMetricObservation(
                id=f"gm-betweenness-{node_id}",
                metric_name="betweenness_centrality",
                measured_node_id=node_id,
                value=float(score),
                interpretation=_classify(score, 0.15, 0.35, 0.6),
                confidence=0.9,
                detail=f"node_type={getattr(node_type_of(graph, node_id), 'value', 'unknown')}",
            )
        )
    observations.sort(key=lambda o: o.value, reverse=True)
    return observations


def top_bottleneck(graph: nx.MultiDiGraph) -> GraphMetricObservation | None:
    """The single highest-betweenness node, or None for an empty graph."""
    obs = betweenness_centrality(graph)
    return obs[0] if obs else None


# ── 2. Scope Variance (drift) ───────────────────────────────────────

def scope_variance(
    graph: nx.MultiDiGraph,
    context: OwnershipContext,
    modified_node_ids: list[str],
    bridges: list | None = None,
    at: datetime | None = None,
) -> GraphMetricObservation:
    """Average graph distance from a person's baseline-owned nodes to the
    nodes they are actively modifying.

    Modifying what you own → distance 0 → no drift. Modifying a far-off
    domain → large distance → drift. But any node that is temporarily
    authorized, historically familiar, or covered by an active
    AuthorizedBridge contributes zero distance — that is the false-positive
    suppressor (``effective_variance = max(0, actual - authorized)``).
    """
    undirected = graph.to_undirected(as_view=False)
    owned = context.baseline_owned_node_ids
    distances: list[float] = []

    for node_id in modified_node_ids:
        # Expected work (owned/authorized/familiar/bridged) → zero contribution.
        if context.is_expected(node_id, bridges=bridges, at=at):
            distances.append(0.0)
            continue
        if not owned:
            distances.append(UNRELATED_DISTANCE)
            continue
        best = UNRELATED_DISTANCE
        for owned_id in owned:
            if owned_id == node_id:
                best = 0.0
                break
            if owned_id in undirected and node_id in undirected:
                try:
                    d = nx.shortest_path_length(undirected, owned_id, node_id)
                    best = min(best, float(d))
                except nx.NetworkXNoPath:
                    continue
        distances.append(best)

    value = sum(distances) / len(distances) if distances else 0.0
    return GraphMetricObservation(
        id=f"gm-scope-{context.person_id}",
        metric_name="scope_variance",
        measured_node_id=context.person_id,
        value=value,
        interpretation=_classify(value, 1.0, 2.5, 4.0),
        confidence=0.85,
        detail=f"modified={len(modified_node_ids)} owned={len(owned)}",
    )


# ── 3. Dependency Blast Radius ──────────────────────────────────────

def dependency_blast_radius(
    graph: nx.MultiDiGraph,
    node_id: str,
) -> GraphMetricObservation:
    """Count the downstream nodes reachable from ``node_id`` via
    blocks/depends_on edges — how many things break if this slips.
    """
    sub = _propagation_subgraph(graph)
    if node_id in sub:
        descendants = nx.descendants(sub, node_id)
    else:
        descendants = set()
    value = float(len(descendants))
    return GraphMetricObservation(
        id=f"gm-blast-{node_id}",
        metric_name="dependency_blast_radius",
        measured_node_id=node_id,
        value=value,
        interpretation=_classify(value, 2.0, 4.0, 6.0),
        confidence=0.95,
        detail=f"descendants={sorted(descendants)}",
    )


# ── 4. Active Context Entropy ───────────────────────────────────────

def active_context_entropy(
    graph: nx.MultiDiGraph,
    person_id: str,
    active_node_ids: list[str],
    cluster_key: str = "domain",
) -> GraphMetricObservation:
    """Shannon entropy (bits) over the domain/cluster distribution of a
    person's active work.

    Many tasks concentrated in one cluster → low entropy (focused). A handful
    of tasks scattered across unrelated clusters → high entropy (fragmented
    context-switching). This is ONE signal among several, never the verdict.
    """
    counts: dict[str, int] = {}
    for node_id in active_node_ids:
        cluster = _cluster_of(graph, node_id, cluster_key)
        counts[cluster] = counts.get(cluster, 0) + 1

    total = sum(counts.values())
    entropy = 0.0
    if total > 0:
        for c in counts.values():
            p = c / total
            entropy -= p * math.log2(p)

    return GraphMetricObservation(
        id=f"gm-entropy-{person_id}",
        metric_name="active_context_entropy",
        measured_node_id=person_id,
        value=entropy,
        interpretation=_classify(entropy, 0.5, 1.5, 2.5),
        confidence=0.8,
        detail=f"clusters={counts}",
    )


def _cluster_of(graph: nx.MultiDiGraph, node_id: str, cluster_key: str) -> str:
    """Resolve a node's cluster/domain label for entropy bucketing."""
    if node_id in graph:
        props = graph.nodes[node_id].get("properties") or {}
        if cluster_key in props:
            return str(props[cluster_key])
        label = graph.nodes[node_id].get("label")
        if label:
            return str(label)
    return node_id


# ── 5. Review Concentration ─────────────────────────────────────────

def review_concentration(graph: nx.MultiDiGraph) -> GraphMetricObservation:
    """Fraction of reviewed workstreams whose review load funnels through the
    single busiest reviewer. High concentration = a human bottleneck and a
    bus-factor risk.
    """
    reviewer_targets: dict[str, set[str]] = {}
    all_targets: set[str] = set()
    for frm, to, data in graph.edges(data=True):
        etype = getattr(data.get("edge_type"), "value", data.get("edge_type"))
        if etype == "reviews":
            reviewer_targets.setdefault(frm, set()).add(to)
            all_targets.add(to)

    if not all_targets:
        return GraphMetricObservation(
            id="gm-review-concentration",
            metric_name="review_concentration",
            measured_node_id="",
            value=0.0,
            interpretation=HealthStatus.HEALTHY,
            confidence=0.7,
            detail="no review edges",
        )

    busiest_id, busiest = max(
        reviewer_targets.items(), key=lambda kv: len(kv[1])
    )
    value = len(busiest) / len(all_targets)
    return GraphMetricObservation(
        id="gm-review-concentration",
        metric_name="review_concentration",
        measured_node_id=busiest_id,
        value=value,
        interpretation=_classify(value, 0.5, 0.7, 0.9),
        confidence=0.8,
        detail=f"reviewer={busiest_id} covers {len(busiest)}/{len(all_targets)}",
    )
