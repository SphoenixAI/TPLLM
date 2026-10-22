"""Criteria construction, scoring, and the
selection engine's abstention behavior."""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.coordination import (
    AgentRecommendationStatus,
    CommitmentType,
    DecisionCriterionType,
    DecisionRecommendationPolicy,
    DecisionRecommendationStrength,
    PositionType,
    WorkstreamCommitment,
    build_decision_criteria,
    option_is_eligible,
    score_decision_option,
    select_agent_recommendation,
)
from tests._coordination_fixtures import AS_OF, WS, actor, understanding
from tests._recommendation_fixtures import (
    POLICY,
    assessment,
    capsule,
    criteria,
    position,
    standard_positions,
)


def _select(assessments, crit=None, policy=None, unknowns=(), caps=None):
    return select_agent_recommendation(
        capsule=caps or capsule(),
        assessments=assessments,
        criteria=crit if crit is not None else criteria(),
        policy=policy or POLICY,
        critical_unknowns=list(unknowns),
        now=AS_OF,
    )


class TestCriteriaConstruction:
    def test_criteria_derive_from_context(self):
        built = build_decision_criteria(
            capsule=capsule(), understanding=understanding(),
            readiness=None, kpi_context=[], commitments=[],
        )
        types = {c.criterion_type for c in built}
        assert DecisionCriterionType.OBJECTIVE_ALIGNMENT in types
        assert DecisionCriterionType.EVIDENCE_STRENGTH in types
        # No KPI criterion invented when nothing links to a KPI... but
        # this capsule does carry KPI ids, so it should appear.
        assert DecisionCriterionType.KPI_IMPACT in types

    def test_weights_are_not_uniform(self):
        built = build_decision_criteria(
            capsule=capsule(), understanding=understanding(),
            readiness=None, kpi_context=[], commitments=[],
        )
        weights = {c.weight for c in built}
        assert len(weights) > 1, "criteria must not use identical weights"

    def test_external_commitment_becomes_hard_requirement(self):
        commitment = WorkstreamCommitment(
            id="cm-1", workstream_id=WS,
            commitment_type=CommitmentType.CUSTOMER,
            description="Customer delivery of graph authorization",
            target_date=AS_OF + timedelta(days=30), status="open",
            evidence_ids=["ev-commit"],
        )
        built = build_decision_criteria(
            capsule=capsule(), understanding=understanding(),
            readiness=None, kpi_context=[], commitments=[commitment],
        )
        commitment_criteria = [
            c for c in built
            if c.criterion_type == DecisionCriterionType.COMMITMENT_IMPACT
        ]
        assert commitment_criteria
        assert commitment_criteria[0].hard_requirement is True

    def test_internal_commitment_is_not_hard(self):
        commitment = WorkstreamCommitment(
            id="cm-2", workstream_id=WS,
            commitment_type=CommitmentType.INTERNAL_PRODUCT,
            description="Internal milestone", status="open",
            evidence_ids=["ev-2"],
        )
        built = build_decision_criteria(
            capsule=capsule(), understanding=understanding(),
            readiness=None, kpi_context=[], commitments=[commitment],
        )
        commitment_criteria = [
            c for c in built
            if c.criterion_type == DecisionCriterionType.COMMITMENT_IMPACT
        ]
        assert commitment_criteria[0].hard_requirement is False


class TestScoring:
    def test_weighted_mean(self):
        crit = criteria()
        score = score_decision_option(
            assessment=assessment(
                "opt-a", {"crit-0": 1.0, "crit-1": 0.0, "crit-2": 0.5},
            ),
            criteria=crit,
        )
        expected = (0.85 * 1.0 + 0.75 * 0.0 + 0.80 * 0.5) / (
            0.85 + 0.75 + 0.80
        )
        assert score == pytest.approx(expected)

    def test_unscored_criteria_are_skipped(self):
        assert score_decision_option(
            assessment=assessment("opt-a", {}), criteria=criteria(),
        ) == 0.0

    def test_hard_constraint_violation_makes_ineligible(self):
        violating = assessment("opt-b", violated=["security-review"])
        assert option_is_eligible(violating) is False
        assert option_is_eligible(assessment("opt-a")) is True


class TestSelection:
    def test_best_eligible_option_wins(self):
        result = _select([
            assessment("opt-a", {f"crit-{i}": 0.95 for i in range(3)}),
            assessment("opt-b", {f"crit-{i}": 0.30 for i in range(3)}),
        ])
        assert result.status == (
            AgentRecommendationStatus.RECOMMENDATION_AVAILABLE
        )
        assert result.recommended_option_id == "opt-a"
        assert result.strength == DecisionRecommendationStrength.STRONG

    def test_hard_constraint_violation_disqualifies_even_if_top_scoring(self):
        result = _select([
            assessment(
                "opt-a", {f"crit-{i}": 1.0 for i in range(3)},
                violated=["approved-launch-scope"],
            ),
            assessment("opt-b", {f"crit-{i}": 0.85 for i in range(3)}),
        ])
        assert result.recommended_option_id == "opt-b"
        assert "opt-a" in result.rationale.alternative_rejection_reasons
        assert "hard constraint" in " ".join(
            result.rationale.alternative_rejection_reasons["opt-a"]
        ).lower()

    def test_all_options_violating_requires_human_judgment(self):
        result = _select([
            assessment("opt-a", violated=["security"]),
            assessment("opt-b", violated=["regulatory"]),
        ])
        assert result.status == (
            AgentRecommendationStatus.HUMAN_JUDGMENT_REQUIRED
        )
        assert result.recommended_option_id is None

    def test_small_margin_causes_abstention(self):
        # 0.81 vs 0.79 with a 0.10 margin threshold.
        result = _select([
            assessment("opt-a", {f"crit-{i}": 0.81 for i in range(3)}),
            assessment("opt-b", {f"crit-{i}": 0.79 for i in range(3)}),
        ])
        assert result.status == AgentRecommendationStatus.CONTESTED
        assert result.recommended_option_id is None
        assert "separated by only" in result.rationale.summary

    def test_critical_unknown_blocks_recommendation(self):
        result = _select(
            [assessment("opt-a", {f"crit-{i}": 0.95 for i in range(3)})],
            unknowns=["Is mobile migration launch-blocking?"],
        )
        assert result.status == (
            AgentRecommendationStatus.HUMAN_JUDGMENT_REQUIRED
        )
        assert result.recommended_option_id is None
        assert "Is mobile migration launch-blocking?" in (
            result.rationale.unresolved_unknowns
        )

    def test_thin_evidence_causes_insufficient_evidence(self):
        result = _select([
            assessment(
                "opt-a", {f"crit-{i}": 0.9 for i in range(3)},
                evidence_confidence=0.2,
            ),
        ])
        assert result.status == (
            AgentRecommendationStatus.INSUFFICIENT_EVIDENCE
        )
        assert result.recommended_option_id is None

    def test_no_assessments_abstains(self):
        result = _select([])
        assert result.status == (
            AgentRecommendationStatus.INSUFFICIENT_EVIDENCE
        )

    def test_strength_thresholds(self):
        strong = _select([
            assessment("opt-a", {f"crit-{i}": 0.95 for i in range(3)},
                       evidence_confidence=0.95),
        ])
        moderate = _select([
            assessment("opt-a", {f"crit-{i}": 0.80 for i in range(3)},
                       evidence_confidence=0.95),
        ])
        assert strong.strength == DecisionRecommendationStrength.STRONG
        assert moderate.strength == DecisionRecommendationStrength.MODERATE

    def test_deterministic(self):
        args = [
            assessment("opt-a", {f"crit-{i}": 0.95 for i in range(3)}),
            assessment("opt-b", {f"crit-{i}": 0.40 for i in range(3)}),
        ]
        assert _select(args).model_dump() == _select(args).model_dump()


class TestEpistemicHonesty:
    def _recommendation(self):
        return _select([
            assessment("opt-a", {f"crit-{i}": 0.95 for i in range(3)}),
            assessment("opt-b", {f"crit-{i}": 0.40 for i in range(3)}),
        ])

    def test_evidence_against_is_shown(self):
        rationale = self._recommendation().rationale
        assert rationale.evidence_against_ids == ["ev-against-opt-a"]
        assert rationale.evidence_for_ids == ["ev-for-opt-a"]

    def test_dissent_is_preserved(self):
        rationale = self._recommendation().rationale
        assert any("Mobile" in d for d in rationale.dissenting_positions)

    def test_assumptions_and_rejection_reasons_recorded(self):
        rationale = self._recommendation().rationale
        assert rationale.assumptions
        assert "opt-b" in rationale.alternative_rejection_reasons

    def test_authority_and_recommendation_stay_separate(self):
        caps = capsule()
        result = _select(
            [assessment("opt-a", {f"crit-{i}": 0.95 for i in range(3)})],
            caps=caps,
        )
        # The recommendation names an option; it never names an authority.
        assert result.recommended_option_id == "opt-a"
        assert "decision_authority" not in type(result).model_fields
        # Authority remains a capsule concept.
        assert caps.decision_authority is not None

    def test_recommendation_may_contradict_authority_preference(self):
        # Platform (the authority) is conditional on A; Mobile prefers B.
        # If the evidence favors B, the agent recommends B anyway.
        result = _select([
            assessment("opt-a", {f"crit-{i}": 0.30 for i in range(3)}),
            assessment("opt-b", {f"crit-{i}": 0.95 for i in range(3)}),
        ])
        assert result.recommended_option_id == "opt-b"
        # And the authority is still recorded, unchanged, on the capsule.
        assert capsule().decision_authority.actor_id == "priya"

    def test_confidence_scaled_by_evidence_never_one(self):
        result = _select([
            assessment("opt-a", {f"crit-{i}": 1.0 for i in range(3)},
                       evidence_confidence=1.0),
        ])
        assert result.confidence <= 0.99
        assert "capped below certainty" in (
            result.rationale.confidence_explanation
        )

    def test_dissent_policy_can_block_when_configured(self):
        strict = DecisionRecommendationPolicy(
            allow_recommendation_with_dissent=False,
        )
        result = _select(
            [assessment("opt-a", {f"crit-{i}": 0.95 for i in range(3)})],
            policy=strict,
        )
        assert result.status == AgentRecommendationStatus.CONTESTED

    def test_no_dissent_when_positions_all_support(self):
        supportive = [
            position("p1", actor("dana", team="payments"),
                     PositionType.SUPPORT, "Ready."),
        ]
        result = _select(
            [assessment("opt-a", {f"crit-{i}": 0.95 for i in range(3)})],
            caps=capsule(positions=supportive),
        )
        assert result.rationale.dissenting_positions == []
