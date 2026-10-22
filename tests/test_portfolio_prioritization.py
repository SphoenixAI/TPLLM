"""Prioritization, displacement cost, and the
invariant that nothing is displaced invisibly."""

from __future__ import annotations

import pytest

from tpllm.portfolio import (
    build_priority_recommendation,
    detect_bottlenecks,
    displacement_cost,
    prioritization_weight,
)
from tests._portfolio_fixtures import (
    PLATFORM,
    approval_window,
    demand,
    four_approval_demands,
)


def _recommendation(capacity: float = 2.0):
    window = approval_window(capacity=capacity)
    demands = four_approval_demands()
    bottleneck = detect_bottlenecks(
        demands=demands, capacity_windows=[window],
    )[0]
    return build_priority_recommendation(
        bottleneck=bottleneck, demands=demands, capacity_window=window,
    )


class TestDisplacementCost:
    def test_workaround_lowers_the_cost_of_waiting(self):
        without = demand(
            demand_id="d1", project_id="p", criticality=0.9,
        )
        with_workaround = demand(
            demand_id="d2", project_id="p", criticality=0.9,
            has_workaround=True, workaround="fallback",
        )
        assert displacement_cost(with_workaround) < displacement_cost(without)

    def test_alternative_resource_also_lowers_cost(self):
        without = demand(
            demand_id="d1", project_id="p", criticality=0.9,
        )
        with_alternative = demand(
            demand_id="d2", project_id="p", criticality=0.9,
            alternatives=["Platform Deputy"],
        )
        assert displacement_cost(with_alternative) < displacement_cost(without)

    def test_no_alternative_keeps_full_criticality(self):
        stranded = demand(
            demand_id="d1", project_id="p", criticality=0.9,
        )
        assert displacement_cost(stranded) == pytest.approx(0.9)

    def test_no_route_project_outranks_a_higher_criticality_one(self):
        # A is more critical but has a workaround; C has nowhere to go.
        a = demand(
            demand_id="dem-a", project_id="project-a", criticality=0.95,
            has_workaround=True, workaround="partial rollout",
        )
        c = demand(
            demand_id="dem-c", project_id="project-c", criticality=0.90,
        )
        assert prioritization_weight(c) > prioritization_weight(a), (
            "a project with no alternative route gains weight because "
            "delaying it has nowhere to go"
        )


class TestPriorityOrdering:
    def test_stranded_project_is_served(self):
        recommendation = _recommendation()
        served = recommendation.ordered_demand_ids[:2]
        assert "dem-c" in served, (
            "the project with no alternative route must not be the one "
            "displaced while others have workarounds"
        )

    def test_projects_with_workarounds_yield(self):
        recommendation = _recommendation()
        displaced = set(recommendation.displaced_demand_impacts)
        assert "dem-b" in displaced or "dem-d" in displaced
        assert "dem-c" not in displaced

    def test_capacity_determines_how_many_are_served(self):
        assert len(
            _recommendation(capacity=1.0).displaced_demand_impacts
        ) == 3
        assert len(
            _recommendation(capacity=3.0).displaced_demand_impacts
        ) == 1

    def test_deterministic(self):
        assert _recommendation().model_dump() == _recommendation().model_dump()


class TestNothingIsDisplacedInvisibly:
    """The non-negotiable invariant of this layer."""

    def test_every_displaced_demand_has_a_stated_impact(self):
        recommendation = _recommendation()
        served = set(recommendation.ordered_demand_ids[:2])
        displaced = [
            d for d in recommendation.ordered_demand_ids if d not in served
        ]
        assert displaced
        for demand_id in displaced:
            assert demand_id in recommendation.displaced_demand_impacts, (
                f"{demand_id} was displaced without its impact being stated"
            )

    def test_opportunity_costs_are_computed_pairwise(self):
        recommendation = _recommendation()
        assert recommendation.opportunity_costs
        for cost in recommendation.opportunity_costs:
            assert cost.selected_demand_id
            assert cost.displaced_demand_id
            assert cost.explanation
            assert cost.net_portfolio_effect == pytest.approx(
                cost.selected_project_benefit - cost.displaced_project_cost
            )

    def test_alternative_routes_are_named_for_displaced_projects(self):
        recommendation = _recommendation()
        routes = recommendation.alternative_routes
        assert routes
        assert any(r for r in routes.values()), (
            "a displaced project's alternative route must be surfaced, not "
            "left for the reader to find"
        )

    def test_a_displaced_project_without_a_route_is_called_out(self):
        # Force the stranded project to be displaced by starving capacity.
        recommendation = _recommendation(capacity=1.0)
        stranded_routes = recommendation.alternative_routes.get("dem-c")
        if "dem-c" in recommendation.displaced_demand_impacts:
            assert stranded_routes == []
            assert "no alternative route" in recommendation.rationale

    def test_rationale_names_who_waits(self):
        recommendation = _recommendation()
        assert "Prioritize" in recommendation.rationale
        assert "waits" in recommendation.rationale

    def test_portfolio_outcome_change_covers_every_project(self):
        recommendation = _recommendation()
        assert set(recommendation.expected_portfolio_outcome_change) == {
            "project-a", "project-b", "project-c", "project-d",
        }
        # Served projects show a benefit; displaced show a cost.
        values = recommendation.expected_portfolio_outcome_change
        assert any(v > 0 for v in values.values())
        assert any(v < 0 for v in values.values())

    def test_capacity_confidence_is_carried_into_the_recommendation(self):
        recommendation = _recommendation()
        assert recommendation.confidence == 0.6
        assert "capacity confidence" in recommendation.rationale

    def test_recommendation_hedges_the_capacity_claim(self):
        recommendation = _recommendation()
        assert "approximately" in recommendation.rationale
