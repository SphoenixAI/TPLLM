"""Recommendation models and their invariants."""

from __future__ import annotations

from datetime import datetime

import pytest

from lltpm.coordination import (
    AgentDecisionRecommendation,
    AgentRecommendationStatus,
    DecisionCriterion,
    DecisionCriterionType,
    DecisionEvaluationAction,
    DecisionOptionAssessment,
    DecisionRationale,
    DecisionRecommendationPolicy,
    HumanDecisionEvaluation,
)
from lltpm.core import ActorReference
from tests._coordination_fixtures import AS_OF, PROJECT


def _rec(**overrides):
    defaults = dict(
        id="rec-1", project_id=PROJECT, decision_capsule_id="capsule-1",
        decision_question="Approve contract v3 for enterprise accounts?",
        status=AgentRecommendationStatus.RECOMMENDATION_AVAILABLE,
        recommended_option_id="opt-a", confidence=0.86,
        assessments=[DecisionOptionAssessment(option_id="opt-a")],
        rationale=DecisionRationale(recommended_option_id="opt-a"),
        evidence_ids=["ev-1"], generated_at=AS_OF, valid_as_of=AS_OF,
    )
    defaults.update(overrides)
    return AgentDecisionRecommendation(**defaults)


class TestCriterionAndAssessment:
    def test_criterion_weight_bounded(self):
        with pytest.raises(ValueError, match="within"):
            DecisionCriterion(
                id="c1", criterion_type=DecisionCriterionType.RISK,
                description="d", weight=1.5,
            )

    def test_weighting_is_visible_not_hidden(self):
        criterion = DecisionCriterion(
            id="c1", criterion_type=DecisionCriterionType.COMMITMENT_IMPACT,
            description="Does not violate a customer commitment",
            weight=0.9, hard_requirement=True, evidence_ids=["ev-1"],
        )
        assert criterion.weight == 0.9
        assert criterion.hard_requirement is True

    def test_criterion_scores_bounded(self):
        with pytest.raises(ValueError, match="within"):
            DecisionOptionAssessment(
                option_id="opt-a", criterion_scores={"c1": 1.4},
            )

    def test_evidence_confidence_bounded(self):
        with pytest.raises(ValueError, match="evidence_confidence"):
            DecisionOptionAssessment(
                option_id="opt-a", evidence_confidence=1.2,
            )


class TestRecommendationInvariants:
    def test_recommendation_requires_evidence(self):
        with pytest.raises(ValueError, match="evidence-backed"):
            _rec(evidence_ids=[])

    def test_available_requires_an_option(self):
        with pytest.raises(ValueError, match="requires a recommended"):
            _rec(recommended_option_id=None)

    def test_abstention_must_not_pretend_to_choose(self):
        with pytest.raises(ValueError, match="pretend to choose"):
            _rec(
                status=AgentRecommendationStatus.INSUFFICIENT_EVIDENCE,
                recommended_option_id="opt-a",
            )

    def test_contested_preserves_competing_assessments(self):
        with pytest.raises(ValueError, match="competing"):
            _rec(
                status=AgentRecommendationStatus.CONTESTED,
                recommended_option_id=None, assessments=[],
            )

    def test_confidence_never_reaches_one(self):
        with pytest.raises(ValueError, match="certainty is never"):
            _rec(confidence=1.0)
        assert _rec(confidence=0.99).confidence == 0.99

    def test_rationale_is_an_audit_artifact_not_chain_of_thought(self):
        field_names = set(DecisionRationale.model_fields)
        for forbidden in ("chain_of_thought", "thoughts", "reasoning_trace",
                          "internal_monologue", "scratchpad"):
            assert forbidden not in field_names
        # It carries the auditable structure instead.
        for expected in ("decisive_factors", "evidence_for_ids",
                         "evidence_against_ids", "assumptions",
                         "unresolved_unknowns", "dissenting_positions",
                         "alternative_rejection_reasons"):
            assert expected in field_names


class TestPolicy:
    def test_policy_defaults_are_conservative(self):
        policy = DecisionRecommendationPolicy()
        assert policy.minimum_recommendation_confidence == 0.65
        assert policy.strong_recommendation_threshold > (
            policy.moderate_recommendation_threshold
        )
        assert policy.minimum_option_margin > 0
        assert policy.prohibit_recommendation_with_unresolved_critical_unknown
        # Dissent is shown, not automatically disqualifying.
        assert policy.allow_recommendation_with_dissent is True


class TestHumanEvaluation:
    def _evaluator(self):
        return ActorReference(
            actor_id="priya", role="Platform Architecture Lead",
            team_id="platform",
        )

    def test_validate_requires_selected_option(self):
        with pytest.raises(ValueError, match="selected option"):
            HumanDecisionEvaluation(
                id="ev-1", recommendation_id="rec-1",
                evaluator=self._evaluator(),
                action=DecisionEvaluationAction.VALIDATE,
                evaluated_at=AS_OF,
            )

    def test_rejection_requires_reason(self):
        with pytest.raises(ValueError, match="reason"):
            HumanDecisionEvaluation(
                id="ev-1", recommendation_id="rec-1",
                evaluator=self._evaluator(),
                action=DecisionEvaluationAction.REJECT,
                evaluated_at=AS_OF,
            )

    def test_deferral_requires_what_is_missing(self):
        with pytest.raises(ValueError, match="what is missing"):
            HumanDecisionEvaluation(
                id="ev-1", recommendation_id="rec-1",
                evaluator=self._evaluator(),
                action=DecisionEvaluationAction.DEFER,
                evaluated_at=AS_OF,
            )

    def test_valid_evaluation_accepted(self):
        evaluation = HumanDecisionEvaluation(
            id="ev-1", recommendation_id="rec-1", evaluator=self._evaluator(),
            action=DecisionEvaluationAction.VALIDATE,
            selected_option_id="opt-a", evaluated_at=AS_OF,
        )
        assert evaluation.selected_option_id == "opt-a"
        assert evaluation.evaluator.role == "Platform Architecture Lead"
