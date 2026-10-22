"""What deserves attention.

Two symmetric failures. Surfacing P(slip) 0.431 → 0.433 trains people to
ignore the system. Suppressing P(>7-day slip) 0.12 → 0.31 because its
confidence is low means the system is quiet right up until the thing
happens.

The rules: absolute movement for ordinary outcomes, a tighter floor for
severe ones, threshold crossings regardless of size, and low confidence
as a label rather than a mute button.
"""

from __future__ import annotations

import pytest

from tpllm.runtime import (
    IntelligenceArtifactType as A,
    MaterialityReason,
    NoActionReason,
    RecomputedArtifact,
    RuntimeResearchBudget,
    ScenarioMaterialityPolicy,
    assess_artifact_materiality,
    assess_scenario_probability_materiality,
    assess_scenario_shifts,
    non_material_assessment,
    should_research_before_asking,
)
from tests._runtime_fixtures import PROJECT_A, PROJECT_C

POLICY = ScenarioMaterialityPolicy()


def _shift(before, after, **kwargs):
    defaults = dict(
        outcome_label="slip", before_probability=before,
        after_probability=after, policy=POLICY, probability_confidence=0.8,
    )
    defaults.update(kwargs)
    return assess_scenario_probability_materiality(**defaults)


def _artifact(artifact_type=A.SCENARIO_LATTICE, **overrides):
    defaults = dict(
        artifact_type=artifact_type,
        artifact_id=f"{artifact_type.value}-1",
        revision_id="revision-1",
        state_fingerprint="fp",
        project_ids=[PROJECT_A],
        after_summary=f"{artifact_type.value} refreshed.",
    )
    defaults.update(overrides)
    return RecomputedArtifact(**defaults)


class TestScenarioShiftMateriality:
    def test_a_tiny_shift_is_suppressed(self):
        shift = _shift(0.431, 0.433)
        assert shift.material is False
        assert "Not worth an interruption" in shift.explanation

    def test_a_large_shift_is_surfaced(self):
        shift = _shift(0.12, 0.31)
        assert shift.material is True
        assert "12% → 31%" in shift.explanation

    def test_the_default_floor_is_ten_points(self):
        assert _shift(0.20, 0.29).material is False
        assert _shift(0.20, 0.30).material is True

    def test_a_severe_outcome_gets_a_tighter_floor(self):
        assert _shift(0.20, 0.26, is_severe_outcome=True).material is True
        assert _shift(0.20, 0.26).material is False, (
            "a small rise in the chance of something bad is worth more than "
            "the same rise in the chance of something inconvenient"
        )

    def test_a_threshold_crossing_is_material_despite_small_movement(self):
        shift = _shift(0.19, 0.21, alert_threshold=0.20)
        assert shift.material is True
        assert shift.threshold_crossed is True
        assert "crossing the 20% alert threshold" in shift.explanation

    def test_movement_below_a_threshold_is_not_a_crossing(self):
        shift = _shift(0.15, 0.18, alert_threshold=0.20)
        assert shift.threshold_crossed is False
        assert shift.material is False

    def test_crossing_downward_also_counts(self):
        assert _shift(0.21, 0.19, alert_threshold=0.20).threshold_crossed

    def test_threshold_crossings_can_be_switched_off(self):
        shift = _shift(
            0.19, 0.21, alert_threshold=0.20,
            policy=ScenarioMaterialityPolicy(
                threshold_crossings_are_material=False,
            ),
        )
        assert shift.threshold_crossed is False
        assert shift.material is False

    def test_the_delta_is_reported(self):
        assert _shift(0.12, 0.31).absolute_delta == pytest.approx(0.19)

    def test_no_movement_is_not_material(self):
        assert _shift(0.30, 0.30).material is False


class TestLowConfidenceIsLabelledNotSilenced:
    def test_a_dramatic_low_confidence_shift_is_still_surfaced(self):
        shift = _shift(0.20, 0.55, probability_confidence=0.12)
        assert shift.material is True, (
            "suppressing uncertain risk is how a system stays quiet until "
            "the thing happens"
        )

    def test_it_carries_a_qualifier(self):
        shift = _shift(0.20, 0.55, probability_confidence=0.12)
        assert shift.low_confidence_qualifier is True
        assert "confidence is 0.12" in shift.explanation
        assert "not as a firm number" in shift.explanation

    def test_a_confident_shift_carries_no_qualifier(self):
        shift = _shift(0.20, 0.55, probability_confidence=0.85)
        assert shift.low_confidence_qualifier is False
        assert "investigate" not in shift.explanation

    def test_a_non_material_low_confidence_shift_stays_quiet(self):
        shift = _shift(0.20, 0.21, probability_confidence=0.05)
        assert shift.material is False
        assert shift.low_confidence_qualifier is False

    def test_probability_and_confidence_stay_separate(self):
        shift = _shift(0.20, 0.55, probability_confidence=0.12)
        assert shift.after_probability == 0.55
        assert shift.probability_confidence == 0.12


class TestShiftSets:
    def test_every_outcome_is_judged(self):
        shifts = assess_scenario_shifts(
            before={"on_time": 0.64, "slip": 0.11},
            after={"on_time": 0.81, "slip": 0.05},
            policy=POLICY,
        )
        assert [s.outcome_label for s in shifts] == ["on_time", "slip"]

    def test_a_new_outcome_counts_from_zero(self):
        shifts = assess_scenario_shifts(
            before={}, after={"severe_slip": 0.4}, policy=POLICY,
        )
        assert shifts[0].before_probability == 0.0
        assert shifts[0].material is True

    def test_severe_labels_are_honoured(self):
        shifts = assess_scenario_shifts(
            before={"severe_slip": 0.20}, after={"severe_slip": 0.26},
            policy=POLICY, severe_outcome_labels=["severe_slip"],
        )
        assert shifts[0].severe_outcome is True
        assert shifts[0].material is True

    def test_thresholds_are_applied_per_label(self):
        shifts = assess_scenario_shifts(
            before={"severe_slip": 0.19}, after={"severe_slip": 0.21},
            policy=POLICY, alert_thresholds={"severe_slip": 0.20},
        )
        assert shifts[0].threshold_crossed is True


class TestArtifactMateriality:
    def test_an_unchanged_artifact_is_never_material(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(changed=False), policy=POLICY,
        )
        assert assessment.material is False
        assert assessment.human_attention_required is False
        assert NoActionReason.NON_MATERIAL in assessment.no_action_reasons
        assert "nobody needs to hear about it" in assessment.explanation

    def test_a_tiny_scenario_move_updates_state_quietly(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(
                previous_outcome_probabilities={"slip": 0.431},
                outcome_probabilities={"slip": 0.433},
                probability_confidence=0.8,
            ),
            policy=POLICY,
        )
        assert assessment.material is False
        assert assessment.human_attention_required is False

    def test_a_large_scenario_move_is_material(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(
                previous_outcome_probabilities={"slip": 0.19},
                outcome_probabilities={"slip": 0.49},
                probability_confidence=0.8,
            ),
            policy=POLICY,
        )
        assert assessment.material is True
        assert MaterialityReason.SCENARIO_PROBABILITY_SHIFT in (
            assessment.reasons
        )

    def test_a_severe_outcome_move_raises_the_score(self):
        ordinary = assess_artifact_materiality(
            artifact=_artifact(
                previous_outcome_probabilities={"slip": 0.19},
                outcome_probabilities={"slip": 0.49},
            ),
            policy=POLICY,
        )
        severe = assess_artifact_materiality(
            artifact=_artifact(
                previous_outcome_probabilities={"severe_slip": 0.19},
                outcome_probabilities={"severe_slip": 0.49},
            ),
            policy=POLICY, severe_outcome_labels=["severe_slip"],
        )
        assert severe.score > ordinary.score
        assert MaterialityReason.SEVERE_FAILURE_RISK_CHANGE in severe.reasons

    def test_a_decision_requirement_demands_attention(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(
                A.DECISION_RECOMMENDATION, decision_required=True,
            ),
            policy=POLICY,
        )
        assert assessment.material is True
        assert assessment.human_attention_required is True
        assert MaterialityReason.DECISION_REQUIRED in assessment.reasons
        assert "decision_request" in assessment.recommended_surface_types

    def test_a_new_portfolio_bottleneck_is_material(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(A.PORTFOLIO_BOTTLENECKS), policy=POLICY,
        )
        assert assessment.material is True
        assert MaterialityReason.NEW_PORTFOLIO_BOTTLENECK in (
            assessment.reasons
        )
        assert assessment.human_attention_required is True

    def test_a_portfolio_priority_change_is_material(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(A.PORTFOLIO_RECOMMENDATION), policy=POLICY,
        )
        assert MaterialityReason.PORTFOLIO_PRIORITY_CHANGED in (
            assessment.reasons
        )

    def test_a_recommendation_change_is_material(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(A.DECISION_RECOMMENDATION), policy=POLICY,
        )
        assert MaterialityReason.RECOMMENDATION_CHANGED in assessment.reasons

    def test_a_readiness_change_updates_state_without_interrupting(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(A.LAUNCH_READINESS), policy=POLICY,
        )
        assert assessment.material is True
        assert assessment.human_attention_required is False
        assert NoActionReason.NO_HUMAN_JUDGMENT_REQUIRED in (
            assessment.no_action_reasons
        )

    def test_a_bare_workstream_refresh_is_not_material(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(A.WORKSTREAM_STATE), policy=POLICY,
        )
        assert assessment.material is False
        assert "no attention required" in assessment.explanation

    def test_a_duplicate_is_recorded_as_such(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(changed=False), policy=POLICY, duplicate=True,
        )
        assert NoActionReason.DUPLICATE in assessment.no_action_reasons

    def test_an_already_resolved_request_needs_no_attention(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(
                A.DECISION_RECOMMENDATION, decision_required=True,
            ),
            policy=POLICY, already_resolved=True,
        )
        assert assessment.human_attention_required is False
        assert NoActionReason.ALREADY_RESOLVED in assessment.no_action_reasons

    def test_an_already_answered_question_is_not_asked_again(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(
                A.DECISION_RECOMMENDATION, decision_required=True,
            ),
            policy=POLICY, already_answered=True,
        )
        assert assessment.human_attention_required is False
        assert NoActionReason.ALREADY_ANSWERED in assessment.no_action_reasons

    def test_a_low_confidence_material_change_is_flagged(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(
                previous_outcome_probabilities={"slip": 0.20},
                outcome_probabilities={"slip": 0.55},
                probability_confidence=0.12,
            ),
            policy=POLICY,
        )
        assert assessment.material is True
        assert assessment.low_confidence_qualifier is True

    def test_affected_projects_are_carried_through(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(project_ids=[PROJECT_A, PROJECT_C]),
            policy=POLICY,
        )
        assert assessment.affected_project_ids == [PROJECT_A, PROJECT_C]

    def test_reasons_are_ordered_by_weight(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(
                A.DECISION_RECOMMENDATION,
                decision_required=True,
                previous_outcome_probabilities={"slip": 0.19},
                outcome_probabilities={"slip": 0.49},
            ),
            policy=POLICY,
        )
        assert assessment.reasons[0] == MaterialityReason.DECISION_REQUIRED

    def test_severity_alone_raises_a_risk_reason(self):
        assessment = assess_artifact_materiality(
            artifact=_artifact(A.WORKSTREAM_STATE, severity="severe"),
            policy=POLICY,
        )
        assert MaterialityReason.SEVERE_FAILURE_RISK_CHANGE in (
            assessment.reasons
        )


class TestFirstClassNoAction:
    def test_a_no_action_assessment_is_constructible(self):
        assessment = non_material_assessment(
            project_ids=[PROJECT_A],
            reason=NoActionReason.EXISTING_PLAN_SUFFICIENT,
            explanation="The existing plan already covers this.",
        )
        assert assessment.material is False
        assert assessment.human_attention_required is False
        assert assessment.no_action_reasons == [
            NoActionReason.EXISTING_PLAN_SUFFICIENT
        ]


class TestResearchBeforeAsking:
    def test_a_valuable_unknown_triggers_internal_research(self):
        should, reason = should_research_before_asking(
            probability_confidence=0.3,
            expected_information_gain=0.4,
            rounds_used=0,
            budget=RuntimeResearchBudget(),
        )
        assert should is True
        assert "before consuming anyone's attention" in reason

    def test_a_negligible_gain_stops_the_loop(self):
        should, reason = should_research_before_asking(
            probability_confidence=0.3,
            expected_information_gain=0.02,
            rounds_used=0,
            budget=RuntimeResearchBudget(),
        )
        assert should is False
        assert "would not change the answer" in reason

    def test_the_round_budget_stops_the_loop(self):
        should, reason = should_research_before_asking(
            probability_confidence=0.3,
            expected_information_gain=0.9,
            rounds_used=3,
            budget=RuntimeResearchBudget(),
        )
        assert should is False
        assert "budget spent" in reason

    def test_a_tighter_budget_stops_sooner(self):
        should, _ = should_research_before_asking(
            probability_confidence=0.3,
            expected_information_gain=0.9,
            rounds_used=1,
            budget=RuntimeResearchBudget(
                maximum_research_rounds_per_causal_event=1,
            ),
        )
        assert should is False

    def test_the_loop_cannot_run_forever(self):
        budget = RuntimeResearchBudget()
        rounds = 0
        while True:
            should, _ = should_research_before_asking(
                probability_confidence=0.2,
                expected_information_gain=0.5,
                rounds_used=rounds,
                budget=budget,
            )
            if not should:
                break
            rounds += 1
            assert rounds <= 10
        assert rounds == budget.maximum_research_rounds_per_causal_event
