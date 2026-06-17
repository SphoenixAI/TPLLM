"""Intervention simulation + ranked option matrix.

This is the prediction layer — no ML required. We clone the affected subgraph,
apply a counterfactual mutation (decouple a dependency, inject a bridge/mock,
reassign an owner, resequence a milestone), re-run the metric engine, and
measure the structural risk reduction.

The output is never a single bossy answer. It is a ranked *matrix* of
``InterventionOption``s, scored by tradeoff (risk reduction × confidence ÷
disruption), so a human chooses with the cost-of-each-path visible. Nothing
auto-executes this round — execution_mode is suggest_only.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

import networkx as nx
from pydantic import BaseModel, Field

from lltpm.ontology.entities import AgentRecommendation, RecommendationType
from lltpm.structural.metrics import betweenness_centrality

# A mutator takes a graph and modifies it in place.
Mutator = Callable[[nx.MultiDiGraph], None]

_DISRUPTION_WEIGHT: dict[str, float] = {"low": 1.0, "medium": 2.0, "high": 3.0}


class InterventionType(str, Enum):
    BRIDGE_INJECTION = "bridge_injection"
    REVIEWER_REDISTRIBUTION = "reviewer_redistribution"
    MILESTONE_RESEQUENCING = "milestone_resequencing"
    DEPENDENCY_SPLIT = "dependency_split"
    OWNERSHIP_CLARIFICATION = "ownership_clarification"
    SCOPE_REDUCTION = "scope_reduction"
    DECISION_GATE_INSERTION = "decision_gate_insertion"
    INTERFACE_STABILIZATION = "interface_stabilization"


class InterventionOption(BaseModel):
    """One candidate intervention with its predicted tradeoffs."""
    id: str = ""
    structural_signal_id: str = ""
    intervention_type: InterventionType = InterventionType.BRIDGE_INJECTION
    title: str = ""
    description: str = ""
    predicted_structural_risk_reduction: float = 0.0
    predicted_delay_reduction_days: float = 0.0
    disruption_cost: str = "medium"
    devex_cost: str = "medium"
    implementation_effort: str = "medium"
    confidence: float = 0.5
    tradeoffs: list[str] = Field(default_factory=list)
    required_approval_level: str = "tpm"
    execution_mode: str = "suggest_only"

    def tradeoff_score(self) -> float:
        """Rank key: risk reduction × confidence ÷ disruption.

        Deliberately NOT raw risk reduction — a huge but highly disruptive,
        low-confidence change should lose to a modest, safe, confident one.
        """
        weight = _DISRUPTION_WEIGHT.get(self.disruption_cost.lower(), 2.0)
        return (self.predicted_structural_risk_reduction * self.confidence) / weight


# ── Subgraph Mutators ───────────────────────────────────────────────

def mutate_remove_edge(from_id: str, to_id: str, key=None) -> Mutator:
    """Decouple a dependency by removing an edge."""
    def _m(graph: nx.MultiDiGraph) -> None:
        if graph.has_edge(from_id, to_id):
            if key is not None:
                graph.remove_edge(from_id, to_id, key)
            else:
                graph.remove_edge(from_id, to_id)
    return _m


def mutate_add_bridge_node(
    bridge_id: str,
    consumers: list[str],
    provider: str,
) -> Mutator:
    """Inject an interface/mock node so consumers depend on a stable bridge
    instead of routing directly through the bottleneck provider.
    """
    def _m(graph: nx.MultiDiGraph) -> None:
        graph.add_node(bridge_id, node_type="api_contract")
        for c in consumers:
            # Redirect consumer → bridge, and bridge → provider once.
            if graph.has_edge(c, provider):
                keys = list(graph[c][provider].keys())
                for k in keys:
                    graph.remove_edge(c, provider, k)
            graph.add_edge(c, bridge_id, edge_type="depends_on")
        graph.add_edge(bridge_id, provider, edge_type="depends_on")
    return _m


def mutate_split_node(node_id: str, clone_id: str | None = None) -> Mutator:
    """Split a bottleneck node, dividing its upstream consumers across two
    nodes that share the same downstream. This genuinely lowers the worst
    betweenness — the core relief for an overloaded contract/owner/service.
    """
    clone = clone_id or f"{node_id}-split"

    def _m(graph: nx.MultiDiGraph) -> None:
        if node_id not in graph:
            return
        downstream = [t for _, t in list(graph.out_edges(node_id))]
        consumers = [s for s, _ in list(graph.in_edges(node_id))]
        ntype = graph.nodes[node_id].get("node_type")
        graph.add_node(clone, node_type=ntype)
        # Move the second half of consumers onto the clone.
        for c in consumers[len(consumers) // 2:]:
            for k in list(graph[c][node_id].keys()):
                graph.remove_edge(c, node_id, k)
            graph.add_edge(c, clone, edge_type="depends_on")
        for d in downstream:
            graph.add_edge(clone, d, edge_type="depends_on")
    return _m


def mutate_reassign_owner(node_id: str, old_owner: str, new_owner: str) -> Mutator:
    """Move an ownership edge from one person to another."""
    def _m(graph: nx.MultiDiGraph) -> None:
        if graph.has_edge(old_owner, node_id):
            graph.remove_edge(old_owner, node_id)
        graph.add_node(new_owner, node_type="person")
        graph.add_edge(new_owner, node_id, edge_type="owns")
    return _m


def mutate_resequence_milestone(milestone_id: str, drop_dependency_id: str) -> Mutator:
    """Resequence a milestone by dropping one ordering dependency."""
    return mutate_remove_edge(milestone_id, drop_dependency_id)


# ── Simulation ──────────────────────────────────────────────────────

def _structural_risk(graph: nx.MultiDiGraph) -> float:
    """Scalar structural risk = severity of the worst bottleneck."""
    obs = betweenness_centrality(graph)
    return obs[0].value if obs else 0.0


def simulate_intervention(graph: nx.MultiDiGraph, mutator: Mutator) -> float:
    """Clone the graph, apply the mutator, re-measure risk, return the
    fractional risk reduction in [0, 1]. This is the prediction.
    """
    before = _structural_risk(graph)
    clone = graph.copy()
    mutator(clone)
    after = _structural_risk(clone)
    if before <= 0:
        return 0.0
    return max(0.0, (before - after) / before)


# ── Ranking & Recommendation ────────────────────────────────────────

def rank_options(options: list[InterventionOption]) -> list[InterventionOption]:
    """Rank options by tradeoff score (desc). Stable for equal scores."""
    return sorted(options, key=lambda o: o.tradeoff_score(), reverse=True)


def option_to_recommendation(option: InterventionOption) -> AgentRecommendation:
    """Turn a (typically top-ranked) option into an approval-gated
    AgentRecommendation, ready for ``deduplicate_recommendations()``.
    """
    return AgentRecommendation(
        id=f"rec-{option.id}" if option.id else "rec-intervention",
        recommendation_type=RecommendationType.FLAG_FOR_REVIEW.value,
        title=option.title or option.intervention_type.value.replace("_", " ").title(),
        recommendation_text=option.description,
        reasoning_summary=(
            f"Predicted structural risk reduction "
            f"{option.predicted_structural_risk_reduction:.0%}, "
            f"disruption={option.disruption_cost}, confidence={option.confidence:.2f}. "
            + "; ".join(option.tradeoffs)
        ),
        target_object_ids=[option.structural_signal_id] if option.structural_signal_id else [],
        approval_required=True,
        confidence=option.confidence,
    )
