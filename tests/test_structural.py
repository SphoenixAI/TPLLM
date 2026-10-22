"""Tests for the Structural Intelligence Layer.

Covers the graph projection, the five topology metrics, ownership-context
false-positive suppression, the structural-signal → validation-gate reuse,
and the tradeoff-ranked intervention matrix.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tpllm.ontology.entities import (
    AuthorizedBridge,
    BridgeReason,
    Dependency,
    Milestone,
    WorkItem,
)
from tpllm.ontology.measurement import InsightSeverity
from tpllm.ontology.validation import ValidationOutcome, validate_insight
from tpllm.structural import (
    EdgeType,
    InterventionOption,
    InterventionType,
    NodeType,
    OntologyEdge,
    OntologyNode,
    OwnershipContext,
    StructuralEngine,
    StructuralSignal,
    StructuralSignalType,
    active_context_entropy,
    betweenness_centrality,
    dependency_blast_radius,
    graph_from,
    node_type_of,
    project_to_graph,
    rank_options,
    scope_variance,
    signal_to_insight,
    top_bottleneck,
)


# ── Helpers ─────────────────────────────────────────────────────────

def _wi_node(node_id: str, domain: str = "") -> OntologyNode:
    props = {"domain": domain} if domain else {}
    return OntologyNode(
        id=node_id, node_type=NodeType.WORK_ITEM, canonical_object_id=node_id,
        properties=props,
    )


def _edge(frm: str, to: str, etype: EdgeType) -> OntologyEdge:
    return OntologyEdge(
        id=f"e-{frm}-{to}", from_node_id=frm, to_node_id=to, edge_type=etype
    )


def _has_edge_of_type(graph, frm: str, to: str, etype: EdgeType) -> bool:
    data = graph.get_edge_data(frm, to)
    if not data:
        return False
    return any(
        getattr(d.get("edge_type"), "value", d.get("edge_type")) == etype.value
        for d in data.values()
    )


# ── 10. Graph Projection ────────────────────────────────────────────

class TestGraphProjection:
    def test_graph_projection_from_ontology_entities(self):
        wi = WorkItem(
            id="wi-1", title="Build API",
            blocks=["wi-2"], dependency_ids=["wi-3"],
        )
        dep = Dependency(
            id="dep-1", title="Security review",
            dependent_work_item_ids=["wi-1"], upstream_team_id="team-sec",
        )
        ms = Milestone(id="ms-1", name="Beta", dependency_milestone_ids=["ms-2"])

        graph = project_to_graph([wi, dep, ms])

        # Nodes carry the right types.
        assert node_type_of(graph, "wi-1") == NodeType.WORK_ITEM
        assert node_type_of(graph, "dep-1") == NodeType.DEPENDENCY
        assert node_type_of(graph, "ms-1") == NodeType.MILESTONE
        assert node_type_of(graph, "team-sec") == NodeType.TEAM

        # Edges carry the right types.
        assert _has_edge_of_type(graph, "wi-1", "wi-2", EdgeType.BLOCKS)
        assert _has_edge_of_type(graph, "wi-1", "wi-3", EdgeType.DEPENDS_ON)
        assert _has_edge_of_type(graph, "dep-1", "wi-1", EdgeType.BLOCKS)
        assert _has_edge_of_type(graph, "ms-1", "ms-2", EdgeType.DEPENDS_ON)


# ── 9. Blast Radius ─────────────────────────────────────────────────

class TestBlastRadius:
    def test_blast_radius_counts_downstream_dependents(self):
        # wi-1 → wi-2 → wi-3 → wi-4, and wi-2 → wi-5 (all BLOCKS edges).
        nodes = [_wi_node(f"wi-{i}") for i in range(1, 6)]
        edges = [
            _edge("wi-1", "wi-2", EdgeType.BLOCKS),
            _edge("wi-2", "wi-3", EdgeType.BLOCKS),
            _edge("wi-3", "wi-4", EdgeType.BLOCKS),
            _edge("wi-2", "wi-5", EdgeType.BLOCKS),
        ]
        graph = graph_from(nodes, edges)

        obs = dependency_blast_radius(graph, "wi-1")
        # wi-2, wi-3, wi-4, wi-5 are all downstream of wi-1.
        assert obs.value == 4.0

        # A leaf has zero blast radius.
        assert dependency_blast_radius(graph, "wi-4").value == 0.0


# ── 6. Bottleneck Is Not Always A Person ────────────────────────────

class TestBottleneck:
    def test_bottleneck_node_is_not_always_a_person(self):
        # Four consumers route through one API contract to reach a provider.
        contract = OntologyNode(
            id="contract-payments", node_type=NodeType.API_CONTRACT,
            canonical_object_id="contract-payments", label="Payments API contract",
        )
        provider = OntologyNode(
            id="svc-payments", node_type=NodeType.SERVICE,
            canonical_object_id="svc-payments",
        )
        consumers = [_wi_node(f"ws-{i}") for i in range(4)]
        nodes = [contract, provider, *consumers]
        edges = [_edge(f"ws-{i}", "contract-payments", EdgeType.DEPENDS_ON) for i in range(4)]
        edges.append(_edge("contract-payments", "svc-payments", EdgeType.DEPENDS_ON))

        graph = graph_from(nodes, edges)

        top = top_bottleneck(graph)
        assert top is not None
        # The bottleneck is the contract — a non-person node.
        assert top.measured_node_id == "contract-payments"
        assert node_type_of(graph, top.measured_node_id) == NodeType.API_CONTRACT
        assert node_type_of(graph, top.measured_node_id) != NodeType.PERSON


# ── 1–3. Scope Variance / Drift ─────────────────────────────────────

class TestScopeVariance:
    def test_baseline_ownership_prevents_false_drift(self):
        graph = graph_from([_wi_node("comp-a")], [])
        ctx = OwnershipContext(person_id="p1", baseline_owned_node_ids=["comp-a"])

        obs = scope_variance(graph, ctx, modified_node_ids=["comp-a"])
        assert obs.value == 0.0
        assert obs.interpretation.value == "healthy"

    def test_cross_domain_pr_creates_scope_drift(self):
        graph = graph_from(
            [_wi_node("frontend", "frontend"), _wi_node("payments", "payments")], []
        )
        ctx = OwnershipContext(person_id="p1", baseline_owned_node_ids=["frontend"])

        obs = scope_variance(graph, ctx, modified_node_ids=["payments"])
        assert obs.value >= 4.0  # unreachable, unrelated domain
        assert obs.interpretation.value in ("warning", "critical")

        # The engine turns the high variance into a scope_drift signal.
        engine = StructuralEngine()
        signals = engine.analyze_graph(
            graph,
            ownership_contexts=[ctx],
            modified_by_person={"p1": ["payments"]},
        )
        assert any(s.signal_type == StructuralSignalType.SCOPE_DRIFT for s in signals)

    def test_temporary_authorized_ownership_suppresses_false_alarm(self):
        graph = graph_from(
            [_wi_node("frontend", "frontend"), _wi_node("payments", "payments")], []
        )
        # Same cross-domain modification — but explicitly authorized.
        ctx = OwnershipContext(
            person_id="p1",
            baseline_owned_node_ids=["frontend"],
            temporary_authorized_node_ids=["payments"],
        )
        obs = scope_variance(graph, ctx, modified_node_ids=["payments"])
        assert obs.value == 0.0
        assert obs.interpretation.value == "healthy"

        # No scope-drift signal should fire.
        engine = StructuralEngine()
        signals = engine.analyze_graph(
            graph, ownership_contexts=[ctx], modified_by_person={"p1": ["payments"]}
        )
        assert not any(s.signal_type == StructuralSignalType.SCOPE_DRIFT for s in signals)

    def test_authorized_bridge_also_suppresses_drift(self):
        graph = graph_from(
            [_wi_node("frontend", "frontend"), _wi_node("payments", "payments")], []
        )
        ctx = OwnershipContext(person_id="p1", baseline_owned_node_ids=["frontend"])
        bridge = AuthorizedBridge(
            id="ab-1", person_id="p1", authorized_node_ids=["payments"],
            reason=BridgeReason.MIGRATION_LEAD.value,
            valid_from=datetime.utcnow() - timedelta(days=1),
            valid_until=datetime.utcnow() + timedelta(days=30),
        )
        obs = scope_variance(graph, ctx, modified_node_ids=["payments"], bridges=[bridge])
        assert obs.value == 0.0


# ── 4–5. Context Entropy ────────────────────────────────────────────

class TestContextEntropy:
    def test_local_high_volume_work_stays_low_entropy(self):
        nodes = [_wi_node(f"wi-{i}", "payments") for i in range(5)]
        graph = graph_from(nodes, [])
        obs = active_context_entropy(graph, "p1", [n.id for n in nodes])
        assert obs.value == 0.0  # all one cluster

    def test_low_volume_cross_domain_work_is_high_entropy(self):
        nodes = [_wi_node("wi-pay", "payments"), _wi_node("wi-fe", "frontend")]
        graph = graph_from(nodes, [])
        obs = active_context_entropy(graph, "p1", ["wi-pay", "wi-fe"])
        # Two evenly-split clusters → 1 bit, the max for two categories.
        assert obs.value >= 0.99


# ── 7. Intervention Matrix Ranking ──────────────────────────────────

class TestInterventionMatrix:
    def test_intervention_matrix_ranks_by_tradeoff(self):
        # opt-a: biggest raw risk reduction, but disruptive + low confidence.
        opt_a = InterventionOption(
            id="opt-a", intervention_type=InterventionType.DEPENDENCY_SPLIT,
            predicted_structural_risk_reduction=0.9, confidence=0.5,
            disruption_cost="high",
        )
        # opt-b: modest reduction, but cheap + confident → best tradeoff.
        opt_b = InterventionOption(
            id="opt-b", intervention_type=InterventionType.OWNERSHIP_CLARIFICATION,
            predicted_structural_risk_reduction=0.6, confidence=0.9,
            disruption_cost="low",
        )
        opt_c = InterventionOption(
            id="opt-c", intervention_type=InterventionType.REVIEWER_REDISTRIBUTION,
            predicted_structural_risk_reduction=0.7, confidence=0.7,
            disruption_cost="medium",
        )

        ranked = rank_options([opt_a, opt_b, opt_c])

        # The balanced option wins, not the one with max raw risk reduction.
        assert ranked[0].id == "opt-b"
        max_raw = max([opt_a, opt_b, opt_c],
                      key=lambda o: o.predicted_structural_risk_reduction)
        assert max_raw.id == "opt-a"
        assert ranked[0].id != max_raw.id


# ── 8. Signal → Validation Gate Reuse ───────────────────────────────

class TestSignalThroughValidationGate:
    def test_structural_signal_flows_through_validation_gate(self):
        signal = StructuralSignal(
            id="sig-bottleneck-contract",
            signal_type=StructuralSignalType.BOTTLENECK_NODE,
            affected_object_ids=["contract-payments"],
            graph_metric_observation_ids=["gm-1"],
            evidence_claim_ids=["ec-1"],
            summary="Payments API contract is the bottleneck for 4 workstreams.",
            severity=InsightSeverity.MEDIUM,
            confidence=0.9,
        )

        insight = signal_to_insight(signal)
        # The adapter wires graph evidence into the insight's evidence slots.
        assert insight.related_kpi_observation_ids == ["gm-1"]
        assert insight.supporting_evidence_claim_ids == ["ec-1"]

        # And it passes the SAME validation gate as any KPI insight.
        result = validate_insight(insight)
        assert result.passed is True
        assert result.outcome == ValidationOutcome.APPROVED

    def test_structural_signal_without_evidence_is_rejected_by_gate(self):
        # No metric observations, no evidence claims — the gate must reject it
        # exactly as it would any unevidenced insight.
        signal = StructuralSignal(
            id="sig-empty",
            signal_type=StructuralSignalType.BOTTLENECK_NODE,
            summary="Hunch with no backing.",
            severity=InsightSeverity.MEDIUM,
            confidence=0.95,
        )
        insight = signal_to_insight(signal)
        result = validate_insight(insight)
        assert result.passed is False
        assert result.outcome == ValidationOutcome.INSUFFICIENT_EVIDENCE
        assert result.failed_check == "evidence_sufficiency"


# ── Engine smoke: bottleneck → signal → intervention matrix ─────────

class TestEngineEndToEnd:
    def test_bottleneck_produces_ranked_interventions(self):
        contract = OntologyNode(
            id="contract-payments", node_type=NodeType.API_CONTRACT,
            label="Payments API",
        )
        provider = OntologyNode(id="svc-payments", node_type=NodeType.SERVICE)
        consumers = [_wi_node(f"ws-{i}") for i in range(4)]
        edges = [_edge(f"ws-{i}", "contract-payments", EdgeType.DEPENDS_ON) for i in range(4)]
        edges.append(_edge("contract-payments", "svc-payments", EdgeType.DEPENDS_ON))
        graph = graph_from([contract, provider, *consumers], edges)

        engine = StructuralEngine()
        signals = engine.analyze_graph(graph)
        bottlenecks = [s for s in signals if s.signal_type == StructuralSignalType.BOTTLENECK_NODE]
        assert bottlenecks, "expected a bottleneck signal"

        options = engine.propose_interventions(bottlenecks[0], graph=graph)
        assert options  # a ranked, non-empty option matrix
        assert all(o.execution_mode == "suggest_only" for o in options)
