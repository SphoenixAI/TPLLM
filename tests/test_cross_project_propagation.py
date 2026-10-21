"""Tests for Commit 29 — how one project's delay reaches another.

The arithmetic rules under test: two routes through the same shared
resource are one mechanism and counted once, and genuinely distinct
mechanisms combine by noisy-OR so a probability never exceeds one.
"""

from __future__ import annotations

import pytest

from lltpm.portfolio import (
    TRANSMISSION_BY_NODE_TYPE,
    CrossProjectImpact,
    build_portfolio_graph,
    combine_independent_transmissions,
    projects_at_risk_from,
    propagate_delay_across_projects,
    stranded_downstream_projects,
    summarize_cross_project_propagation,
)
from tests._portfolio_scenario_fixtures import (
    ACCOUNT_GRAPH,
    PLATFORM,
    four_approval_demands,
    four_project_graph_inputs,
)


def _graph():
    return build_portfolio_graph(**four_project_graph_inputs())


def _impacts(source: str = "project-a", **kwargs):
    return propagate_delay_across_projects(
        graph=_graph(),
        source_project_id=source,
        demands=four_approval_demands(),
        **kwargs,
    )


class TestNoisyOr:
    def test_two_mechanisms_combine_below_certainty(self):
        assert combine_independent_transmissions([0.6, 0.6]) == (
            pytest.approx(0.84)
        )

    def test_summing_would_have_exceeded_one(self):
        combined = combine_independent_transmissions([0.7, 0.7, 0.7])
        assert combined < 1.0
        assert combined > 0.7

    def test_a_single_mechanism_passes_through(self):
        assert combine_independent_transmissions([0.45]) == (
            pytest.approx(0.45)
        )

    def test_no_mechanism_is_no_risk(self):
        assert combine_independent_transmissions([]) == 0.0

    def test_certainty_stays_certain(self):
        assert combine_independent_transmissions([1.0, 0.3]) == 1.0


class TestPropagationRoutes:
    def test_projects_behind_the_same_approval_are_reached(self):
        reached = {i.affected_project_id for i in _impacts()}
        assert reached == {"project-b", "project-c"}

    def test_an_unconnected_project_is_not_reached(self):
        assert "project-d" not in {i.affected_project_id for i in _impacts()}, (
            "project-d shares no resource with project-a; inventing an "
            "impact would be a false alarm"
        )

    def test_two_shared_resources_transmit_more_than_one(self):
        impacts = {i.affected_project_id: i for i in _impacts()}
        # C shares both the approval authority and the Account Graph
        # service with A; B shares only the authority.
        assert len(impacts["project-c"].route_resource_ids) == 2
        assert len(impacts["project-b"].route_resource_ids) == 1
        assert (
            impacts["project-c"].transmission_probability
            > impacts["project-b"].transmission_probability
        )

    def test_the_combined_probability_matches_noisy_or(self):
        impact = next(
            i for i in _impacts() if i.affected_project_id == "project-c"
        )
        expected = combine_independent_transmissions([
            TRANSMISSION_BY_NODE_TYPE[
                _graph().node(f"authority:{PLATFORM}").node_type
            ],
            TRANSMISSION_BY_NODE_TYPE[
                _graph().node(f"service:{ACCOUNT_GRAPH}").node_type
            ],
        ])
        assert impact.transmission_probability == pytest.approx(expected)

    def test_one_resource_reached_twice_is_counted_once(self):
        # A reaches C directly through the Platform queue, and again
        # through B which shares the same queue. That is one contended
        # resource, not two independent risks.
        impact = next(
            i for i in _impacts(maximum_hops=3)
            if i.affected_project_id == "project-c"
        )
        assert impact.route_resource_ids == sorted(
            set(impact.route_resource_ids)
        )
        assert impact.transmission_probability == pytest.approx(
            next(
                i for i in _impacts(maximum_hops=1)
                if i.affected_project_id == "project-c"
            ).transmission_probability
        )

    def test_a_probability_never_exceeds_one(self):
        for impact in _impacts(maximum_hops=3, source_severity=1.0):
            assert 0.0 <= impact.transmission_probability <= 1.0

    def test_a_milder_source_delay_transmits_less(self):
        strong = _impacts(source_severity=1.0)[0]
        mild = _impacts(source_severity=0.3)[0]
        assert mild.transmission_probability < strong.transmission_probability

    def test_results_are_ordered_by_exposure(self):
        probabilities = [i.transmission_probability for i in _impacts()]
        assert probabilities == sorted(probabilities, reverse=True)

    def test_deterministic(self):
        assert [i.model_dump() for i in _impacts()] == [
            i.model_dump() for i in _impacts()
        ]

    def test_rejects_a_zero_hop_request(self):
        with pytest.raises(ValueError, match="maximum_hops"):
            _impacts(maximum_hops=0)


class TestWhoAbsorbsIt:
    def test_a_project_without_a_route_is_flagged(self):
        assert stranded_downstream_projects(_impacts()) == ["project-c"]

    def test_a_project_with_a_deputy_is_not_flagged(self):
        impact = next(
            i for i in _impacts() if i.affected_project_id == "project-b"
        )
        assert impact.affected_has_alternative_route is True

    def test_projects_above_a_risk_threshold(self):
        assert projects_at_risk_from(
            impacts=_impacts(), minimum_probability=0.7,
        ) == ["project-c"]

    def test_explanations_name_the_route_and_the_absence_of_one(self):
        impacts = {i.affected_project_id: i for i in _impacts()}
        assert "no alternative route" in impacts["project-c"].explanation
        assert "alternative route" in impacts["project-b"].explanation
        assert PLATFORM in impacts["project-b"].explanation

    def test_summary_names_who_absorbs_the_delay(self):
        summary = summarize_cross_project_propagation(_impacts())
        assert "project-c absorbs the delay directly" in summary
        assert "told before the fact" in summary

    def test_summary_when_nothing_propagates(self):
        assert "No modelled route" in summarize_cross_project_propagation([])

    def test_an_isolated_source_propagates_nothing(self):
        assert _impacts(source="project-d") == []


class TestImpactModel:
    def test_a_project_does_not_propagate_to_itself(self):
        with pytest.raises(ValueError, match="itself"):
            CrossProjectImpact(
                id="x", source_project_id="project-a",
                affected_project_id="project-a",
                transmission_probability=0.5,
            )

    def test_probability_and_confidence_are_bounded(self):
        with pytest.raises(ValueError, match="transmission_probability"):
            CrossProjectImpact(
                id="x", source_project_id="a", affected_project_id="b",
                transmission_probability=1.5,
            )
        with pytest.raises(ValueError, match="probability_confidence"):
            CrossProjectImpact(
                id="x", source_project_id="a", affected_project_id="b",
                transmission_probability=0.5, probability_confidence=1.5,
            )

    def test_confidence_is_carried_separately_from_probability(self):
        impact = _impacts(confidence=0.2)[0]
        assert impact.probability_confidence == 0.2
        assert impact.transmission_probability != 0.2
        assert "confidence 0.20" in impact.explanation


class TestTransmissionIsStructural:
    def test_weights_describe_resource_types_not_people(self):
        import ast
        import inspect

        import lltpm.portfolio.propagation as propagation

        tree = ast.parse(inspect.getsource(propagation))
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
        source = ast.unparse(tree).lower()
        for forbidden in (
            "reliability_score", "responsiveness", "productivity",
            "employee", "performance_score", "individual_output",
        ):
            assert forbidden not in source, (
                f"propagation must not model '{forbidden}' — transmission "
                f"is a property of shared resources, not of people."
            )


class TestNoLeakTerm:
    """Commit 39 — a documented departure from the canonical noisy-OR.

    The Bayesian-network form usually carries a leak: a standing
    probability that the effect happens even when every modelled cause
    is absent. It is left out here on purpose, and a test keeps it a
    decision rather than something that drifts in.
    """

    def test_no_modelled_mechanism_means_no_claim(self):
        assert combine_independent_transmissions([]) == 0.0, (
            "a leak term would give every project a floor probability "
            "with no mechanism behind it; absence of a mechanism must "
            "read as no claim"
        )

    def test_a_certain_mechanism_saturates_without_exceeding_one(self):
        assert combine_independent_transmissions([1.0, 0.5]) == 1.0

    def test_two_moderate_routes_combine_below_their_sum(self):
        assert combine_independent_transmissions([0.6, 0.6]) == 0.84

    def test_the_error_direction_is_under_estimation(self):
        """Stated as a property: with no leak, the combined probability
        can never exceed what the modelled mechanisms justify."""
        mechanisms = [0.3, 0.4, 0.2]
        combined = combine_independent_transmissions(mechanisms)
        assert combined < sum(mechanisms)
        assert combined >= max(mechanisms)
