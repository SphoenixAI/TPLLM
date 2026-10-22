"""Comparing allocations and reporting regret.

The rule this file defends: a portfolio recommendation must state the
displaced impact. Choosing to delay a project is sometimes right;
choosing it silently never is, so the recommendation object cannot even
be constructed without naming what the displaced projects pay.
"""

from __future__ import annotations

import pytest

from lltpm.portfolio import (
    PortfolioAllocationRecommendation,
    PortfolioRegretAssessment,
    allocation_from_capacity,
    allocation_from_order,
    assess_allocation_regret,
    compare_allocations,
    explain_portfolio_allocation,
    portfolio_value_by_allocation,
    recommend_portfolio_allocation,
    simulate_portfolio_allocation,
)
from tests._portfolio_scenario_fixtures import (
    PLATFORM,
    horizon,
    platform_saturation_factor,
    portfolio_state,
    stranding_allocation,
    weighted_allocation,
)

RUNS = 800
SEED = 20261117


def _candidates(state):
    return [
        weighted_allocation(state),
        stranding_allocation(state),
        allocation_from_order(
            state=state, resource_id=PLATFORM,
            ordered_demand_ids=["dem-c", "dem-d", "dem-a", "dem-b"],
            available_units=2.0,
            allocation_id="alloc-serves-c-and-d",
        ),
    ]


_UNSET = object()


def _recommend(**kwargs):
    state = kwargs.pop("state", None) or portfolio_state()
    allocations = kwargs.pop("allocations", _UNSET)
    return recommend_portfolio_allocation(
        portfolio_state=state,
        allocations=(
            _candidates(state) if allocations is _UNSET else allocations
        ),
        horizon=horizon(),
        runs=kwargs.pop("runs", RUNS),
        seed=kwargs.pop("seed", SEED),
        **kwargs,
    )


def _results(state=None):
    state = state or portfolio_state()
    return compare_allocations(
        portfolio_state=state, allocations=_candidates(state),
        horizon=horizon(), runs=RUNS, seed=SEED,
    )


class TestComparingAllocations:
    def test_every_candidate_is_simulated(self):
        assert len(_results()) == 3

    def test_candidates_face_the_same_drawn_futures(self):
        results = _results()
        # Shared seed, so the sampled factor frequencies match; any
        # difference between allocations is the decision, not the luck.
        frequencies = {
            tuple(sorted(r.shared_factor_frequencies.items()))
            for r in results
        }
        assert len(frequencies) == 1

    def test_values_differ_between_allocations(self):
        values = portfolio_value_by_allocation(_results())
        assert len(set(values.values())) == 3

    def test_no_candidates_compares_nothing(self):
        assert compare_allocations(
            portfolio_state=portfolio_state(), allocations=[],
            horizon=horizon(),
        ) == []

    def test_deterministic(self):
        assert [r.model_dump() for r in _results()] == [
            r.model_dump() for r in _results()
        ]


class TestRegret:
    def test_the_best_allocation_has_zero_regret(self):
        assessments = assess_allocation_regret(_results())
        assert assessments[0].regret == 0.0
        assert assessments[0].best_alternative_id is None

    def test_worse_allocations_carry_positive_regret(self):
        assessments = assess_allocation_regret(_results())
        assert all(a.regret >= 0 for a in assessments)
        assert assessments[-1].regret > 0

    def test_regret_names_the_better_alternative(self):
        assessments = assess_allocation_regret(_results())
        worst = assessments[-1]
        assert worst.best_alternative_id == assessments[0].allocation_id
        assert worst.best_alternative_id in worst.explanation

    def test_when_everything_fails_the_ordering_stops_mattering(self):
        # Every project here sits behind a factor that can delay it, so in
        # the fully adverse future no ordering of the queue helps. Saying
        # so is more useful than implying a better order would have saved
        # it.
        assessments = assess_allocation_regret(_results())
        assert len({a.worst_case_value for a in assessments}) == 1
        assert all(a.worst_case_regret == 0.0 for a in assessments)
        assert any(a.regret > 0 for a in assessments)

    def test_worst_case_regret_appears_where_worst_cases_differ(self):
        # Drop project-d's own exposure: its fate is then decided purely
        # by the allocation, so the adverse futures are no longer equal.
        state = portfolio_state(factors=[platform_saturation_factor()])
        assessments = assess_allocation_regret(
            compare_allocations(
                portfolio_state=state, allocations=_candidates(state),
                horizon=horizon(), runs=300, seed=SEED,
            )
        )
        assert any(a.worst_case_regret > 0 for a in assessments)
        assert all(a.worst_case_regret >= 0 for a in assessments)

    def test_assessments_are_ordered_by_regret(self):
        regrets = [a.regret for a in assess_allocation_regret(_results())]
        assert regrets == sorted(regrets)

    def test_regret_cannot_be_negative(self):
        with pytest.raises(ValueError, match="Regret cannot be negative"):
            PortfolioRegretAssessment(
                allocation_id="a", expected_value=1.0,
                worst_case_value=0.0, regret=-0.5,
            )

    def test_a_regret_assessment_states_displaced_impact(self):
        with pytest.raises(ValueError, match="impact stated"):
            PortfolioRegretAssessment(
                allocation_id="a", expected_value=1.0,
                worst_case_value=0.0,
                displaced_project_ids=["project-c"],
            )

    def test_no_results_assesses_nothing(self):
        assert assess_allocation_regret([]) == []


class TestRecommendationStatesTheBill:
    """Required invariant: never optimize one project invisibly at
    another's expense."""

    def test_every_displaced_project_is_named_with_its_cost(self):
        recommendation = _recommend()
        assert recommendation.displaced_project_ids
        for project_id in recommendation.displaced_project_ids:
            assert project_id in recommendation.displaced_project_impacts
            assert project_id in recommendation.rationale

    def test_a_recommendation_cannot_hide_a_displacement(self):
        with pytest.raises(ValueError, match="must state displaced impact"):
            PortfolioAllocationRecommendation(
                id="r", resource_id=PLATFORM,
                recommended_allocation_id="a",
                displaced_project_ids=["project-c"],
                per_project_expected_impact={"project-c": -0.9},
            )

    def test_a_displaced_project_must_appear_in_the_outcome_summary(self):
        with pytest.raises(ValueError, match="portfolio outcome summary"):
            PortfolioAllocationRecommendation(
                id="r", resource_id=PLATFORM,
                recommended_allocation_id="a",
                displaced_project_ids=["project-c"],
                displaced_project_impacts={"project-c": 0.9},
            )

    def test_every_project_gets_an_expected_impact(self):
        recommendation = _recommend()
        assert set(recommendation.per_project_expected_impact) == {
            "project-a", "project-b", "project-c", "project-d",
        }

    def test_the_portfolio_total_is_reported_alongside_the_parts(self):
        recommendation = _recommend()
        assert recommendation.expected_portfolio_value == pytest.approx(
            sum(recommendation.per_project_expected_impact.values()),
            abs=1e-5,
        )

    def test_alternative_routes_are_surfaced_for_the_displaced(self):
        recommendation = _recommend()
        assert recommendation.alternative_routes
        assert any(
            routes for routes in recommendation.alternative_routes.values()
        )

    def test_a_project_with_no_route_is_called_out(self):
        state = portfolio_state()
        recommendation = _recommend(
            state=state, allocations=[stranding_allocation(state)],
        )
        assert recommendation.projects_without_alternative == ["project-c"]
        assert "no alternative route" in recommendation.rationale
        assert "told before the fact" in recommendation.rationale

    def test_the_rationale_names_who_proceeds_and_who_waits(self):
        recommendation = _recommend()
        assert "Prioritize" in recommendation.rationale
        assert "waits at a cost of" in recommendation.rationale

    def test_pruned_mass_is_disclosed_in_the_rationale(self):
        state = portfolio_state()
        recommendation = recommend_portfolio_allocation(
            portfolio_state=state, allocations=_candidates(state),
            horizon=horizon(minimum_branch_probability=0.2),
            runs=200, seed=SEED,
        )
        assert recommendation.unmodeled_probability_mass > 0
        assert "outside the modelled futures" in recommendation.rationale
        assert "not all possible ones" in recommendation.rationale


class TestChoosingTheAllocation:
    def test_the_highest_value_allocation_is_recommended(self):
        state = portfolio_state()
        results = _results(state)
        best = max(results, key=lambda r: r.portfolio_expected_value)
        assert _recommend(
            state=state,
        ).recommended_allocation_id == best.allocation_id

    def test_the_stranding_allocation_is_not_chosen(self):
        assert _recommend().recommended_allocation_id != (
            "alloc-strands-project-c"
        )

    def test_every_candidate_is_recorded_as_considered(self):
        recommendation = _recommend()
        assert len(recommendation.considered_allocation_ids) == 3
        assert "alloc-strands-project-c" in (
            recommendation.considered_allocation_ids
        )

    def test_risk_aversion_shifts_the_ranking_criterion(self):
        state = portfolio_state()
        neutral = _recommend(state=state, risk_aversion=0.0)
        averse = _recommend(state=state, risk_aversion=1.0)
        assert "by expected value" in neutral.rationale
        assert "risk aversion of 1.00" in averse.rationale

    def test_risk_aversion_is_bounded(self):
        with pytest.raises(ValueError, match="risk_aversion"):
            _recommend(risk_aversion=1.5)

    def test_at_least_one_candidate_is_required(self):
        with pytest.raises(ValueError, match="At least one candidate"):
            _recommend(allocations=[])

    def test_confidence_is_carried_and_kept_separate(self):
        recommendation = _recommend()
        assert recommendation.confidence == 0.5
        assert recommendation.confidence != (
            recommendation.expected_portfolio_value
        )

    def test_regret_for_every_candidate_is_attached(self):
        recommendation = _recommend()
        assert {a.allocation_id for a in recommendation.regret_assessments} == (
            set(recommendation.considered_allocation_ids)
        )

    def test_deterministic(self):
        assert _recommend().model_dump() == _recommend().model_dump()


class TestExplanation:
    def test_it_names_the_number_of_options_weighed(self):
        state = portfolio_state()
        result = simulate_portfolio_allocation(
            portfolio_state=state, allocation=weighted_allocation(state),
            horizon=horizon(), runs=200, seed=SEED,
        )
        explanation = explain_portfolio_allocation(
            result=result, considered=3, risk_aversion=0.0, stranded=[],
        )
        assert "Of 3 allocation(s) considered" in explanation
        assert PLATFORM in explanation

    def test_it_states_a_cost_for_each_displaced_project(self):
        state = portfolio_state()
        result = simulate_portfolio_allocation(
            portfolio_state=state, allocation=weighted_allocation(state),
            horizon=horizon(), runs=200, seed=SEED,
        )
        explanation = explain_portfolio_allocation(
            result=result, considered=1, risk_aversion=0.0, stranded=[],
        )
        for project_id in result.displaced_project_ids:
            assert f"{project_id} waits at a cost of" in explanation


class TestSingleFactorPortfolio:
    def test_a_shared_failure_hurts_every_allocation(self):
        state = portfolio_state(
            factors=[platform_saturation_factor(probability=0.9)],
        )
        results = compare_allocations(
            portfolio_state=state, allocations=_candidates(state),
            horizon=horizon(), runs=300, seed=SEED,
        )
        assert all(r.portfolio_expected_value < 0 for r in results), (
            "no allocation can route around a constraint that binds every "
            "project behind it — the honest answer is that the queue is "
            "the problem"
        )

    def test_capacity_relief_beats_reordering(self):
        tight = portfolio_state(capacity=2.0)
        relieved = portfolio_state(capacity=4.0)
        best_reorder = max(
            r.portfolio_expected_value for r in compare_allocations(
                portfolio_state=tight, allocations=_candidates(tight),
                horizon=horizon(), runs=300, seed=SEED,
            )
        )
        relieved_value = simulate_portfolio_allocation(
            portfolio_state=relieved,
            allocation=allocation_from_capacity(
                state=relieved, resource_id=PLATFORM,
            ),
            horizon=horizon(), runs=300, seed=SEED,
        ).portfolio_expected_value
        assert relieved_value > best_reorder, (
            "the highest-leverage move is widening the bottleneck, not "
            "rearranging the queue behind it"
        )
