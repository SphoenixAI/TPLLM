"""Bottleneck ranking, leverage, and the queries
that only become answerable across projects."""

from __future__ import annotations

import pytest

from tpllm.portfolio import (
    CapacityResourceType,
    assess_intervention_leverage,
    biggest_shared_bottleneck,
    build_portfolio_graph,
    detect_bottlenecks,
    explain_portfolio_risk,
    highest_leverage_intervention,
    invisible_portfolio_risk,
    projects_competing_for_authority,
    projects_without_alternative_route,
    rank_interventions_by_leverage,
    shared_failure_services,
    who_can_be_delayed_safely,
)
from tests._portfolio_fixtures import (
    ACCOUNT_GRAPH,
    PLATFORM,
    T0,
    approval_window,
    demand,
    four_approval_demands,
    four_project_graph_inputs,
)


def _bottlenecks(capacity: float = 2.0):
    return detect_bottlenecks(
        demands=four_approval_demands(),
        capacity_windows=[approval_window(capacity=capacity)],
    )


def _graph():
    return build_portfolio_graph(**four_project_graph_inputs())


class TestBottleneckRanking:
    def test_biggest_bottleneck_by_reach_then_overload(self):
        wide = _bottlenecks()[0]
        narrow_demands = [
            demand(
                demand_id="dem-x", project_id="project-x", criticality=0.9,
                resource_id="Narrow Queue", units=5.0,
            ),
        ]
        narrow = detect_bottlenecks(
            demands=narrow_demands,
            capacity_windows=[
                approval_window(resource_id="Narrow Queue", capacity=1.0),
            ],
        )[0]
        # Narrow has a worse ratio but touches one project; reach wins.
        assert narrow.overload_ratio > wide.overload_ratio
        assert biggest_shared_bottleneck([wide, narrow]).id == wide.id

    def test_empty_input(self):
        assert biggest_shared_bottleneck([]) is None


class TestInterventionLeverage:
    def test_one_intervention_across_projects_beats_separate_pings(self):
        shared = assess_intervention_leverage(
            intervention_id="int-contract-standard",
            description="Resolve the Platform contract standard once",
            bottlenecks=_bottlenecks(),
            coordination_cost=0.3,
        )
        single = assess_intervention_leverage(
            intervention_id="int-single-ping",
            description="Ping Platform about project-c only",
            bottlenecks=detect_bottlenecks(
                demands=[
                    demand(
                        demand_id="dem-c2", project_id="project-c",
                        criticality=0.9, units=3.0,
                    ),
                ],
                capacity_windows=[approval_window(capacity=1.0)],
            ),
            coordination_cost=0.3,
        )
        assert shared.leverage_score > single.leverage_score
        assert len(shared.affected_project_ids) == 4

    def test_leverage_falls_as_coordination_cost_rises(self):
        cheap = assess_intervention_leverage(
            intervention_id="i1", description="d",
            bottlenecks=_bottlenecks(), coordination_cost=0.2,
        )
        expensive = assess_intervention_leverage(
            intervention_id="i1", description="d",
            bottlenecks=_bottlenecks(), coordination_cost=0.9,
        )
        assert cheap.leverage_score > expensive.leverage_score

    def test_explanation_names_the_reach(self):
        leverage = assess_intervention_leverage(
            intervention_id="int-1",
            description="Resolve the Platform contract standard once",
            bottlenecks=_bottlenecks(),
        )
        assert "4 project(s)" in leverage.explanation
        assert "leverage" in leverage.explanation

    def test_ranking_and_selection(self):
        high = assess_intervention_leverage(
            intervention_id="int-high", description="wide",
            bottlenecks=_bottlenecks(), coordination_cost=0.2,
        )
        low = assess_intervention_leverage(
            intervention_id="int-low", description="narrow",
            bottlenecks=_bottlenecks(), coordination_cost=0.9,
        )
        ranked = rank_interventions_by_leverage([low, high])
        assert ranked[0].intervention_id == "int-high"
        assert highest_leverage_intervention([low, high]).intervention_id == (
            "int-high"
        )

    def test_no_interventions(self):
        assert highest_leverage_intervention([]) is None


class TestPortfolioQueries:
    def test_projects_competing_for_the_same_authority(self):
        competing = projects_competing_for_authority(graph=_graph())
        assert competing == {
            f"authority:{PLATFORM}": [
                "project-a", "project-b", "project-c",
            ],
        }

    def test_shared_failure_services(self):
        services = shared_failure_services(graph=_graph())
        assert [s.label for s in services] == [ACCOUNT_GRAPH]

    def test_who_can_be_delayed_safely(self):
        safe = who_can_be_delayed_safely(four_approval_demands())
        safe_ids = [d.id for d in safe]
        # C has no alternative route, so it is never in this list.
        assert "dem-c" not in safe_ids
        # The cheapest displacement comes first.
        assert safe_ids[0] == "dem-b"

    def test_projects_without_an_alternative_route(self):
        assert projects_without_alternative_route(
            four_approval_demands(),
        ) == ["project-c"]

    def test_invisible_portfolio_risk(self):
        domains = invisible_portfolio_risk(
            graph=_graph(), demands=four_approval_demands(),
        )
        assert domains
        assert domains[0].resource_id == PLATFORM
        # Each project sees a local dependency; only the combined view
        # shows one failure taking several down together.
        assert len(domains[0].project_ids) == 4

    def test_risk_explanation_names_the_stranded_project(self):
        explanation = explain_portfolio_risk(
            invisible_portfolio_risk(
                graph=_graph(), demands=four_approval_demands(),
            )
        )
        assert PLATFORM in explanation
        assert "project-c" in explanation
        assert "no alternative route" in explanation

    def test_no_shared_risk_is_stated_plainly(self):
        assert "No shared failure domain" in explain_portfolio_risk([])


class TestNoWorkerScoring:
    def test_portfolio_source_models_resources_not_people(self):
        import ast
        import inspect
        import tpllm.portfolio.capacity as capacity
        import tpllm.portfolio.graph as graph_mod
        import tpllm.portfolio.models as models
        import tpllm.portfolio.prioritization as prioritization
        import tpllm.portfolio.queries as queries

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

        for module in (
            models, graph_mod, capacity, prioritization, queries,
        ):
            source = executable(module)
            for forbidden in (
                "employee_score", "performance_score", "productivity",
                "responsiveness", "individual_output", "tickets_closed",
            ):
                assert forbidden not in source, (
                    f"{module.__name__} must not model '{forbidden}' — "
                    f"capacity is a structural resource estimate."
                )
