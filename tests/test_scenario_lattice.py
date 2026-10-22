"""Lattice structure, convergence, and honest
probability accounting."""

from __future__ import annotations

import pytest

from lltpm.scenarios import (
    assess_lattice_integrity,
    enumerate_paths,
    find_convergent_states,
    omitted_probability_mass,
    total_displayed_probability,
)
from tests._scenario_fixtures import horizon, lattice


class TestLatticeStructure:
    def test_states_can_have_multiple_parents(self):
        convergent = find_convergent_states(lattice())
        assert convergent, (
            "distinct paths must be able to converge on one future state — "
            "that is what makes this a lattice rather than a tree"
        )
        assert any(len(s.parent_state_ids) > 1 for s in convergent)

    def test_root_has_no_parents(self):
        lat = lattice()
        root = lat.state(lat.root_state_id)
        assert root.parent_state_ids == []
        assert root.horizon_step == 0

    def test_every_transition_endpoints_exist(self):
        lat = lattice()
        ids = {s.id for s in lat.states}
        for transition in lat.transitions:
            assert transition.from_state_id in ids
            assert transition.to_state_id in ids

    def test_horizon_steps_increase_monotonically(self):
        lat = lattice()
        for transition in lat.transitions:
            source = lat.state(transition.from_state_id)
            target = lat.state(transition.to_state_id)
            assert target.horizon_step > source.horizon_step


class TestIntegrityAccounting:
    def test_report_counts_match(self):
        lat = lattice()
        report = assess_lattice_integrity(lat)
        assert report.state_count == len(lat.states)
        assert report.transition_count == len(lat.transitions)
        assert report.path_count == len(enumerate_paths(lattice=lat))

    def test_unpruned_lattice_displays_full_mass(self):
        report = assess_lattice_integrity(lattice())
        assert report.displayed_probability == pytest.approx(1.0, abs=0.01)
        assert report.omitted_probability == 0.0

    def test_pruned_lattice_discloses_the_gap(self):
        pruned = lattice(hz=horizon(minimum_branch_probability=0.20))
        report = assess_lattice_integrity(pruned)
        assert report.omitted_probability > 0
        # Displayed paths must not falsely appear to sum to 100%.
        assert report.displayed_probability < 1.0
        assert "not every possible future" in report.explanation

    def test_displayed_plus_residual_is_coherent(self):
        pruned = lattice(hz=horizon(minimum_branch_probability=0.20))
        report = assess_lattice_integrity(pruned)
        assert report.displayed_probability + report.residual_uncertainty == (
            pytest.approx(1.0, abs=0.001)
        )

    def test_method_is_surfaced_in_the_report(self):
        assert assess_lattice_integrity(lattice()).method == "heuristic_v1"

    def test_helper_functions_agree_with_report(self):
        pruned = lattice(hz=horizon(minimum_branch_probability=0.20))
        report = assess_lattice_integrity(pruned)
        paths = enumerate_paths(lattice=pruned)
        assert total_displayed_probability(paths) == pytest.approx(
            report.displayed_probability
        )
        assert omitted_probability_mass(pruned) == pytest.approx(
            report.omitted_probability
        )
