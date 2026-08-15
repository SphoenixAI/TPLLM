"""Tests for Commit 25 — miscommunication risk detection."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from lltpm.scenarios import (
    LOST_CONTEXT_DEPTH_THRESHOLD,
    MiscommunicationRisk,
    MiscommunicationType,
    OrganizationalInteractionEdge,
    TeamBeliefState,
    assess_decision_propagation,
    calculate_belief_divergence,
    calculate_context_handoff_depth,
    detect_authority_confusion_risk,
    detect_belief_divergence_risk,
    detect_kpi_definition_mismatch,
    detect_lost_context_risk,
    detect_stale_propagation_risk,
    detect_terminology_ambiguity,
)

T0 = datetime(2026, 9, 15, 9, 0, 0)


def _belief(team, decision, claims=None):
    return TeamBeliefState(
        team_id=team, topic_id="contract-version",
        active_claim_ids=claims or [f"claim-{decision}"],
        assumed_decision_id=decision, as_of=T0,
    )


class TestBeliefDivergence:
    def test_conflicting_beliefs_produce_divergence(self):
        # Payments believes v3, Mobile believes v2, Platform conditional.
        states = [
            _belief("payments", "dec-v3"),
            _belief("mobile", "dec-v2"),
            _belief("platform", "dec-v3-conditional"),
        ]
        assert calculate_belief_divergence(states) > 0.5

    def test_aligned_beliefs_produce_no_divergence(self):
        states = [
            _belief("payments", "dec-v3", claims=["claim-a"]),
            _belief("mobile", "dec-v3", claims=["claim-a"]),
        ]
        assert calculate_belief_divergence(states) == 0.0

    def test_single_team_has_no_divergence(self):
        assert calculate_belief_divergence([_belief("payments", "dec-v3")]) == 0.0
        assert calculate_belief_divergence([]) == 0.0

    def test_partial_claim_overlap_registers(self):
        states = [
            _belief("a", "dec-v3", claims=["c1", "c2"]),
            _belief("b", "dec-v3", claims=["c1", "c3"]),
        ]
        divergence = calculate_belief_divergence(states)
        assert 0.0 < divergence < 0.5

    def test_divergence_risk_names_the_teams(self):
        risk = detect_belief_divergence_risk(
            states=[
                _belief("payments", "dec-v3"),
                _belief("mobile", "dec-v2"),
            ],
            topic="contract-version",
        )
        assert risk is not None
        assert risk.risk_type == (
            MiscommunicationType.CONFLICTING_TEAM_ASSUMPTIONS
        )
        assert set(risk.affected_entity_ids) == {"payments", "mobile"}
        assert "mobile assumes dec-v2" in risk.explanation

    def test_no_risk_when_aligned(self):
        assert detect_belief_divergence_risk(
            states=[
                _belief("a", "dec-v3", claims=["c"]),
                _belief("b", "dec-v3", claims=["c"]),
            ],
            topic="contract-version",
        ) is None


class TestDecisionPropagation:
    def test_comprehension_not_inferred_from_delivery(self):
        assessment = assess_decision_propagation(
            decision_id="dec-v3",
            expected_recipients=["payments", "mobile", "platform"],
            # Mobile still reflects the old decision; platform unknown.
            downstream_state_decisions={
                "payments": "dec-v3",
                "mobile": "dec-v2",
            },
        )
        assert assessment.confirmed_context_entity_ids == ["payments"]
        assert assessment.stale_context_entity_ids == ["mobile"]
        assert assessment.unknown_context_entity_ids == ["platform"]
        assert assessment.propagation_coverage == pytest.approx(
            1 / 3, abs=1e-4,  # coverage is rounded to 4dp for display
        )
        assert "not treated as comprehension" in assessment.explanation

    def test_full_propagation(self):
        assessment = assess_decision_propagation(
            decision_id="dec-v3",
            expected_recipients=["a", "b"],
            downstream_state_decisions={"a": "dec-v3", "b": "dec-v3"},
        )
        assert assessment.propagation_coverage == 1.0
        assert detect_stale_propagation_risk(
            assessment=assessment, topic="contract",
        ) is None

    def test_stale_propagation_risk_detected(self):
        assessment = assess_decision_propagation(
            decision_id="dec-v3",
            expected_recipients=["payments", "mobile"],
            downstream_state_decisions={
                "payments": "dec-v3", "mobile": "dec-v2",
            },
        )
        risk = detect_stale_propagation_risk(
            assessment=assessment, topic="contract-version",
        )
        assert risk is not None
        assert risk.risk_type == (
            MiscommunicationType.STALE_DECISION_PROPAGATION
        )
        assert "mobile" in risk.affected_entity_ids
        assert risk.probability == pytest.approx(0.5)

    def test_explicit_none_state_counts_as_unknown(self):
        assessment = assess_decision_propagation(
            decision_id="dec-v3", expected_recipients=["a"],
            downstream_state_decisions={"a": None},
        )
        assert assessment.unknown_context_entity_ids == ["a"]


class TestHandoffDepth:
    def test_depth_from_relay_chain(self):
        chain = ["requester", "team-lead-a", "manager-b", "decision-maker"]
        assert calculate_context_handoff_depth(relay_chain=chain) == 3

    def test_direct_ask_has_no_handoff(self):
        assert calculate_context_handoff_depth(
            relay_chain=["requester", "decision-maker"],
        ) == 1
        assert calculate_context_handoff_depth(relay_chain=["only"]) == 0

    def test_deep_handoff_increases_lost_context_risk(self):
        shallow = OrganizationalInteractionEdge(
            id="edge-shallow", source_entity_id="a", target_entity_id="b",
            interaction_type="request", handoff_depth=1,
        )
        deep = OrganizationalInteractionEdge(
            id="edge-deep", source_entity_id="a", target_entity_id="b",
            interaction_type="request", handoff_depth=4,
        )
        assert detect_lost_context_risk(
            edge=shallow, topic="contract",
        ) is None
        deep_risk = detect_lost_context_risk(edge=deep, topic="contract")
        assert deep_risk is not None
        assert deep_risk.probability > 0.5
        assert "4 handoff" in deep_risk.explanation

    def test_message_volume_is_not_modeled(self):
        # The edge model has no message-count field at all.
        fields = set(OrganizationalInteractionEdge.model_fields)
        for forbidden in ("message_count", "messages_sent", "reply_count",
                          "response_time", "responsiveness"):
            assert forbidden not in fields

    def test_threshold_is_explicit(self):
        assert LOST_CONTEXT_DEPTH_THRESHOLD == 2


class TestAuthorityConfusion:
    def test_unresolved_authority_with_candidates_is_risky(self):
        risk = detect_authority_confusion_risk(
            topic="contract-approval",
            candidate_authority_ids=["priya", "sam", "lee"],
            resolved_authority_id=None,
        )
        assert risk is not None
        assert risk.risk_type == MiscommunicationType.AUTHORITY_CONFUSION
        assert risk.probability > 0.5

    def test_resolved_authority_is_not_risky(self):
        assert detect_authority_confusion_risk(
            topic="contract-approval",
            candidate_authority_ids=["priya", "sam"],
            resolved_authority_id="priya",
        ) is None

    def test_single_candidate_is_not_confusion(self):
        assert detect_authority_confusion_risk(
            topic="contract-approval",
            candidate_authority_ids=["priya"],
            resolved_authority_id=None,
        ) is None


class TestKPIAndTerminology:
    def test_incompatible_units_detected(self):
        risk = detect_kpi_definition_mismatch(
            kpi_name="activation",
            definitions_by_team={
                "growth": {"unit": "percent"},
                "data": {"unit": "count"},
            },
        )
        assert risk is not None
        assert risk.risk_type == MiscommunicationType.KPI_DEFINITION_MISMATCH
        assert "percent" in risk.explanation and "count" in risk.explanation

    def test_matching_units_are_fine(self):
        assert detect_kpi_definition_mismatch(
            kpi_name="activation",
            definitions_by_team={
                "growth": {"unit": "percent"},
                "data": {"unit": "Percent"},
            },
        ) is None

    def test_terminology_ambiguity_detected(self):
        risk = detect_terminology_ambiguity(
            term="entitlement",
            meanings_by_entity={
                "billing": "a SKU grant",
                "platform": "an effective runtime permission",
            },
        )
        assert risk is not None
        assert risk.risk_type == MiscommunicationType.TERMINOLOGY_AMBIGUITY

    def test_single_meaning_is_unambiguous(self):
        assert detect_terminology_ambiguity(
            term="entitlement",
            meanings_by_entity={"billing": "a SKU grant",
                                "platform": "A SKU grant"},
        ) is None


class TestRiskModelInvariants:
    def test_probability_bounded(self):
        with pytest.raises(ValueError, match="probability"):
            MiscommunicationRisk(
                id="r", risk_type=MiscommunicationType.LOST_CONTEXT,
                topic="t", probability=1.5, probability_confidence=0.5,
                impact=0.5,
            )

    def test_probability_and_confidence_are_separate(self):
        risk = MiscommunicationRisk(
            id="r", risk_type=MiscommunicationType.LOST_CONTEXT,
            topic="t", probability=0.8, probability_confidence=0.2,
            impact=0.6,
        )
        assert risk.probability != risk.probability_confidence
