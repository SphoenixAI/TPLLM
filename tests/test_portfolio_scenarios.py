"""The portfolio scenario lattice.

Reuses the lattice invariants at portfolio scale: terminal outcomes do
not expand, pruned mass is preserved, and convergent paths are counted
once each rather than once per parent.
"""

from __future__ import annotations

import pytest

from lltpm.portfolio import (
    PortfolioAllocationDecision,
    ProjectScenarioStatus,
    apply_factor_outcomes,
    baseline_project_statuses,
    build_portfolio_lattice,
    convergent_portfolio_states,
    omitted_portfolio_probability_mass,
    portfolio_outcome_probability,
    project_status_value,
    state_probabilities,
    total_displayed_portfolio_probability,
)
from tests._portfolio_scenario_fixtures import (
    PLATFORM,
    T0,
    account_graph_factor,
    demand,
    four_approval_demands,
    horizon,
    partner_ops_factor,
    platform_saturation_factor,
    portfolio_state,
    shared_factors,
    stranding_allocation,
    weighted_allocation,
)


def _lattice(**kwargs):
    state = kwargs.pop("state", None) or portfolio_state()
    allocation = kwargs.pop("allocation", None) or weighted_allocation(state)
    return build_portfolio_lattice(
        state=state,
        allocation=allocation,
        horizon=kwargs.pop("horizon", None) or horizon(**kwargs),
    )


class TestAllocationShape:
    def test_a_demand_cannot_be_both_served_and_displaced(self):
        with pytest.raises(ValueError, match="both served and displaced"):
            PortfolioAllocationDecision(
                id="a", resource_id=PLATFORM,
                served_demand_ids=["dem-a"], displaced_demand_ids=["dem-a"],
            )

    def test_only_displaced_demands_take_an_alternative_route(self):
        with pytest.raises(ValueError, match="alternative route"):
            PortfolioAllocationDecision(
                id="a", resource_id=PLATFORM,
                served_demand_ids=["dem-a"],
                routed_demand_ids={"dem-a": "Platform Deputy"},
            )

    def test_the_canonical_allocation_serves_two_and_displaces_two(self):
        allocation = weighted_allocation()
        assert set(allocation.served_demand_ids) == {"dem-a", "dem-c"}
        assert set(allocation.displaced_demand_ids) == {"dem-b", "dem-d"}
        assert allocation.routed_demand_ids["dem-b"] == "Platform Deputy"


class TestBaselineStatuses:
    def test_served_projects_start_on_track(self):
        statuses = baseline_project_statuses(
            demands=four_approval_demands(),
            allocation=weighted_allocation(),
        )
        assert statuses["project-a"] == ProjectScenarioStatus.ON_TRACK
        assert statuses["project-c"] == ProjectScenarioStatus.ON_TRACK

    def test_a_displaced_project_with_a_route_is_not_simply_blocked(self):
        statuses = baseline_project_statuses(
            demands=four_approval_demands(),
            allocation=weighted_allocation(),
        )
        assert statuses["project-b"] == (
            ProjectScenarioStatus.DELIVERED_VIA_WORKAROUND
        )

    def test_a_displaced_project_without_a_route_is_blocked(self):
        statuses = baseline_project_statuses(
            demands=four_approval_demands(),
            allocation=stranding_allocation(),
        )
        assert statuses["project-c"] == (
            ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT
        ), "project-c has nowhere else to go, so displacing it blocks it"


class TestFactorApplication:
    def test_one_draw_degrades_every_project_it_touches(self):
        baseline = baseline_project_statuses(
            demands=four_approval_demands(),
            allocation=weighted_allocation(),
        )
        factor = platform_saturation_factor()
        statuses = apply_factor_outcomes(
            baseline=baseline, factors=[factor], outcomes={factor.id: True},
        )
        for project_id in factor.affected_project_ids:
            assert statuses[project_id] == ProjectScenarioStatus.DELAYED
        assert statuses["project-d"] != ProjectScenarioStatus.DELAYED, (
            "an untouched project must not move because of someone else's "
            "shared factor"
        )

    def test_a_factor_that_does_not_fire_changes_nothing(self):
        baseline = baseline_project_statuses(
            demands=four_approval_demands(),
            allocation=weighted_allocation(),
        )
        factor = platform_saturation_factor()
        assert apply_factor_outcomes(
            baseline=baseline, factors=[factor], outcomes={factor.id: False},
        ) == baseline

    def test_a_second_failure_does_not_stack_past_delayed(self):
        baseline = baseline_project_statuses(
            demands=four_approval_demands(),
            allocation=weighted_allocation(),
        )
        factors = [account_graph_factor(), platform_saturation_factor()]
        statuses = apply_factor_outcomes(
            baseline=baseline, factors=factors,
            outcomes={f.id: True for f in factors},
        )
        assert statuses["project-a"] == ProjectScenarioStatus.DELAYED


class TestStatusValue:
    def test_progress_is_positive_and_waiting_is_negative(self):
        demands = [d for d in four_approval_demands() if d.id == "dem-c"]
        assert project_status_value(
            status=ProjectScenarioStatus.ON_TRACK, demands=demands,
        ) > 0
        assert project_status_value(
            status=ProjectScenarioStatus.DELAYED, demands=demands,
        ) < 0

    def test_waiting_costs_less_when_there_is_another_route(self):
        with_route = [d for d in four_approval_demands() if d.id == "dem-b"]
        without = [d for d in four_approval_demands() if d.id == "dem-c"]
        assert project_status_value(
            status=ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT,
            demands=with_route,
        ) > project_status_value(
            status=ProjectScenarioStatus.DELAYED_BY_DISPLACEMENT,
            demands=without,
        )

    def test_a_project_with_no_demands_is_neutral(self):
        assert project_status_value(
            status=ProjectScenarioStatus.DELAYED, demands=[],
        ) == 0.0


class TestLatticeStructure:
    def test_root_holds_the_allocation_baseline(self):
        lattice = _lattice()
        root = lattice.state(lattice.root_state_id)
        assert root.horizon_step == 0
        assert root.project_statuses["project-a"] == (
            ProjectScenarioStatus.ON_TRACK
        )
        assert not root.is_terminal

    def test_every_project_appears_in_the_lattice(self):
        assert _lattice().project_ids == [
            "project-a", "project-b", "project-c", "project-d",
        ]

    def test_transitions_carry_the_factor_that_caused_them(self):
        lattice = _lattice()
        factor_ids = {t.shared_factor_id for t in lattice.transitions}
        assert factor_ids == {f.id for f in shared_factors()}

    def test_transitions_name_the_projects_the_factor_touches(self):
        lattice = _lattice()
        platform = next(
            t for t in lattice.transitions
            if t.shared_factor_id == "factor-platform-saturated"
        )
        assert platform.affected_project_ids == [
            "project-a", "project-b", "project-c",
        ]
        assert "project-a" in platform.event_description

    def test_probability_and_confidence_stay_separate(self):
        lattice = _lattice()
        account_graph = next(
            t for t in lattice.transitions
            if t.shared_factor_id == "factor-account-graph-incident"
            and t.fired
        )
        assert account_graph.conditional_probability == pytest.approx(0.25)
        assert account_graph.probability_confidence == pytest.approx(0.6)

    def test_deterministic(self):
        assert _lattice().model_dump() == _lattice().model_dump()


class TestTerminalOutcomesDoNotExpand:
    """Lattice invariant, re-asserted at portfolio scale."""

    def test_no_terminal_state_has_an_outgoing_transition(self):
        lattice = _lattice()
        assert lattice.terminal_states
        for state in lattice.terminal_states:
            assert lattice.outgoing(state.id) == [], (
                f"{state.id} carries an outcome and must not expand further"
            )

    def test_terminal_states_label_every_project(self):
        lattice = _lattice()
        for state in lattice.terminal_states:
            for project_id in lattice.project_ids:
                assert any(
                    label.startswith(f"{project_id}:")
                    for label in state.outcome_labels
                )

    def test_terminal_states_carry_a_portfolio_level_label(self):
        lattice = _lattice()
        for state in lattice.terminal_states:
            assert any(
                label.startswith("portfolio_")
                for label in state.outcome_labels
            )


class TestConvergentPathsDoNotDoubleCount:
    def test_some_states_have_several_parents(self):
        convergent = convergent_portfolio_states(_lattice())
        assert convergent, (
            "different combinations of shared failures can leave the "
            "portfolio in the same place — that is what makes this a "
            "lattice and not a tree"
        )
        assert all(len(set(s.parent_state_ids)) > 1 for s in convergent)

    def test_a_convergent_state_is_counted_once_per_path(self):
        lattice = _lattice()
        probabilities = state_probabilities(lattice)
        convergent = convergent_portfolio_states(lattice)[0]
        expected = sum(
            probabilities[t.from_state_id] * t.conditional_probability
            for t in lattice.incoming(convergent.id)
        )
        assert probabilities[convergent.id] == pytest.approx(expected)
        assert probabilities[convergent.id] <= 1.0

    def test_no_state_exceeds_certainty(self):
        for probability in state_probabilities(_lattice()).values():
            assert 0.0 <= probability <= 1.0 + 1e-9


class TestTailMassIsPreserved:
    def test_displayed_and_omitted_mass_sum_to_one(self):
        lattice = _lattice()
        displayed = total_displayed_portfolio_probability(lattice)
        omitted = omitted_portfolio_probability_mass(lattice)
        assert displayed + omitted == pytest.approx(1.0)

    def test_pruning_moves_mass_to_the_tail_rather_than_losing_it(self):
        lattice = _lattice(minimum_branch_probability=0.2)
        displayed = total_displayed_portfolio_probability(lattice)
        omitted = omitted_portfolio_probability_mass(lattice)
        assert omitted > 0
        assert displayed < 1.0, (
            "displayed futures must not appear to sum to 100% once "
            "branches have been pruned"
        )
        assert displayed + omitted == pytest.approx(1.0)

    def test_capping_active_states_also_preserves_mass(self):
        lattice = _lattice(maximum_active_states=2)
        displayed = total_displayed_portfolio_probability(lattice)
        omitted = omitted_portfolio_probability_mass(lattice)
        assert omitted > 0
        assert displayed + omitted == pytest.approx(1.0)

    def test_pruned_states_are_not_also_displayed(self):
        lattice = _lattice(maximum_active_states=2)
        state_ids = {s.id for s in lattice.states}
        for transition in lattice.transitions:
            assert transition.to_state_id in state_ids

    def test_an_unexpanded_factor_is_disclosed(self):
        lattice = _lattice(maximum_depth=1)
        explanations = " ".join(t.explanation for t in lattice.tail_masses)
        assert "horizon depth" in explanations
        assert "smaller than the real one" in explanations

    def test_tail_masses_explain_themselves(self):
        lattice = _lattice(minimum_branch_probability=0.2)
        for tail in lattice.tail_masses:
            assert tail.explanation
            assert tail.omitted_branch_count >= 1


class TestOutcomeProbabilities:
    def test_portfolio_outcomes_sum_to_the_displayed_mass(self):
        lattice = _lattice()
        labels = [
            "portfolio_all_projects_progressing",
            "portfolio_single_project_delayed",
            "portfolio_multiple_projects_delayed",
        ]
        total = sum(
            portfolio_outcome_probability(lattice=lattice, label=label)
            for label in labels
        )
        assert total == pytest.approx(
            total_displayed_portfolio_probability(lattice)
        )

    def test_an_unknown_label_has_no_mass(self):
        assert portfolio_outcome_probability(
            lattice=_lattice(), label="portfolio_everything_is_fine_forever",
        ) == 0.0

    def test_stranding_the_routeless_project_worsens_the_portfolio(self):
        state = portfolio_state()
        good = build_portfolio_lattice(
            state=state, allocation=weighted_allocation(state),
            horizon=horizon(),
        )
        bad = build_portfolio_lattice(
            state=state, allocation=stranding_allocation(state),
            horizon=horizon(),
        )
        assert portfolio_outcome_probability(
            lattice=bad, label="portfolio_multiple_projects_delayed",
        ) > portfolio_outcome_probability(
            lattice=good, label="portfolio_multiple_projects_delayed",
        )


class TestDegenerateInputs:
    def test_a_portfolio_with_no_shared_factors_still_builds(self):
        lattice = _lattice(state=portfolio_state(factors=[]))
        assert lattice.states == [lattice.state(lattice.root_state_id)]
        assert lattice.terminal_states
        assert total_displayed_portfolio_probability(lattice) == (
            pytest.approx(1.0)
        )

    def test_a_certain_factor_produces_one_branch(self):
        state = portfolio_state(
            factors=[platform_saturation_factor(probability=1.0)],
        )
        lattice = _lattice(state=state)
        assert all(t.fired for t in lattice.transitions)
        assert total_displayed_portfolio_probability(lattice) == (
            pytest.approx(1.0)
        )

    def test_a_single_project_portfolio(self):
        state = portfolio_state(
            demands=[
                demand(
                    demand_id="dem-solo", project_id="project-solo",
                    criticality=0.8,
                ),
            ],
            factors=[partner_ops_factor()],
            project_ids=["project-solo"],
        )
        lattice = _lattice(state=state)
        assert lattice.project_ids == ["project-solo"]
        assert total_displayed_portfolio_probability(lattice) == (
            pytest.approx(1.0)
        )
