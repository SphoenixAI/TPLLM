"""Tests for Commit 27 — outcome utility and expected value."""

from __future__ import annotations

import pytest

from lltpm.coordination import (
    ScenarioOutcomeUtility,
    analyse_option_lattice,
    calculate_expected_option_utility,
    calculate_expected_utility_from_lattice,
    default_outcome_utilities,
    normalize_utility_to_unit,
    outcome_distribution,
)
from tests._scenario_decision_fixtures import (
    assessment,
    risky_lattice,
    safe_lattice,
    utilities,
)


class TestUtilityModel:
    def test_utility_bounded(self):
        with pytest.raises(ValueError, match="within"):
            ScenarioOutcomeUtility(
                outcome_id="x", utility=1.5, rationale="too good",
            )

    def test_rationale_required(self):
        with pytest.raises(ValueError, match="rationale"):
            ScenarioOutcomeUtility(
                outcome_id="x", utility=0.5, rationale="   ",
            )

    def test_utility_is_not_purely_financial(self):
        # The model carries a rationale precisely so non-financial value
        # can be expressed and inspected.
        utility = ScenarioOutcomeUtility(
            outcome_id="commitment_preserved", utility=0.7,
            rationale="A partner commitment is preserved.",
        )
        assert "commitment" in utility.rationale

    def test_defaults_are_transparent(self):
        defaults = default_outcome_utilities()
        assert {u.outcome_id for u in defaults} == {
            "launch_on_time", "launch_slips",
        }
        assert all(u.rationale for u in defaults)

    def test_normalization_maps_to_unit_interval(self):
        assert normalize_utility_to_unit(-1.0) == 0.0
        assert normalize_utility_to_unit(0.0) == 0.5
        assert normalize_utility_to_unit(1.0) == 1.0


class TestOutcomeDistribution:
    def test_distribution_covers_named_outcomes(self):
        distribution = outcome_distribution(
            lattice=safe_lattice(),
            outcome_ids=["launch_on_time", "launch_slips"],
        )
        assert set(distribution) == {"launch_on_time", "launch_slips"}
        assert all(0.0 <= p <= 1.0 for p in distribution.values())
        assert sum(distribution.values()) <= 1.0

    def test_safe_option_carries_more_success_mass(self):
        safe = outcome_distribution(
            lattice=safe_lattice(), outcome_ids=["launch_on_time"],
        )["launch_on_time"]
        risky = outcome_distribution(
            lattice=risky_lattice(), outcome_ids=["launch_on_time"],
        )["launch_on_time"]
        assert safe > risky


class TestExpectedUtility:
    def test_lattice_expectation_reports_covered_mass(self):
        expected, covered = calculate_expected_utility_from_lattice(
            lattice=safe_lattice(), outcome_utilities=utilities(),
        )
        assert -1.0 <= expected <= 1.0
        assert 0.0 < covered <= 1.0

    def test_uncovered_mass_is_reported_not_hidden(self):
        # Only one outcome is priced, so covered mass must fall short of 1.
        partial = [
            ScenarioOutcomeUtility(
                outcome_id="launch_on_time", utility=1.0,
                rationale="Only success is priced here.",
            )
        ]
        _, covered = calculate_expected_utility_from_lattice(
            lattice=safe_lattice(), outcome_utilities=partial,
        )
        assert covered < 1.0

    def test_no_utilities_yields_zero(self):
        assert calculate_expected_utility_from_lattice(
            lattice=safe_lattice(), outcome_utilities=[],
        ) == (0.0, 0.0)

    def test_safe_option_has_higher_expected_utility(self):
        safe = calculate_expected_option_utility(
            analysis=analyse_option_lattice(
                option_id="opt-b", lattice=safe_lattice(),
                assessment=assessment("opt-b"),
                outcome_utilities=utilities(),
            ),
            outcome_utilities=utilities(),
        )
        risky = calculate_expected_option_utility(
            analysis=analyse_option_lattice(
                option_id="opt-a", lattice=risky_lattice(),
                assessment=assessment("opt-a", reversible=False),
                outcome_utilities=utilities(),
            ),
            outcome_utilities=utilities(),
        )
        assert safe > risky

    def test_unlabeled_remainder_is_treated_as_neutral(self):
        from types import SimpleNamespace

        # 50% success, 10% severe, 40% unlabeled. The remainder must not
        # be counted as favourable.
        value = calculate_expected_option_utility(
            analysis=SimpleNamespace(
                target_success_probability=0.5,
                severe_failure_probability=0.1,
            ),
            outcome_utilities=utilities(),
        )
        assert value == pytest.approx(0.5 * 1.0 + 0.1 * -0.6)
