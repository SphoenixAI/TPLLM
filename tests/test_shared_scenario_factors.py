"""Shared uncertainty stays shared.

The failure this file exists to prevent: sampling "Platform saturates"
once for A, once for B, and once for C. Three draws would make three
correlated projects look like three independent bets, and the portfolio
would appear far safer than it actually is.
"""

from __future__ import annotations

import random

import pytest

from tpllm.portfolio import (
    SharedScenarioFactor,
    deduplicate_shared_factors,
    factors_affecting_project,
    projects_sharing_factor,
    sample_shared_factors,
    shared_factor_coverage,
)
from tests._portfolio_scenario_fixtures import (
    CountingRandom,
    account_graph_factor,
    partner_ops_factor,
    platform_saturation_factor,
    shared_factors,
)


class TestSharedFactorShape:
    def test_a_factor_touching_several_projects_is_shared(self):
        assert platform_saturation_factor().is_shared is True

    def test_a_factor_touching_one_project_is_not_shared(self):
        assert partner_ops_factor().is_shared is False

    def test_probability_and_confidence_are_bounded_separately(self):
        with pytest.raises(ValueError, match="probability"):
            SharedScenarioFactor(
                id="f", factor_type="t", probability=1.4,
                probability_confidence=0.5,
            )
        with pytest.raises(ValueError, match="probability_confidence"):
            SharedScenarioFactor(
                id="f", factor_type="t", probability=0.4,
                probability_confidence=1.4,
            )

    def test_a_low_confidence_high_probability_factor_is_legal(self):
        # "70% likely, and we are not at all sure of that" is an honest
        # statement, not a contradiction.
        factor = SharedScenarioFactor(
            id="f", factor_type="t", probability=0.7,
            probability_confidence=0.2,
        )
        assert factor.probability == 0.7
        assert factor.probability_confidence == 0.2


class TestSampledOnce:
    """The non-negotiable invariant of this module."""

    def test_one_draw_per_distinct_uncertainty(self):
        rng = CountingRandom([0.1])
        factors = shared_factors()
        sample_shared_factors(factors=factors, rng=rng)
        assert rng.calls == 3, (
            "three distinct uncertainties must cost exactly three draws — "
            "one per uncertainty, not one per affected project"
        )

    def test_a_three_project_factor_still_costs_one_draw(self):
        rng = CountingRandom([0.1])
        sample_shared_factors(
            factors=[platform_saturation_factor()], rng=rng,
        )
        assert rng.calls == 1, (
            "sampling per affected project would manufacture "
            "diversification the organization does not have"
        )

    def test_every_affected_project_reads_the_same_value(self):
        rng = random.Random(3)
        factor = platform_saturation_factor()
        outcomes = sample_shared_factors(factors=[factor], rng=rng)
        values = {
            outcomes[f.id]
            for project_id in factor.affected_project_ids
            for f in factors_affecting_project(
                factors=[factor], project_id=project_id,
            )
        }
        assert len(values) == 1

    def test_shared_factor_moves_projects_together(self):
        # A shared factor at p=0.5 disrupts all three projects together in
        # about half the runs. Independent sampling would have produced
        # 0.5**3 = 0.125 for the joint case.
        factor = platform_saturation_factor(probability=0.5)
        rng = random.Random(99)
        joint = sum(
            1 for _ in range(4000)
            if sample_shared_factors(factors=[factor], rng=rng)[factor.id]
        )
        assert 0.45 < joint / 4000 < 0.55

    def test_independent_factors_stay_independent(self):
        # Two single-project factors at p=0.5 must fire together about a
        # quarter of the time, not half.
        a = SharedScenarioFactor(
            id="f-a", factor_type="t", affected_project_ids=["project-a"],
            probability=0.5, probability_confidence=0.5,
        )
        b = SharedScenarioFactor(
            id="f-b", factor_type="t", affected_project_ids=["project-b"],
            probability=0.5, probability_confidence=0.5,
        )
        rng = random.Random(4242)
        joint = 0
        runs = 4000
        for _ in range(runs):
            outcomes = sample_shared_factors(factors=[a, b], rng=rng)
            if outcomes["f-a"] and outcomes["f-b"]:
                joint += 1
        assert 0.21 < joint / runs < 0.29, (
            "genuinely independent project uncertainties must not be "
            "collapsed into one draw"
        )

    def test_certain_and_impossible_factors(self):
        rng = random.Random(1)
        certain = SharedScenarioFactor(
            id="certain", factor_type="t", probability=1.0,
            probability_confidence=0.9,
            affected_project_ids=["project-a", "project-b"],
        )
        never = SharedScenarioFactor(
            id="never", factor_type="t", probability=0.0,
            probability_confidence=0.9,
        )
        outcomes = sample_shared_factors(factors=[certain, never], rng=rng)
        assert outcomes["certain"] is True
        assert outcomes["never"] is False

    def test_deterministic_for_a_seed(self):
        factors = shared_factors()
        first = sample_shared_factors(factors=factors, rng=random.Random(5))
        second = sample_shared_factors(factors=factors, rng=random.Random(5))
        assert first == second


class TestCorrelatedReportsCountOnce:
    def _three_reports(self) -> list[SharedScenarioFactor]:
        return [
            platform_saturation_factor(
                probability=0.30, factor_id="rep-slack",
                group="platform-thursday",
            ),
            platform_saturation_factor(
                probability=0.45, factor_id="rep-jira",
                group="platform-thursday",
            ),
            platform_saturation_factor(
                probability=0.20, factor_id="rep-email",
                group="platform-thursday",
            ),
        ]

    def test_three_reports_of_one_situation_collapse_to_one(self):
        collapsed = deduplicate_shared_factors(self._three_reports())
        assert len(collapsed) == 1, (
            "three descriptions of one Platform outage are one risk, not "
            "three"
        )
        assert collapsed[0].id == "rep-jira"  # strongest by p * confidence

    def test_correlated_reports_cost_one_draw(self):
        rng = CountingRandom([0.1])
        sample_shared_factors(factors=self._three_reports(), rng=rng)
        assert rng.calls == 1

    def test_every_report_id_resolves_to_the_same_draw(self):
        rng = CountingRandom([0.1])
        outcomes = sample_shared_factors(factors=self._three_reports(), rng=rng)
        assert set(outcomes) == {"rep-slack", "rep-jira", "rep-email"}
        assert len(set(outcomes.values())) == 1

    def test_ungrouped_factors_are_untouched(self):
        collapsed = deduplicate_shared_factors(shared_factors())
        assert [f.id for f in collapsed] == [
            "factor-account-graph-incident",
            "factor-partner-ops-slip",
            "factor-platform-saturated",
        ]

    def test_deduplication_is_deterministic(self):
        factors = self._three_reports() + shared_factors()
        assert [f.id for f in deduplicate_shared_factors(factors)] == [
            f.id for f in deduplicate_shared_factors(list(reversed(factors)))
        ]


class TestFactorQueries:
    def test_factors_affecting_a_project(self):
        found = factors_affecting_project(
            factors=shared_factors(), project_id="project-a",
        )
        assert [f.id for f in found] == [
            "factor-account-graph-incident", "factor-platform-saturated",
        ]

    def test_a_project_with_its_own_exposure_only(self):
        found = factors_affecting_project(
            factors=shared_factors(), project_id="project-d",
        )
        assert [f.id for f in found] == ["factor-partner-ops-slip"]

    def test_projects_sharing_a_factor(self):
        assert projects_sharing_factor(platform_saturation_factor()) == [
            "project-a", "project-b", "project-c",
        ]

    def test_coverage_lists_only_genuinely_shared_factors(self):
        coverage = shared_factor_coverage(shared_factors())
        assert set(coverage) == {
            "factor-account-graph-incident", "factor-platform-saturated",
        }
        assert "factor-partner-ops-slip" not in coverage, (
            "a single-project uncertainty is not shared exposure"
        )
        assert coverage["factor-account-graph-incident"] == [
            "project-a", "project-c",
        ]

    def test_empty_inputs(self):
        assert deduplicate_shared_factors([]) == []
        assert shared_factor_coverage([]) == {}
        assert sample_shared_factors(
            factors=[], rng=random.Random(1),
        ) == {}
        assert account_graph_factor().evidence_ids == [
            "ev-account-graph-incident"
        ]
