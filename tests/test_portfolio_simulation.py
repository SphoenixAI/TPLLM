"""Simulating the portfolio under an allocation.

The invariants under test:

- a shared uncertainty is drawn once per run and read by every project
  it touches, so correlated projects move together
- independent per-project uncertainty stays independent
- the same capacity spent in a different order produces a different
  portfolio outcome
- no displaced project's impact is ever left unstated
- the same seed reproduces the run exactly
"""

from __future__ import annotations

import pytest

from tpllm.portfolio import (
    PortfolioOutcomeSummary,
    PortfolioSimulationResult,
    ProjectScenarioStatus,
    allocation_from_capacity,
    allocation_from_order,
    allocation_from_recommendation,
    bottleneck_state,
    build_priority_recommendation,
    detect_bottlenecks,
    simulate_portfolio_allocation,
)
from tests._portfolio_scenario_fixtures import (
    PLATFORM,
    T0,
    approval_window,
    four_approval_demands,
    horizon,
    partner_ops_factor,
    platform_saturation_factor,
    portfolio_state,
    shared_factors,
    stranding_allocation,
    weighted_allocation,
)

RUNS = 3000
SEED = 20261116


def _simulate(state=None, allocation=None, **kwargs):
    state = state or portfolio_state()
    allocation = allocation or weighted_allocation(state)
    return simulate_portfolio_allocation(
        portfolio_state=state,
        allocation=allocation,
        horizon=kwargs.pop("horizon", None) or horizon(),
        runs=kwargs.pop("runs", RUNS),
        seed=kwargs.pop("seed", SEED),
        **kwargs,
    )


class TestSharedUncertaintyMovesProjectsTogether:
    """The central invariant of portfolio simulation."""

    def test_every_shared_factor_moved_all_its_projects_together(self):
        result = _simulate()
        for factor_id, co_movement in result.factor_co_movement.items():
            assert co_movement == 1.0, (
                f"{factor_id} was read inconsistently across the projects "
                f"it affects — the shared draw did not reach all of them"
            )

    def test_a_certain_shared_failure_delays_every_affected_project(self):
        state = portfolio_state(
            factors=[platform_saturation_factor(probability=1.0)],
        )
        result = _simulate(state=state, runs=200)
        for project_id in ("project-a", "project-b", "project-c"):
            assert result.outcome(project_id).outcome_probabilities[
                ProjectScenarioStatus.DELAYED.value
            ] == 1.0

    def test_an_unaffected_project_does_not_move_with_them(self):
        state = portfolio_state(
            factors=[platform_saturation_factor(probability=1.0)],
        )
        result = _simulate(state=state, runs=200)
        assert ProjectScenarioStatus.DELAYED.value not in (
            result.outcome("project-d").outcome_probabilities
        ), "project-d shares nothing with the Platform queue"

    def test_correlated_projects_fail_together_far_more_than_by_chance(self):
        # A single 50% shared factor across three projects. Were it drawn
        # per project, all three would fail together in about 12.5% of
        # runs; drawn once, it is about 50%.
        state = portfolio_state(
            factors=[platform_saturation_factor(probability=0.5)],
        )
        result = _simulate(state=state, runs=RUNS)
        joint = result.portfolio_outcome_frequencies.get(
            "portfolio_multiple_projects_delayed", 0.0,
        )
        assert 0.45 < joint < 0.55, (
            "three projects behind one queue are one bet, not three"
        )

    def test_independent_exposure_is_not_swept_into_the_shared_draw(self):
        result = _simulate()
        # Partner Ops affects only project-d and fires at its own rate.
        assert result.shared_factor_frequencies[
            "factor-partner-ops-slip"
        ] == pytest.approx(0.30, abs=0.03)
        assert result.shared_factor_frequencies[
            "factor-platform-saturated"
        ] == pytest.approx(0.40, abs=0.03)

    def test_observed_frequencies_track_the_stated_probabilities(self):
        result = _simulate()
        for factor in shared_factors():
            assert result.shared_factor_frequencies[
                factor.id
            ] == pytest.approx(factor.probability, abs=0.03)


class TestNothingIsDisplacedInvisibly:
    """Required invariant: never optimize one project at another's
    expense without saying so."""

    def test_every_displaced_project_has_a_stated_impact(self):
        result = _simulate()
        assert result.displaced_project_ids
        for project_id in result.displaced_project_ids:
            assert project_id in result.displaced_project_impacts

    def test_a_result_cannot_omit_a_displaced_impact(self):
        with pytest.raises(ValueError, match="without a stated cost"):
            PortfolioSimulationResult(
                id="r", allocation_id="a", resource_id=PLATFORM,
                runs=1, seed=1,
                displaced_project_ids=["project-b"],
                displaced_project_impacts={},
            )

    def test_every_project_appears_in_the_outcome_summary(self):
        result = _simulate()
        assert {o.project_id for o in result.project_outcomes} == {
            "project-a", "project-b", "project-c", "project-d",
        }

    def test_the_displaced_projects_alternative_routes_are_named(self):
        result = _simulate()
        assert result.alternative_routes["project-b"] == [
            "Platform Deputy", "Continue on contract v2",
        ]

    def test_a_stranded_project_is_shown_with_no_route(self):
        state = portfolio_state()
        result = _simulate(state=state, allocation=stranding_allocation(state))
        outcome = result.outcome("project-c")
        assert outcome.was_displaced is True
        assert outcome.alternative_routes == []
        assert "no alternative route" in outcome.explanation

    def test_the_explanation_states_the_displacement_costs(self):
        result = _simulate()
        assert "Displaced, with cost stated" in result.explanation
        assert "project-b" in result.explanation

    def test_the_explanation_says_shared_draws_were_shared(self):
        result = _simulate()
        assert "drawn once per run" in result.explanation
        assert "correlated projects move together" in result.explanation

    def test_no_displacement_is_stated_plainly(self):
        state = portfolio_state(capacity=4.0)
        result = _simulate(
            state=state,
            allocation=allocation_from_capacity(
                state=state, resource_id=PLATFORM,
            ),
            runs=200,
        )
        assert result.displaced_project_ids == []
        assert "No project was displaced" in result.explanation


class TestAllocationOrderChangesOutcomes:
    def test_a_different_order_produces_a_different_portfolio_value(self):
        state = portfolio_state()
        good = _simulate(state=state, allocation=weighted_allocation(state))
        bad = _simulate(state=state, allocation=stranding_allocation(state))
        assert good.portfolio_expected_value != bad.portfolio_expected_value

    def test_serving_the_routeless_project_beats_stranding_it(self):
        state = portfolio_state()
        good = _simulate(state=state, allocation=weighted_allocation(state))
        bad = _simulate(state=state, allocation=stranding_allocation(state))
        assert good.portfolio_expected_value > bad.portfolio_expected_value, (
            "displacing the project with nowhere to go costs the portfolio "
            "more than displacing projects that have a workaround"
        )

    def test_a_different_order_displaces_different_projects(self):
        state = portfolio_state()
        assert set(
            _simulate(
                state=state, allocation=weighted_allocation(state),
            ).displaced_project_ids
        ) != set(
            _simulate(
                state=state, allocation=stranding_allocation(state),
            ).displaced_project_ids
        )

    def test_worst_case_is_reported_alongside_the_expectation(self):
        result = _simulate()
        assert result.worst_case_portfolio_value < (
            result.portfolio_expected_value
        )

    def test_more_capacity_improves_the_portfolio(self):
        tight = portfolio_state(capacity=2.0)
        loose = portfolio_state(capacity=4.0)
        assert _simulate(
            state=loose,
            allocation=allocation_from_capacity(
                state=loose, resource_id=PLATFORM,
            ),
        ).portfolio_expected_value > _simulate(
            state=tight, allocation=weighted_allocation(tight),
        ).portfolio_expected_value


class TestReproducibility:
    def test_the_same_seed_reproduces_the_run(self):
        assert _simulate().model_dump() == _simulate().model_dump()

    def test_a_different_seed_gives_a_different_sample(self):
        assert _simulate(seed=1).shared_factor_frequencies != (
            _simulate(seed=2).shared_factor_frequencies
        )

    def test_seed_and_run_count_are_reported(self):
        result = _simulate(runs=500, seed=77)
        assert result.runs == 500
        assert result.seed == 77
        assert "seed=77" in result.explanation

    def test_zero_runs_is_rejected(self):
        with pytest.raises(ValueError, match="runs must be positive"):
            _simulate(runs=0)


class TestHonestyOfTheReport:
    def test_outcome_probabilities_sum_to_one_per_project(self):
        result = _simulate()
        for outcome in result.project_outcomes:
            assert sum(
                outcome.outcome_probabilities.values()
            ) == pytest.approx(1.0)

    def test_confidence_is_separate_from_probability(self):
        result = _simulate()
        outcome = result.outcome("project-a")
        assert outcome.probability_confidence == 0.5
        assert outcome.probability_confidence not in (
            outcome.outcome_probabilities.values()
        )

    def test_confidence_takes_the_weakest_input(self):
        state = portfolio_state(
            factors=[platform_saturation_factor()],
        )
        # The capacity window's confidence is 0.6, the factor's 0.5.
        assert _simulate(
            state=state, runs=100,
        ).outcome("project-a").probability_confidence == 0.5

    def test_unmodeled_mass_is_carried_from_the_lattice(self):
        result = _simulate(horizon=horizon(minimum_branch_probability=0.2))
        assert result.unmodeled_probability_mass > 0, (
            "pruned futures must be disclosed, not silently dropped"
        )

    def test_the_method_is_labelled_honestly(self):
        assert _simulate(runs=100).method == (
            "seeded_monte_carlo_over_shared_factors"
        )

    def test_portfolio_frequencies_sum_to_one(self):
        result = _simulate()
        assert sum(
            result.portfolio_outcome_frequencies.values()
        ) == pytest.approx(1.0)


class TestAllocationBuilders:
    def test_weighted_allocation_serves_the_stranded_project(self):
        allocation = weighted_allocation()
        assert "dem-c" in allocation.served_demand_ids

    def test_an_order_is_honoured_even_when_it_is_a_poor_one(self):
        state = portfolio_state()
        allocation = allocation_from_order(
            state=state, resource_id=PLATFORM,
            ordered_demand_ids=["dem-d", "dem-b", "dem-a", "dem-c"],
            available_units=2.0,
        )
        assert allocation.served_demand_ids == ["dem-d", "dem-b"]

    def test_demands_for_another_resource_are_ignored(self):
        state = portfolio_state()
        allocation = allocation_from_order(
            state=state, resource_id="Partner Ops",
            ordered_demand_ids=["dem-a", "dem-b"],
            available_units=2.0,
        )
        assert allocation.served_demand_ids == []

    def test_a_missing_capacity_window_is_an_error(self):
        with pytest.raises(ValueError, match="No capacity window"):
            allocation_from_capacity(
                state=portfolio_state(), resource_id="Nonexistent Queue",
            )

    def test_a_commit_28_recommendation_can_be_simulated(self):
        demands = four_approval_demands()
        window = approval_window(capacity=2.0)
        bottleneck = detect_bottlenecks(
            demands=demands, capacity_windows=[window],
        )[0]
        recommendation = build_priority_recommendation(
            bottleneck=bottleneck, demands=demands, capacity_window=window,
        )
        state = bottleneck_state(
            bottleneck=bottleneck, demands=demands,
            capacity_windows=[window], shared_factors=shared_factors(),
            as_of=T0,
        )
        allocation = allocation_from_recommendation(
            state=state, recommendation=recommendation, available_units=2.0,
        )
        assert set(allocation.served_demand_ids) == {"dem-a", "dem-c"}

        result = simulate_portfolio_allocation(
            portfolio_state=state, allocation=allocation,
            horizon=horizon(), runs=200, seed=SEED,
        )
        assert set(result.displaced_project_ids) == {
            "project-b", "project-d",
        }
        assert result.displaced_project_impacts["project-b"] > 0

    def test_bottleneck_state_carries_the_bottleneck_evidence(self):
        demands = four_approval_demands()
        window = approval_window(capacity=2.0)
        bottleneck = detect_bottlenecks(
            demands=demands, capacity_windows=[window],
        )[0]
        state = bottleneck_state(
            bottleneck=bottleneck, demands=demands,
            capacity_windows=[window], shared_factors=[], as_of=T0,
        )
        assert "ev-approval-queue" in state.evidence_ids


class TestNoWorkerScoring:
    def test_simulation_source_models_resources_not_people(self):
        import ast
        import inspect

        import tpllm.portfolio.optimization as optimization
        import tpllm.portfolio.scenarios as scenarios
        import tpllm.portfolio.shared_factors as shared
        import tpllm.portfolio.simulation as simulation

        def executable(module):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if not isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef,
                     ast.AsyncFunctionDef),
                ):
                    continue
                body = node.body
                if (
                    body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body.pop(0)
            return ast.unparse(tree).lower()

        for module in (scenarios, shared, simulation, optimization):
            source = executable(module)
            for forbidden in (
                "employee_score", "performance_score", "productivity",
                "responsiveness", "individual_output", "tickets_closed",
                "competence", "loyalty", "attitude",
            ):
                assert forbidden not in source, (
                    f"{module.__name__} must not model '{forbidden}' — this "
                    f"layer forecasts organizational mechanics, not human "
                    f"worth."
                )


class TestOutcomeSummaryModel:
    def test_defaults_are_inert(self):
        summary = PortfolioOutcomeSummary(project_id="project-x")
        assert summary.expected_impact == 0.0
        assert summary.was_displaced is False
        assert summary.alternative_routes == []
