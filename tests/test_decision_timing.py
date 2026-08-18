"""Tests for Commit 27 — decide now versus learn first."""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.coordination import (
    DecisionTimingAction,
    ScenarioDecisionPolicy,
    build_decision_research_needs,
    evaluate_decision_timing,
)
from lltpm.scenarios import InformationValueEstimate
from tests._scenario_decision_fixtures import T0, policy


def _analysis(option_id, success, confidence=0.7):
    from types import SimpleNamespace

    return SimpleNamespace(
        option_id=option_id,
        target_success_probability=success,
        probability_confidence=confidence,
    )


def _information(value=0.64, unknown_id="unk-coexistence"):
    return [
        InformationValueEstimate(
            unknown_id=unknown_id,
            current_uncertainty=0.5,
            expected_probability_reduction=value,
            affected_outcomes=["launch_on_time"],
            priority=value,
            explanation=(
                "Resolving v2/v3 coexistence would sharpen the modeled "
                "distribution."
            ),
        )
    ]


class TestResearchFirst:
    def test_high_information_value_low_delay_cost(self):
        # The canonical case: options nearly tied, information is worth
        # far more than four hours of waiting.
        timing = evaluate_decision_timing(
            option_analyses=[
                _analysis("opt-a", 0.71), _analysis("opt-b", 0.69),
            ],
            information_values=_information(0.64),
            cost_of_delay=0.08,
            policy=policy(),
        )
        assert timing.recommended_action == DecisionTimingAction.RESEARCH_FIRST
        assert timing.value_of_information == pytest.approx(0.64)
        assert timing.expected_cost_of_waiting == pytest.approx(0.08)
        assert "unk-coexistence" in timing.recommended_research_unknown_ids

    def test_close_options_are_noted(self):
        timing = evaluate_decision_timing(
            option_analyses=[
                _analysis("opt-a", 0.71), _analysis("opt-b", 0.69),
            ],
            information_values=_information(0.64),
            cost_of_delay=0.08, policy=policy(),
        )
        assert timing.option_separation == pytest.approx(0.02)
        assert "separated by only" in timing.explanation

    def test_research_disallowed_becomes_defer(self):
        wait_until = T0 + timedelta(days=2)
        timing = evaluate_decision_timing(
            option_analyses=[
                _analysis("opt-a", 0.71), _analysis("opt-b", 0.69),
            ],
            information_values=_information(0.64),
            cost_of_delay=0.08,
            policy=policy(allow_research_before_decision=False),
            wait_until=wait_until,
        )
        assert timing.recommended_action == (
            DecisionTimingAction.DEFER_FOR_EVENT
        )
        assert timing.recommended_wait_until == wait_until


class TestDecideNow:
    def test_low_information_value_high_delay_cost(self):
        timing = evaluate_decision_timing(
            option_analyses=[
                _analysis("opt-a", 0.88), _analysis("opt-b", 0.40),
            ],
            information_values=_information(0.10),
            cost_of_delay=0.70, policy=policy(),
        )
        assert timing.recommended_action == DecisionTimingAction.DECIDE_NOW
        assert timing.current_best_option_id == "opt-a"
        assert "does not clear" in timing.explanation

    def test_no_information_available(self):
        timing = evaluate_decision_timing(
            option_analyses=[_analysis("opt-a", 0.9)],
            information_values=[], cost_of_delay=0.1, policy=policy(),
        )
        assert timing.recommended_action == DecisionTimingAction.DECIDE_NOW
        assert timing.value_of_information == 0.0

    def test_clear_winner_decides_despite_some_information_value(self):
        # Waiting costs more than the information is worth.
        timing = evaluate_decision_timing(
            option_analyses=[
                _analysis("opt-a", 0.92), _analysis("opt-b", 0.35),
            ],
            information_values=_information(0.25),
            cost_of_delay=0.60, policy=policy(),
        )
        assert timing.recommended_action == DecisionTimingAction.DECIDE_NOW

    def test_decide_now_requests_no_research(self):
        timing = evaluate_decision_timing(
            option_analyses=[_analysis("opt-a", 0.9)],
            information_values=_information(0.05),
            cost_of_delay=0.8, policy=policy(),
        )
        assert timing.recommended_research_unknown_ids == []


class TestResearchNeeds:
    def _timing(self, action_value=0.64, cost=0.08):
        return evaluate_decision_timing(
            option_analyses=[
                _analysis("opt-a", 0.71), _analysis("opt-b", 0.69),
            ],
            information_values=_information(action_value),
            cost_of_delay=cost, policy=policy(),
        )

    def test_research_first_produces_commit13_targets(self):
        needs = build_decision_research_needs(
            timing=self._timing(),
            information_values=_information(0.64),
            decision_question="Approve contract v3 for enterprise accounts?",
        )
        assert len(needs) == 1
        need = needs[0]
        assert need.unknown_id == "unk-coexistence"
        assert "Approve contract v3" in need.blocking_decision_question
        assert need.expected_uncertainty_reduction == pytest.approx(0.64)
        assert need.why_it_matters

    def test_decide_now_manufactures_no_research(self):
        timing = evaluate_decision_timing(
            option_analyses=[_analysis("opt-a", 0.9)],
            information_values=_information(0.05),
            cost_of_delay=0.8, policy=policy(),
        )
        assert build_decision_research_needs(
            timing=timing, information_values=_information(0.05),
            decision_question="q",
        ) == []

    def test_unknown_without_an_estimate_is_skipped(self):
        timing = self._timing()
        assert build_decision_research_needs(
            timing=timing, information_values=[], decision_question="q",
        ) == []


class TestDeterminism:
    def test_same_inputs_same_assessment(self):
        kwargs = dict(
            option_analyses=[
                _analysis("opt-a", 0.71), _analysis("opt-b", 0.69),
            ],
            information_values=_information(0.64),
            cost_of_delay=0.08, policy=policy(),
        )
        assert evaluate_decision_timing(**kwargs).model_dump() == (
            evaluate_decision_timing(**kwargs).model_dump()
        )
