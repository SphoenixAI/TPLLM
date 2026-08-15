"""Tests for Commit 24 — seeded Monte Carlo simulation."""

from __future__ import annotations

import pytest

from lltpm.scenarios import (
    probability_of_outcome,
    simulate_scenario_lattice,
)
from tests._scenario_fixtures import lattice


class TestReproducibility:
    def test_same_seed_same_result(self):
        lat = lattice()
        a = simulate_scenario_lattice(lattice=lat, runs=500, seed=42)
        b = simulate_scenario_lattice(lattice=lat, runs=500, seed=42)
        assert a.model_dump() == b.model_dump()

    def test_different_seeds_differ(self):
        lat = lattice()
        a = simulate_scenario_lattice(lattice=lat, runs=500, seed=1)
        b = simulate_scenario_lattice(lattice=lat, runs=500, seed=2)
        assert a.outcome_frequencies != b.outcome_frequencies

    def test_zero_runs_rejected(self):
        with pytest.raises(ValueError, match="runs must be positive"):
            simulate_scenario_lattice(lattice=lattice(), runs=0, seed=1)


class TestSimulationAgreement:
    def test_frequencies_approximate_analytic_probability(self):
        lat = lattice()
        report = simulate_scenario_lattice(lattice=lat, runs=4000, seed=7)
        analytic, _ = probability_of_outcome(
            lattice=lat, outcome_label="launch_on_time",
        )
        simulated = report.outcome_frequencies.get("launch_on_time", 0.0)
        # Simulation supplements the analytic lattice; they should agree
        # within sampling error.
        assert simulated == pytest.approx(analytic, abs=0.05)

    def test_report_records_provenance(self):
        report = simulate_scenario_lattice(
            lattice=lattice(), runs=100, seed=3,
        )
        assert report.runs == 100
        assert report.seed == 3
        assert report.method == "heuristic_v1"
        assert "does not replace it" in report.explanation
        assert report.mean_path_length > 0

    def test_terminal_state_frequencies_sum_to_one(self):
        report = simulate_scenario_lattice(
            lattice=lattice(), runs=1000, seed=11,
        )
        assert sum(report.terminal_state_frequencies.values()) == (
            pytest.approx(1.0, abs=0.001)
        )


class TestSimulationDoesNotReplaceTheGraph:
    def test_lattice_remains_the_auditable_artifact(self):
        lat = lattice()
        simulate_scenario_lattice(lattice=lat, runs=200, seed=5)
        # Simulation must not mutate the interpretable graph.
        assert lat.model_dump() == lattice().model_dump()
