"""Structural engine — orchestration over the metric/signal/intervention layers.

Mirrors the existing ``CrossProgramIntel`` / ``RiskEngine`` shape: construct
with an ``ArtifactStore``, expose ``analyze`` and ``propose_interventions``.
It wires structural findings into the SAME closed loop the rest of LLTPM uses —
metrics → structural signals → (validation gate) → ranked intervention options
→ deduped, approval-gated recommendations — rather than standing up a parallel
system.

``analyze_graph`` is the pure, testable core (graph in, signals out).
``analyze(program_id)`` is the store-backed convenience that re-projects a
program's ontology entities when they're available.
"""

from __future__ import annotations

import networkx as nx

from lltpm.artifacts.store import ArtifactStore
from lltpm.ontology.entities import AuthorizedBridge
from lltpm.ontology.measurement import HealthStatus, InsightSeverity
from lltpm.structural.graph import NodeType, node_type_of
from lltpm.structural.intervention import (
    InterventionOption,
    InterventionType,
    rank_options,
    simulate_intervention,
    mutate_split_node,
)
from lltpm.structural.metrics import (
    GraphMetricObservation,
    betweenness_centrality,
    review_concentration,
    scope_variance,
)
from lltpm.structural.ownership import OwnershipContext
from lltpm.structural.signals import StructuralSignal, StructuralSignalType


# Severity bands keyed off the metric interpretation.
_HEALTH_TO_SEVERITY: dict[HealthStatus, InsightSeverity] = {
    HealthStatus.HEALTHY: InsightSeverity.LOW,
    HealthStatus.WATCH: InsightSeverity.MEDIUM,
    HealthStatus.WARNING: InsightSeverity.HIGH,
    HealthStatus.CRITICAL: InsightSeverity.CRITICAL,
}


class StructuralEngine:
    """Turns a program's structural graph into explainable signals and a
    ranked menu of interventions."""

    def __init__(self, store: ArtifactStore | None = None) -> None:
        self._store = store

    # ── Pure core ──────────────────────────────────────────────

    def analyze_graph(
        self,
        graph: nx.MultiDiGraph,
        ownership_contexts: list[OwnershipContext] | None = None,
        modified_by_person: dict[str, list[str]] | None = None,
        bridges: list[AuthorizedBridge] | None = None,
    ) -> list[StructuralSignal]:
        """Analyze a structural graph and return explainable signals.

        Detects bottleneck nodes (betweenness), scope drift (per-person scope
        variance, bridge-suppressed), and reviewer concentration.
        """
        signals: list[StructuralSignal] = []
        signals.extend(self._bottleneck_signals(graph))
        signals.extend(
            self._scope_drift_signals(
                graph, ownership_contexts or [], modified_by_person or {}, bridges or []
            )
        )
        rc_signal = self._review_concentration_signal(graph)
        if rc_signal:
            signals.append(rc_signal)
        return signals

    def _bottleneck_signals(self, graph: nx.MultiDiGraph) -> list[StructuralSignal]:
        obs = betweenness_centrality(graph)
        signals: list[StructuralSignal] = []
        for o in obs:
            # Betweenness is size-normalized and runs small even for genuine
            # bottlenecks, so flag from WATCH up (scope/review use WARNING+).
            if o.value > 0 and o.interpretation in (
                HealthStatus.WATCH, HealthStatus.WARNING, HealthStatus.CRITICAL
            ):
                ntype = node_type_of(graph, o.measured_node_id)
                is_person = ntype == NodeType.PERSON
                label = graph.nodes[o.measured_node_id].get("label") or o.measured_node_id
                signals.append(
                    StructuralSignal(
                        id=f"sig-bottleneck-{o.measured_node_id}",
                        signal_type=StructuralSignalType.BOTTLENECK_NODE,
                        affected_object_ids=[o.measured_node_id],
                        graph_metric_observation_ids=[o.id],
                        summary=(
                            f"{'Person' if is_person else (getattr(ntype, 'value', 'node')).title()} "
                            f"'{label}' is a structural bottleneck "
                            f"(betweenness {o.value:.2f})."
                        ),
                        severity=_HEALTH_TO_SEVERITY.get(o.interpretation, InsightSeverity.MEDIUM),
                        confidence=o.confidence,
                    )
                )
        return signals

    def _scope_drift_signals(
        self,
        graph: nx.MultiDiGraph,
        contexts: list[OwnershipContext],
        modified_by_person: dict[str, list[str]],
        bridges: list[AuthorizedBridge],
    ) -> list[StructuralSignal]:
        signals: list[StructuralSignal] = []
        for ctx in contexts:
            modified = modified_by_person.get(ctx.person_id, [])
            if not modified:
                continue
            o = scope_variance(graph, ctx, modified, bridges=bridges)
            if o.interpretation in (HealthStatus.WARNING, HealthStatus.CRITICAL):
                signals.append(
                    StructuralSignal(
                        id=f"sig-scope-{ctx.person_id}",
                        signal_type=StructuralSignalType.SCOPE_DRIFT,
                        affected_object_ids=[ctx.person_id, *modified],
                        graph_metric_observation_ids=[o.id],
                        summary=(
                            f"{ctx.person_id} is modifying work far from their "
                            f"baseline ownership (scope variance {o.value:.1f})."
                        ),
                        severity=_HEALTH_TO_SEVERITY.get(o.interpretation, InsightSeverity.MEDIUM),
                        confidence=o.confidence,
                    )
                )
        return signals

    def _review_concentration_signal(
        self, graph: nx.MultiDiGraph
    ) -> StructuralSignal | None:
        o = review_concentration(graph)
        if o.interpretation not in (HealthStatus.WARNING, HealthStatus.CRITICAL):
            return None
        return StructuralSignal(
            id="sig-review-concentration",
            signal_type=StructuralSignalType.REVIEWER_CONCENTRATION,
            affected_object_ids=[o.measured_node_id],
            graph_metric_observation_ids=[o.id],
            summary=(
                f"Review load funnels through {o.measured_node_id} "
                f"({o.value:.0%} of reviewed workstreams)."
            ),
            severity=_HEALTH_TO_SEVERITY.get(o.interpretation, InsightSeverity.MEDIUM),
            confidence=o.confidence,
        )

    # ── Intervention proposals ─────────────────────────────────

    def propose_interventions(
        self,
        signal: StructuralSignal,
        graph: nx.MultiDiGraph | None = None,
    ) -> list[InterventionOption]:
        """Propose a ranked matrix of interventions for a signal.

        For bottleneck signals, simulates a bridge-injection on a clone of the
        graph to predict the actual structural risk reduction.
        """
        options: list[InterventionOption] = []

        if signal.signal_type == StructuralSignalType.BOTTLENECK_NODE and graph is not None:
            target = signal.affected_object_ids[0] if signal.affected_object_ids else ""
            consumers = [frm for frm, _ in graph.in_edges(target)] if target in graph else []
            predicted = 0.0
            if consumers:
                # Simulate splitting the bottleneck across two nodes and measure
                # the real reduction in worst-case betweenness.
                predicted = simulate_intervention(graph, mutate_split_node(target))
            options.append(
                InterventionOption(
                    id=f"opt-split-{target}",
                    structural_signal_id=signal.id,
                    intervention_type=InterventionType.DEPENDENCY_SPLIT,
                    title=f"Split the load on {target}",
                    description=(
                        f"Divide the {len(consumers)} consumers across two owners/"
                        f"contracts so they no longer all funnel through {target}."
                    ),
                    predicted_structural_risk_reduction=predicted,
                    disruption_cost="medium",
                    confidence=0.7,
                    tradeoffs=[
                        "Decouples consumers from the single bottleneck",
                        "Requires defining a clean split boundary",
                    ],
                )
            )
            options.append(
                InterventionOption(
                    id=f"opt-reviewer-{target}",
                    structural_signal_id=signal.id,
                    intervention_type=InterventionType.REVIEWER_REDISTRIBUTION,
                    title=f"Redistribute load away from {target}",
                    description="Spread the dependent work across more owners.",
                    predicted_structural_risk_reduction=max(0.0, predicted * 0.6),
                    disruption_cost="low",
                    confidence=0.6,
                    tradeoffs=["Lower disruption", "Slower structural relief"],
                )
            )

        elif signal.signal_type == StructuralSignalType.SCOPE_DRIFT:
            options.append(
                InterventionOption(
                    id=f"opt-clarify-{signal.id}",
                    structural_signal_id=signal.id,
                    intervention_type=InterventionType.OWNERSHIP_CLARIFICATION,
                    title="Clarify ownership / authorize the bridge",
                    description=(
                        "Either record an explicit AuthorizedBridge if this is "
                        "planned cross-team work, or reassign to the domain owner."
                    ),
                    predicted_structural_risk_reduction=0.5,
                    disruption_cost="low",
                    confidence=0.65,
                    tradeoffs=["Low cost", "Resolves false vs. real drift"],
                )
            )

        return rank_options(options)
