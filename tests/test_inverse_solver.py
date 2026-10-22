"""Counterfactual Replay + Local Inverse Solver.

A human override that worked out is treated as a counterexample that
generates ranked, contradiction-checked topological mismatch *hypotheses* —
never an auto-applied correction. These tests pin down: replay
reconstruction, bounded local search, candidate generation, contradiction
gating, rank-shift simulation, calibration adaptation, and — above all —
that nothing mutates the live graph or agent state.
"""

from __future__ import annotations

from lltpm.structural import (
    CandidateEditStatus,
    CandidateEditType,
    CandidateGraphEdit,
    EdgeType,
    HardConstraint,
    InterventionOption,
    InterventionType,
    NodeType,
    OntologyEdge,
    OntologyNode,
    TopologicalMismatch,
    TopologicalMismatchType,
    build_replay_comparison,
    calibration_hypothesis_from_topological_mismatch,
    contradiction_check,
    extract_local_subgraph,
    generate_override_reason_candidates,
    graph_from,
    run_local_inverse_solver,
)
from lltpm.structural.calibration import (
    CalibrationHypothesisStatus,
    CalibrationHypothesisType,
)
from lltpm.structural.feedback import HumanDecisionType, record_human_decision
from lltpm.structural.outcomes import DecisionOutcomeLog


# ── Fakes & helpers ─────────────────────────────────────────────────

class FakeStore:
    """Captures (program_id, artifact_name, model) saves for assertions."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, str, object]] = []

    def save(self, program_id: str, name: str, model: object) -> str:
        self.saved.append((program_id, name, model))
        return name


def _canonical_options() -> list[InterventionOption]:
    """Bridge clearly #1, Reviewer #2, Milestone #3 by tradeoff_score."""
    return [
        InterventionOption(
            id="bridge_injection",
            intervention_type=InterventionType.BRIDGE_INJECTION,
            predicted_structural_risk_reduction=0.9, confidence=0.9, disruption_cost="low",
        ),  # (0.9*0.9)/1 = 0.81
        InterventionOption(
            id="reviewer_redistribution",
            intervention_type=InterventionType.REVIEWER_REDISTRIBUTION,
            predicted_structural_risk_reduction=0.5, confidence=0.8, disruption_cost="medium",
        ),  # (0.5*0.8)/2 = 0.20
        InterventionOption(
            id="milestone_resequencing",
            intervention_type=InterventionType.MILESTONE_RESEQUENCING,
            predicted_structural_risk_reduction=0.55, confidence=0.6, disruption_cost="high",
        ),  # (0.55*0.6)/3 = 0.11
    ]


def _close_options() -> list[InterventionOption]:
    """Bridge only barely #1 — so a small edge nudge can also flip Reviewer."""
    return [
        InterventionOption(
            id="bridge_injection",
            intervention_type=InterventionType.BRIDGE_INJECTION,
            predicted_structural_risk_reduction=0.3, confidence=0.7, disruption_cost="low",
        ),  # 0.21
        InterventionOption(
            id="reviewer_redistribution",
            intervention_type=InterventionType.REVIEWER_REDISTRIBUTION,
            predicted_structural_risk_reduction=0.5, confidence=0.8, disruption_cost="medium",
        ),  # 0.20
        InterventionOption(
            id="milestone_resequencing",
            intervention_type=InterventionType.MILESTONE_RESEQUENCING,
            predicted_structural_risk_reduction=0.2, confidence=0.5, disruption_cost="high",
        ),  # 0.033
    ]


def _scenario_graph():
    """Four workstreams funnel through one API contract to a service."""
    nodes = [
        OntologyNode(id="contract-payments", node_type=NodeType.API_CONTRACT, label="Payments API"),
        OntologyNode(id="svc-payments", node_type=NodeType.SERVICE),
        *[OntologyNode(id=f"ws-{i}", node_type=NodeType.WORK_ITEM) for i in range(4)],
    ]
    edges = [
        OntologyEdge(id=f"e-ws{i}", from_node_id=f"ws-{i}", to_node_id="contract-payments", edge_type=EdgeType.DEPENDS_ON)
        for i in range(4)
    ]
    edges.append(
        OntologyEdge(id="e-contract", from_node_id="contract-payments", to_node_id="svc-payments", edge_type=EdgeType.DEPENDS_ON)
    )
    return graph_from(nodes, edges)


def _decision(reason_text: str = "Org policy forbids mocking this API in launch code"):
    """Human overrode the agent and chose reviewer_redistribution."""
    return record_human_decision(
        "rec-bridge",
        HumanDecisionType.OVERRIDDEN,
        intervention_option_id="reviewer_redistribution",
        signal_id="sig-bottleneck-payments",
        override_text=reason_text,
        agent_predicted_risk_reduction=0.9,
        agent_confidence=0.9,
        agent_severity="high",
    )


def _replay(options, decision, *, observed=0.6):
    outcome = DecisionOutcomeLog(
        id="dol-1", decision_record_id=decision.id, actual_risk_change=observed,
    )
    comparison = build_replay_comparison(
        human_decision_record=decision,
        decision_outcome_log=outcome,
        intervention_options=options,
    )
    return comparison, outcome


# ── 1. Human alternative triggers inverse solver ────────────────────

class TestSolverTriggers:
    def test_human_alternative_produces_mismatch_hypotheses(self):
        options = _canonical_options()
        decision = _decision()
        replay, _ = _replay(options, decision)

        assert replay.agent_top_intervention_id == "bridge_injection"
        assert replay.human_selected_intervention_id == "reviewer_redistribution"
        assert replay.original_human_option_rank == 2
        assert replay.human_option_outperformed_expectation is True

        result = run_local_inverse_solver(
            graph=_scenario_graph(),
            replay_comparison=replay,
            human_decision_record=decision,
            intervention_options=options,
            seed_node_ids=["contract-payments"],
        )
        assert result.mismatch_ids, "expected at least one mismatch hypothesis"
        assert result.selected_mismatch_id is not None


# ── 2. Minimal edit changes ranking ─────────────────────────────────

class TestMinimalEdit:
    def test_architecture_constraint_reranks_human_option(self):
        options = _canonical_options()
        decision = _decision()  # org-constraint override
        replay, _ = _replay(options, decision)

        # The override reason yields an ADD_CONSTRAINT candidate.
        override_candidates = generate_override_reason_candidates(human_decision_record=decision)
        assert any(c.edit_type == CandidateEditType.ADD_CONSTRAINT for c in override_candidates)

        store = FakeStore()
        result = run_local_inverse_solver(
            graph=_scenario_graph(),
            replay_comparison=replay,
            human_decision_record=decision,
            intervention_options=options,
            seed_node_ids=["contract-payments"],
            artifact_store=store,
            program_id="prog-1",
        )

        mismatches = [m for (_, n, m) in store.saved if n.startswith("mismatch_")]
        missing_constraint = [
            m for m in mismatches if m.mismatch_type == TopologicalMismatchType.MISSING_CONSTRAINT
        ]
        assert missing_constraint, "expected a MISSING_CONSTRAINT hypothesis"
        m = missing_constraint[0]
        assert m.after_rank is not None and m.before_rank is not None
        assert m.after_rank < m.before_rank  # human option ranked higher
        assert m.status == "hypothesis"


# ── 3. Solver does not mutate the live graph ────────────────────────

class TestNoMutation:
    def test_original_graph_is_untouched(self):
        graph = _scenario_graph()
        nodes_before = graph.number_of_nodes()
        edges_before = graph.number_of_edges()

        options = _close_options()
        decision = _decision()
        replay, _ = _replay(options, decision)

        run_local_inverse_solver(
            graph=graph,
            replay_comparison=replay,
            human_decision_record=decision,
            intervention_options=options,
            seed_node_ids=["contract-payments"],
        )

        assert graph.number_of_nodes() == nodes_before
        assert graph.number_of_edges() == edges_before
        # No sandbox marker leaked onto the live graph's edges.
        for _f, _t, data in graph.edges(data=True):
            assert "candidate_edit_id" not in data


# ── 4. Contradiction check blocks implausible edits ─────────────────

class TestContradictionGate:
    def test_forbidden_edge_type_is_blocked_and_not_promoted(self):
        store = FakeStore()
        options = _close_options()
        decision = _decision()
        replay, _ = _replay(options, decision)

        constraint = HardConstraint(
            id="hc-1", constraint_type="no_new_dependencies",
            forbidden_edge_type="depends_on",
        )
        result = run_local_inverse_solver(
            graph=_scenario_graph(),
            replay_comparison=replay,
            human_decision_record=decision,
            intervention_options=options,
            seed_node_ids=["contract-payments"],
            hard_constraints=[constraint],
            artifact_store=store,
            program_id="prog-1",
        )

        candidates = [m for (_, n, m) in store.saved if n.startswith("candidate_edit_")]
        failed = [c for c in candidates if c.status == CandidateEditStatus.CONTRADICTION_FAILED]
        assert failed, "expected at least one contradiction-failed candidate"

        # A failed candidate must never appear in a promoted mismatch.
        mismatches = [m for (_, n, m) in store.saved if n.startswith("mismatch_")]
        promoted_edit_ids = {eid for m in mismatches for eid in m.candidate_edit_ids}
        for c in failed:
            assert c.id not in promoted_edit_ids

    def test_contradiction_check_passes_clean_constraint_edit(self):
        edit = CandidateGraphEdit(
            id="cge-x", edit_type=CandidateEditType.ADD_CONSTRAINT,
            constraint_type="architecture_constraint",
        )
        result = contradiction_check(candidate_edit=edit, graph=_scenario_graph())
        assert result.passed is True
        assert result.severity == "none"


# ── 5. Local subgraph bound enforced ────────────────────────────────

class TestLocalBound:
    def test_search_stays_local(self):
        # A long dependency chain of 30 nodes.
        nodes = [OntologyNode(id=f"ws-{i}", node_type=NodeType.WORK_ITEM) for i in range(30)]
        edges = [
            OntologyEdge(id=f"e-{i}", from_node_id=f"ws-{i}", to_node_id=f"ws-{i+1}", edge_type=EdgeType.DEPENDS_ON)
            for i in range(29)
        ]
        big_graph = graph_from(nodes, edges)

        local = extract_local_subgraph(graph=big_graph, seed_node_ids=["ws-0"], radius=2)
        assert local.number_of_nodes() < big_graph.number_of_nodes()
        assert local.number_of_nodes() == 3  # ws-0, ws-1, ws-2

        options = _close_options()
        decision = _decision()
        replay, _ = _replay(options, decision)
        result = run_local_inverse_solver(
            graph=big_graph,
            replay_comparison=replay,
            human_decision_record=decision,
            intervention_options=options,
            seed_node_ids=["ws-0"],
            local_search_radius=2,
        )
        assert result.searched_node_count < big_graph.number_of_nodes()


# ── 6. Multiple hypotheses returned, ranked ─────────────────────────

class TestMultipleHypotheses:
    def test_multiple_ranked_mismatches(self):
        options = _close_options()  # bridge barely beats reviewer
        decision = _decision()
        replay, _ = _replay(options, decision)

        result = run_local_inverse_solver(
            graph=_scenario_graph(),
            replay_comparison=replay,
            human_decision_record=decision,
            intervention_options=options,
            seed_node_ids=["contract-payments"],
        )
        assert len(result.mismatch_ids) >= 2
        # selected_mismatch_id is the top-ranked one.
        assert result.selected_mismatch_id == result.mismatch_ids[0]


# ── 7. Human success is observed, never auto-applied ────────────────

class TestNoAutoApply:
    def test_outperforming_human_choice_stays_hypothesis(self):
        graph = _scenario_graph()
        edges_before = graph.number_of_edges()

        options = _canonical_options()
        decision = _decision()
        replay, _ = _replay(options, decision, observed=0.9)  # strongly outperformed
        assert replay.human_option_outperformed_expectation is True

        store = FakeStore()
        run_local_inverse_solver(
            graph=graph,
            replay_comparison=replay,
            human_decision_record=decision,
            intervention_options=options,
            seed_node_ids=["contract-payments"],
            artifact_store=store,
            program_id="prog-1",
        )

        mismatches = [m for (_, n, m) in store.saved if n.startswith("mismatch_")]
        assert mismatches
        for m in mismatches:
            assert m.status == "hypothesis"  # never "applied"
        # Graph still untouched despite the human "winning".
        assert graph.number_of_edges() == edges_before


# ── 8. Missing edge → calibration hypothesis ────────────────────────

class TestCalibrationFromMissingEdge:
    def test_missing_edge_maps_to_add_missing_edge_type(self):
        mismatch = TopologicalMismatch(
            id="mismatch-1", mismatch_type=TopologicalMismatchType.MISSING_EDGE, confidence=0.8,
        )
        hyp = calibration_hypothesis_from_topological_mismatch(mismatch=mismatch)
        assert hyp.hypothesis_type == CalibrationHypothesisType.ADD_MISSING_EDGE_TYPE
        assert hyp.status == CalibrationHypothesisStatus.HYPOTHESIS
        assert hyp.source_mismatch_id == "mismatch-1"
        assert hyp.confidence == 0.8


# ── 9. Missing constraint → calibration hypothesis ──────────────────

class TestCalibrationFromMissingConstraint:
    def test_missing_constraint_maps_to_add_constraint_type(self):
        mismatch = TopologicalMismatch(
            id="mismatch-2", mismatch_type=TopologicalMismatchType.MISSING_CONSTRAINT, confidence=0.7,
        )
        hyp = calibration_hypothesis_from_topological_mismatch(mismatch=mismatch)
        assert hyp.hypothesis_type == CalibrationHypothesisType.ADD_CONSTRAINT_TYPE
        assert hyp.status == CalibrationHypothesisStatus.HYPOTHESIS


# ── 10. Contradiction-failed candidate persisted, not promoted ──────

class TestFailedCandidatePersistence:
    def test_failed_candidate_saved_but_not_promoted(self):
        store = FakeStore()
        options = _close_options()
        decision = _decision()
        replay, _ = _replay(options, decision)

        constraint = HardConstraint(
            id="hc-1", constraint_type="no_new_dependencies", forbidden_edge_type="depends_on",
        )
        run_local_inverse_solver(
            graph=_scenario_graph(),
            replay_comparison=replay,
            human_decision_record=decision,
            intervention_options=options,
            seed_node_ids=["contract-payments"],
            hard_constraints=[constraint],
            artifact_store=store,
            program_id="prog-1",
        )

        saved_names = [n for (_, n, _) in store.saved]
        # Both the candidate edit and its contradiction result are persisted.
        assert any(n.startswith("candidate_edit_") for n in saved_names)
        assert any(n.startswith("contradiction_") for n in saved_names)

        candidates = [m for (_, n, m) in store.saved if n.startswith("candidate_edit_")]
        failed = [c for c in candidates if c.status == CandidateEditStatus.CONTRADICTION_FAILED]
        assert failed
        contradictions = [m for (_, n, m) in store.saved if n.startswith("contradiction_")]
        assert any(cr.passed is False for cr in contradictions)
