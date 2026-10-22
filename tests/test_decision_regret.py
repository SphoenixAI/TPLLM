"""Regret, optionality, and recoverability."""

from __future__ import annotations

import pytest

from tpllm.coordination import (
    DecisionPosture,
    analyse_option_lattice,
    assess_optionality,
    assess_recoverability,
    calculate_decision_regret,
    lowest_regret_option,
)
from tests._scenario_decision_fixtures import (
    assessment,
    comparable_lattice,
    risky_lattice,
    safe_lattice,
    utilities,
)


def _analyses():
    return [
        analyse_option_lattice(
            option_id="opt-a", lattice=risky_lattice(),
            assessment=assessment("opt-a", reversible=False),
            outcome_utilities=utilities(),
        ),
        analyse_option_lattice(
            option_id="opt-b", lattice=safe_lattice(),
            assessment=assessment("opt-b"),
            outcome_utilities=utilities(),
        ),
    ]


class TestRegret:
    def test_best_option_has_zero_expected_regret(self):
        regrets = calculate_decision_regret(
            option_analyses=_analyses(), outcome_utilities=utilities(),
        )
        assert min(r.expected_regret for r in regrets) == 0.0

    def test_worse_option_carries_positive_regret(self):
        by_id = {
            r.option_id: r
            for r in calculate_decision_regret(
                option_analyses=_analyses(), outcome_utilities=utilities(),
            )
        }
        assert by_id["opt-a"].expected_regret > 0.0
        assert by_id["opt-b"].expected_regret == 0.0

    def test_regret_is_relative_not_absolute(self):
        # Against itself alone, an option has no regret — regret is a
        # comparison between available choices.
        solo = calculate_decision_regret(
            option_analyses=_analyses()[:1], outcome_utilities=utilities(),
        )
        assert solo[0].expected_regret == 0.0

    def test_opportunity_cost_is_explained(self):
        by_id = {
            r.option_id: r
            for r in calculate_decision_regret(
                option_analyses=_analyses(), outcome_utilities=utilities(),
            )
        }
        assert "forgoes" in by_id["opt-a"].opportunity_cost_summary
        assert "opt-b" in by_id["opt-a"].opportunity_cost_summary
        assert "no opportunity cost" in (
            by_id["opt-b"].opportunity_cost_summary
        )

    def test_worst_case_regret_reported_separately(self):
        by_id = {
            r.option_id: r
            for r in calculate_decision_regret(
                option_analyses=_analyses(), outcome_utilities=utilities(),
            )
        }
        # Both quantities exist and are distinct concepts.
        assert by_id["opt-a"].worst_case_regret >= 0.0
        assert by_id["opt-a"].expected_regret >= 0.0

    def test_lowest_regret_option_selected(self):
        assert lowest_regret_option(
            calculate_decision_regret(
                option_analyses=_analyses(), outcome_utilities=utilities(),
            )
        ) == "opt-b"

    def test_empty_input(self):
        assert calculate_decision_regret(
            option_analyses=[], outcome_utilities=utilities(),
        ) == []
        assert lowest_regret_option([]) is None

    def test_regret_carries_its_confidence(self):
        regrets = calculate_decision_regret(
            option_analyses=_analyses(), outcome_utilities=utilities(),
        )
        assert all(0.0 <= r.regret_confidence <= 1.0 for r in regrets)


class TestOptionality:
    def test_score_bounded(self):
        for lattice in (risky_lattice(), safe_lattice()):
            score = assess_optionality(
                option_id="x", lattice=lattice,
            ).optionality_score
            assert 0.0 <= score <= 1.0

    def test_recovery_route_raises_optionality(self):
        safe = assess_optionality(option_id="b", lattice=safe_lattice())
        risky = assess_optionality(option_id="a", lattice=risky_lattice())
        assert safe.optionality_score > risky.optionality_score

    def test_irreversible_commitments_counted(self):
        risky = assess_optionality(option_id="a", lattice=risky_lattice())
        assert risky.irreversible_commitment_count > 0

    def test_reversible_branches_counted(self):
        safe = assess_optionality(option_id="b", lattice=safe_lattice())
        assert safe.reversible_branch_count > 0

    def test_explanation_states_the_counts(self):
        safe = assess_optionality(option_id="b", lattice=safe_lattice())
        assert "future(s) remain reachable" in safe.explanation


class TestRecoverability:
    def test_score_bounded(self):
        for lattice in (risky_lattice(), safe_lattice()):
            score = assess_recoverability(
                option_id="x", lattice=lattice,
            ).recoverability_score
            assert 0.0 <= score <= 1.0

    def test_option_with_a_way_back_scores_higher(self):
        safe = assess_recoverability(option_id="b", lattice=safe_lattice())
        risky = assess_recoverability(option_id="a", lattice=risky_lattice())
        assert safe.recoverability_score > risky.recoverability_score

    def test_recovery_routes_identified(self):
        safe = assess_recoverability(option_id="b", lattice=safe_lattice())
        assert safe.dominant_recovery_paths, (
            "an option whose failure can be worked around must expose the "
            "route that does it"
        )

    def test_option_without_recovery_has_none(self):
        risky = assess_recoverability(option_id="a", lattice=risky_lattice())
        assert risky.dominant_recovery_paths == []
        assert risky.recoverability_score == 0.0

    def test_failure_paths_counted(self):
        safe = assess_recoverability(option_id="b", lattice=safe_lattice())
        assert safe.failure_path_count > 0
        assert safe.recoverable_failure_path_count <= safe.failure_path_count

    def test_explanation_states_the_ratio(self):
        safe = assess_recoverability(option_id="b", lattice=safe_lattice())
        assert "recovery route" in safe.explanation
