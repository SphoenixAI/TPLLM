"""Scenario model invariants."""

from __future__ import annotations

import pytest

from tpllm.scenarios import (
    ScenarioEventType,
    ScenarioFactorType,
    ScenarioLattice,
    ScenarioProbabilityFactor,
    ScenarioProbabilityMethod,
    ScenarioTailMass,
    ScenarioTransition,
    build_state_signature,
)
from tests._scenario_fixtures import T0, horizon


class TestTransitionInvariants:
    def _transition(self, **overrides):
        defaults = dict(
            id="tr-1", from_state_id="s0", to_state_id="s1",
            event_type=ScenarioEventType.DECISION_MADE,
            event_description="approved",
            conditional_probability=0.6, probability_confidence=0.4,
        )
        defaults.update(overrides)
        return ScenarioTransition(**defaults)

    def test_probability_bounded(self):
        with pytest.raises(ValueError, match="conditional_probability"):
            self._transition(conditional_probability=1.4)

    def test_confidence_is_a_separate_bounded_quantity(self):
        with pytest.raises(ValueError, match="separate quantity"):
            self._transition(probability_confidence=1.2)

    def test_high_probability_with_low_confidence_is_valid(self):
        # P=0.75 with confidence 0.30 is an honest statement, not an error.
        transition = self._transition(
            conditional_probability=0.75, probability_confidence=0.30,
        )
        assert transition.conditional_probability == 0.75
        assert transition.probability_confidence == 0.30

    def test_impact_is_separate_from_probability(self):
        transition = self._transition(
            conditional_probability=0.04, impact=0.95,
        )
        assert transition.conditional_probability != transition.impact


class TestFactorInvariants:
    def _factor(self, **overrides):
        defaults = dict(
            id="f1", factor_type=ScenarioFactorType.KPI_SIGNAL,
            description="d", direction="increases",
            magnitude=0.5, confidence=0.5,
        )
        defaults.update(overrides)
        return ScenarioProbabilityFactor(**defaults)

    def test_magnitude_bounded(self):
        with pytest.raises(ValueError, match="magnitude"):
            self._factor(magnitude=1.5)

    def test_confidence_bounded(self):
        with pytest.raises(ValueError, match="confidence"):
            self._factor(confidence=-0.1)

    def test_direction_validated(self):
        with pytest.raises(ValueError, match="increases"):
            self._factor(direction="sideways")

    def test_independent_group_is_available(self):
        factor = self._factor(independent_group="integration-failure")
        assert factor.independent_group == "integration-failure"


class TestMethodHonesty:
    def test_default_method_is_heuristic(self):
        lattice = ScenarioLattice(
            id="l1", project_id="p", root_state_id="s0",
            generated_at=T0, horizon=horizon(),
        )
        assert lattice.method == ScenarioProbabilityMethod.HEURISTIC_V1

    def test_calibrated_and_learned_are_distinct_labels(self):
        assert len({
            ScenarioProbabilityMethod.HEURISTIC_V1,
            ScenarioProbabilityMethod.HISTORICALLY_CALIBRATED,
            ScenarioProbabilityMethod.LEARNED_MODEL,
        }) == 3


class TestTailMass:
    def test_tail_mass_records_what_was_pruned(self):
        tail = ScenarioTailMass(
            parent_state_id="s0", omitted_probability_mass=0.09,
            omitted_branch_count=3,
            explanation="3 branches below threshold omitted.",
        )
        assert tail.omitted_probability_mass == 0.09
        assert tail.omitted_branch_count == 3


class TestStateSignature:
    def test_signature_is_order_insensitive_and_deterministic(self):
        a = build_state_signature(
            {"blockers": ["b", "a"], "kpis": {"x": "1", "y": "2"}},
        )
        b = build_state_signature(
            {"kpis": {"y": "2", "x": "1"}, "blockers": ["a", "b"]},
        )
        assert a == b

    def test_different_content_different_signature(self):
        assert build_state_signature({"a": ["1"]}) != build_state_signature(
            {"a": ["2"]}
        )
