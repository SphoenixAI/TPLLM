"""Probability, correlation handling, and
outcome aggregation without double-counting."""

from __future__ import annotations

import pytest

from lltpm.scenarios import (
    ScenarioFactorType,
    build_correlated_evidence_factors,
    build_factor,
    calculate_outcome_probability,
    calculate_path_probability,
    clamp_probability,
    combine_factors,
    deduplicate_correlated_factors,
    derive_factors_from_state,
    enumerate_paths,
    probability_of_outcome,
)
from tests._scenario_fixtures import T0, lattice, temporal_state


class TestClamping:
    def test_never_certain_in_either_direction(self):
        assert clamp_probability(0.0) == 0.01
        assert clamp_probability(1.0) == 0.99
        assert clamp_probability(-5.0) == 0.01
        assert clamp_probability(0.5) == 0.5


class TestCorrelatedEvidence:
    def _three_reports_of_one_failure(self):
        return build_correlated_evidence_factors(
            situation_id="integration-failure",
            factor_type=ScenarioFactorType.CURRENT_STRUCTURAL_SIGNAL,
            descriptions=[
                "Slack: integration failed",
                "Meeting: the integration failure was discussed",
                "Summary: integration failure noted",
            ],
            magnitude=0.5, confidence=0.7,
        )

    def test_shared_origin_collapses_to_one(self):
        deduped = deduplicate_correlated_factors(
            self._three_reports_of_one_failure()
        )
        assert len(deduped) == 1

    def test_three_reports_do_not_triple_the_probability(self):
        correlated = self._three_reports_of_one_failure()
        one_report = correlated[:1]
        p_three, _ = combine_factors(
            base_probability=0.3, factors=correlated,
        )
        p_one, _ = combine_factors(base_probability=0.3, factors=one_report)
        assert p_three == pytest.approx(p_one), (
            "three messages about one failure must move the estimate once"
        )

    def test_genuinely_independent_factors_do_compound(self):
        independent = [
            build_factor(
                factor_id=f"f{i}",
                factor_type=ScenarioFactorType.CURRENT_STRUCTURAL_SIGNAL,
                description=f"distinct signal {i}",
                direction="increases", magnitude=0.5, confidence=0.7,
            )
            for i in range(3)
        ]
        p_three, _ = combine_factors(
            base_probability=0.3, factors=independent,
        )
        p_one, _ = combine_factors(
            base_probability=0.3, factors=independent[:1],
        )
        assert p_three > p_one

    def test_strongest_member_of_a_group_is_kept(self):
        factors = [
            build_factor(
                factor_id="weak", factor_type=ScenarioFactorType.KPI_SIGNAL,
                description="weak", direction="increases",
                magnitude=0.2, confidence=0.3, independent_group="g",
            ),
            build_factor(
                factor_id="strong", factor_type=ScenarioFactorType.KPI_SIGNAL,
                description="strong", direction="increases",
                magnitude=0.9, confidence=0.9, independent_group="g",
            ),
        ]
        assert [f.id for f in deduplicate_correlated_factors(factors)] == [
            "strong"
        ]


class TestProbabilityVersusConfidence:
    def test_they_are_computed_separately(self):
        strong_evidence = [
            build_factor(
                factor_id="f1",
                factor_type=ScenarioFactorType.HISTORICAL_BASE_RATE,
                description="d", direction="increases",
                magnitude=0.5, confidence=0.9,
            )
        ]
        weak_evidence = [
            build_factor(
                factor_id="f1",
                factor_type=ScenarioFactorType.HISTORICAL_BASE_RATE,
                description="d", direction="increases",
                magnitude=0.5, confidence=0.1,
            )
        ]
        p_strong, c_strong = combine_factors(
            base_probability=0.5, factors=strong_evidence,
        )
        p_weak, c_weak = combine_factors(
            base_probability=0.5, factors=weak_evidence,
        )
        # Confidence tracks evidence quality, not the probability value.
        assert c_strong > c_weak

    def test_no_factors_yields_a_low_confidence_estimate(self):
        probability, confidence = combine_factors(
            base_probability=0.7, factors=[],
        )
        assert probability == pytest.approx(0.7)
        assert confidence <= 0.3

    def test_decreasing_factors_lower_probability(self):
        decreasing = [
            build_factor(
                factor_id="f1",
                factor_type=ScenarioFactorType.RECOVERY_CAPABILITY,
                description="mitigation available", direction="decreases",
                magnitude=0.6, confidence=0.8,
            )
        ]
        probability, _ = combine_factors(
            base_probability=0.6, factors=decreasing,
        )
        assert probability < 0.6


class TestPathProbability:
    def test_chained_conditionals(self):
        lat = lattice()
        path = enumerate_paths(lattice=lat)[0]
        probability, confidence = calculate_path_probability(
            lattice=lat, transition_ids=path.transition_ids,
        )
        assert probability == pytest.approx(path.path_probability)
        # Confidence is the weakest link.
        step_confidences = [
            lat.transition(t).probability_confidence
            for t in path.transition_ids
        ]
        assert confidence == pytest.approx(min(step_confidences))

    def test_empty_path_is_certain_by_definition(self):
        assert calculate_path_probability(
            lattice=lattice(), transition_ids=[],
        ) == (1.0, 1.0)

    def test_path_enumeration_deterministic(self):
        lat = lattice()
        first = [p.model_dump() for p in enumerate_paths(lattice=lat)]
        second = [p.model_dump() for p in enumerate_paths(lattice=lat)]
        assert first == second


class TestOutcomeAggregation:
    def test_convergent_paths_aggregate_without_double_counting(self):
        lat = lattice()
        on_time, _ = probability_of_outcome(
            lattice=lat, outcome_label="launch_on_time",
        )
        slips, _ = probability_of_outcome(
            lattice=lat, outcome_label="launch_slips",
        )
        # Disjoint outcomes cannot together exceed the whole.
        assert on_time + slips <= 1.0
        assert on_time > 0 and slips > 0

    def test_outcome_probability_equals_sum_of_its_paths(self):
        lat = lattice()
        paths = [
            p for p in enumerate_paths(lattice=lat)
            if "launch_on_time" in p.outcome_labels
        ]
        expected = sum(p.path_probability for p in paths)
        actual, _ = probability_of_outcome(
            lattice=lat, outcome_label="launch_on_time",
        )
        assert actual == pytest.approx(expected)

    def test_unknown_outcome_is_zero(self):
        probability, confidence = probability_of_outcome(
            lattice=lattice(), outcome_label="never_modeled",
        )
        assert probability == 0.0
        assert confidence == 0.0

    def test_generic_predicate_api(self):
        lat = lattice()
        probability, _ = calculate_outcome_probability(
            lattice=lat,
            outcome_predicate=lambda p, _l: len(p.transition_ids) >= 2,
        )
        assert 0.0 < probability <= 1.0

    def test_aggregate_confidence_is_probability_weighted(self):
        lat = lattice()
        _, confidence = probability_of_outcome(
            lattice=lat, outcome_label="launch_on_time",
        )
        assert 0.0 < confidence < 1.0


class TestFactorsFromState:
    def test_state_derived_factors_are_traceable(self):
        factors = derive_factors_from_state(
            state=temporal_state(), as_of=T0,
        )
        assert factors
        blocker_factors = [
            f for f in factors if "blk-contract" in f.source_ids
        ]
        assert blocker_factors
        assert blocker_factors[0].independent_group == "blocker:blk-contract"

    def test_authoritative_decisions_reduce_risk(self):
        factors = derive_factors_from_state(
            state=temporal_state(), as_of=T0,
        )
        stabilizers = [f for f in factors if f.direction == "decreases"]
        assert stabilizers

    def test_no_state_no_factors(self):
        from lltpm.temporal import ProjectTemporalState

        empty = ProjectTemporalState(
            project_id="atlas", as_of=T0, evidence_cutoff_at=T0,
            state_fingerprint="fp-empty",
        )
        assert derive_factors_from_state(state=empty, as_of=T0) == []
