"""Tests for Commit 23 — decision lineage, multi-hop supersession, and
cycle rejection."""

from __future__ import annotations

import pytest

from lltpm.temporal import (
    SupersessionCycleError,
    detect_supersession_cycle,
    trace_decision_lineage,
    validate_lineage,
    what_was_authoritative_at,
    why_is_this_currently_authoritative,
)
from tests._temporal_fixtures import at, node


class TestSimpleLineage:
    def test_single_conclusive_decision_is_authoritative(self):
        lineage = trace_decision_lineage(nodes=[node("dec-1")])
        assert lineage.current_authoritative_decision_id == "dec-1"
        assert "never superseded" in lineage.explanation

    def test_superseded_decision_is_not_authoritative(self):
        nodes = [
            node("dec-1", status="superseded", superseded_by="dec-2",
                 effective_from=at(0)),
            node("dec-2", effective_from=at(5)),
        ]
        lineage = trace_decision_lineage(nodes=nodes)
        assert lineage.current_authoritative_decision_id == "dec-2"

    def test_reopened_decision_is_not_authoritative(self):
        lineage = trace_decision_lineage(
            nodes=[node("dec-1", status="reopened", reopened_at=at(1))],
        )
        assert lineage.current_authoritative_decision_id is None
        assert "reopened" in lineage.explanation

    def test_not_yet_effective_decision_excluded(self):
        nodes = [
            node("dec-1", effective_from=at(0)),
            node("dec-2", effective_from=at(10)),
        ]
        assert what_was_authoritative_at(nodes=nodes, as_of=at(5)) == "dec-1"
        assert what_was_authoritative_at(nodes=nodes, as_of=at(11)) == "dec-2"


class TestMultiHopSupersession:
    def _chain(self, reopen_c: bool = False):
        """A superseded by B, B superseded by C."""
        return [
            node("A", status="superseded", superseded_by="B",
                 effective_from=at(0)),
            node("B", status="superseded", superseded_by="C",
                 effective_from=at(5)),
            node(
                "C",
                status="reopened" if reopen_c else "conclusive",
                reopened_at=at(20) if reopen_c else None,
                effective_from=at(10),
            ),
        ]

    def test_deepest_conclusive_wins(self):
        lineage = trace_decision_lineage(nodes=self._chain())
        assert lineage.current_authoritative_decision_id == "C"

    def test_reopening_c_inspects_the_chain_not_just_b(self):
        # C reopened. B is a candidate for restoration, but the chain is
        # inspected rather than blindly restoring the immediate parent.
        lineage = trace_decision_lineage(nodes=self._chain(reopen_c=True))
        assert lineage.current_authoritative_decision_id == "B"
        assert "reopened" in lineage.explanation
        assert "no later decision holds authority" in lineage.explanation

    def test_a_not_restored_while_b_holds(self):
        lineage = trace_decision_lineage(nodes=self._chain(reopen_c=True))
        # A stays superseded by B; only one decision is authoritative.
        assert lineage.current_authoritative_decision_id != "A"

    def test_whole_chain_reopened_leaves_nothing_authoritative(self):
        nodes = [
            node("A", status="reopened", reopened_at=at(21),
                 superseded_by="B", effective_from=at(0)),
            node("B", status="reopened", reopened_at=at(20),
                 superseded_by="C", effective_from=at(5)),
            node("C", status="reopened", reopened_at=at(20),
                 effective_from=at(10)),
        ]
        lineage = trace_decision_lineage(nodes=nodes)
        assert lineage.current_authoritative_decision_id is None

    def test_lineage_preserves_all_nodes(self):
        lineage = trace_decision_lineage(nodes=self._chain())
        assert len(lineage.nodes) == 3


class TestCycleRejection:
    def test_two_node_cycle_detected(self):
        nodes = [
            node("A", superseded_by="B"),
            node("B", superseded_by="A"),
        ]
        assert detect_supersession_cycle(nodes)
        with pytest.raises(SupersessionCycleError, match="cycle"):
            validate_lineage(nodes)
        with pytest.raises(SupersessionCycleError, match="reopen semantics"):
            trace_decision_lineage(nodes=nodes)

    def test_three_node_cycle_detected(self):
        nodes = [
            node("A", superseded_by="B"),
            node("B", superseded_by="C"),
            node("C", superseded_by="A"),
        ]
        with pytest.raises(SupersessionCycleError):
            validate_lineage(nodes)

    def test_legal_chain_has_no_cycle(self):
        nodes = [
            node("A", superseded_by="B"),
            node("B", superseded_by="C"),
            node("C"),
        ]
        assert detect_supersession_cycle(nodes) == []
        validate_lineage(nodes)


class TestOriginPreservation:
    def test_agent_recommendation_distinct_from_final_decision(self):
        nodes = [
            node("dec-1", origin="human_validated_agent_recommendation"),
        ]
        lineage = trace_decision_lineage(nodes=nodes)
        node_out = lineage.nodes[0]
        # The recommendation origin survives in lineage.
        assert node_out.decision_origin == (
            "human_validated_agent_recommendation"
        )
        assert "human_validated_agent_recommendation" in lineage.explanation

    def test_human_modification_retained(self):
        nodes = [node("dec-1", origin="human_modified_agent_recommendation")]
        assert "human_modified_agent_recommendation" in (
            trace_decision_lineage(nodes=nodes).explanation
        )

    def test_autonomous_origin_retained_and_distinct(self):
        nodes = [node("dec-1", origin="agent_autonomous_under_policy")]
        lineage = trace_decision_lineage(nodes=nodes)
        assert lineage.nodes[0].decision_origin == (
            "agent_autonomous_under_policy"
        )
        assert "agent_autonomous_under_policy" in lineage.explanation


class TestExplanationReproducibility:
    def test_explanation_is_reproducible(self):
        nodes = [
            node("A", status="superseded", superseded_by="B",
                 effective_from=at(0)),
            node("B", effective_from=at(5)),
        ]
        first = why_is_this_currently_authoritative(nodes=nodes)
        second = why_is_this_currently_authoritative(nodes=nodes)
        assert first == second
        assert "B is authoritative" in first

    def test_empty_lineage_explains_itself(self):
        lineage = trace_decision_lineage(nodes=[])
        assert lineage.current_authoritative_decision_id is None
        assert lineage.explanation
