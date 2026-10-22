"""Lattice generation, pruning, and tail mass."""

from __future__ import annotations

from datetime import timedelta

from tpllm.scenarios import (
    ScenarioEventType,
    ScenarioProbabilityMethod,
    assess_lattice_integrity,
    build_root_state,
    enumerate_paths,
    find_convergent_states,
    generate_scenario_lattice,
    omitted_probability_mass,
)
from tests._scenario_fixtures import (
    ContractScenarioGenerator,
    PROJECT,
    T0,
    factors,
    horizon,
    lattice,
    temporal_state,
)


class TestRootState:
    def test_root_built_from_temporal_snapshot(self):
        state = temporal_state()
        root = build_root_state(temporal_state=state, horizon=horizon())
        assert root.horizon_step == 0
        assert root.active_blockers == ["blk-contract"]
        assert root.unresolved_unknowns == ["unk-coexistence"]
        assert root.decision_states == {"dec-v3-conditional": "authoritative"}
        assert root.parent_state_ids == []

    def test_lattice_carries_the_evidence_cutoff(self):
        lat = lattice()
        # The forecast is anchored to what was known at as_of.
        assert lat.evidence_cutoff_at == temporal_state().evidence_cutoff_at

    def test_future_evidence_cannot_enter_the_root(self):
        # A state reconstructed at T0 knows only T0 facts; a later blocker
        # simply is not present.
        early = temporal_state(blockers=["blk-contract"])
        later = temporal_state(
            blockers=["blk-contract", "blk-discovered-later"],
            as_of=T0 + timedelta(days=5),
        )
        early_root = build_root_state(
            temporal_state=early, horizon=horizon(),
        )
        later_root = build_root_state(
            temporal_state=later, horizon=horizon(),
        )
        assert "blk-discovered-later" not in early_root.active_blockers
        assert "blk-discovered-later" in later_root.active_blockers


class TestBranching:
    def test_multiple_branches_created(self):
        lat = lattice()
        root_transitions = lat.outgoing(lat.root_state_id)
        # Approve / delay / reject.
        assert len(root_transitions) == 3
        event_types = {t.event_type for t in root_transitions}
        assert ScenarioEventType.DECISION_MADE in event_types
        assert ScenarioEventType.DECISION_DELAYED in event_types

    def test_sibling_probabilities_normalize(self):
        lat = lattice()
        total = sum(
            t.conditional_probability
            for t in lat.outgoing(lat.root_state_id)
        )
        assert abs(total - 1.0) < 0.01

    def test_conditional_prerequisites_gate_branches(self):
        lat = lattice()
        approve = next(
            t for t in lat.outgoing(lat.root_state_id)
            if t.event_type == ScenarioEventType.DECISION_MADE
        )
        delay = next(
            t for t in lat.outgoing(lat.root_state_id)
            if t.event_type == ScenarioEventType.DECISION_DELAYED
        )
        # Integration events follow approval; workaround follows delay.
        after_approve = {
            t.event_type for t in lat.outgoing(approve.to_state_id)
        }
        after_delay = {t.event_type for t in lat.outgoing(delay.to_state_id)}
        assert ScenarioEventType.INTEGRATION_PASSES in after_approve
        assert ScenarioEventType.WORKAROUND_ADOPTED not in after_approve
        assert ScenarioEventType.WORKAROUND_ADOPTED in after_delay
        assert ScenarioEventType.INTEGRATION_PASSES not in after_delay

    def test_terminal_outcome_states_stop_expanding(self):
        lat = lattice()
        for state in lat.states:
            if state.outcome_labels:
                assert lat.outgoing(state.id) == [], (
                    "a state with a modeled outcome must be terminal, or "
                    "one path accumulates contradictory outcomes"
                )

    def test_paths_converge_on_the_same_outcome(self):
        lat = lattice()
        on_time = [
            p for p in enumerate_paths(lattice=lat)
            if "launch_on_time" in p.outcome_labels
        ]
        # Approval->integration, delay->workaround, delay->scope cut.
        assert len(on_time) >= 3

    def test_lattice_has_convergent_states(self):
        # Multiple parents is what makes this a lattice, not a tree.
        assert find_convergent_states(lattice())


class TestPruningAndTailMass:
    def _pruned(self):
        return lattice(hz=horizon(minimum_branch_probability=0.20))

    def test_low_probability_branches_pruned(self):
        baseline = lattice()
        pruned = self._pruned()
        assert len(pruned.transitions) < len(baseline.transitions)

    def test_pruned_mass_is_preserved_not_hidden(self):
        pruned = self._pruned()
        assert pruned.tail_masses
        assert omitted_probability_mass(pruned) > 0
        tail = pruned.tail_masses[0]
        assert tail.omitted_branch_count >= 1
        assert "omitted" in tail.explanation

    def test_integrity_report_discloses_omission(self):
        report = assess_lattice_integrity(self._pruned())
        assert report.omitted_probability > 0
        assert "pruned into tail mass" in report.explanation
        assert "not every possible future" in report.explanation

    def test_high_impact_branch_survives_pruning(self):
        # cand-reject has probability ~0.15 but impact 0.9; at a 0.20
        # threshold it must be preserved anyway.
        pruned = self._pruned()
        reject_transitions = [
            t for t in pruned.transitions
            if t.event_type == ScenarioEventType.DECISION_REOPENED
        ]
        assert reject_transitions, (
            "a rare catastrophic branch must not be pruned away"
        )
        assert reject_transitions[0].impact >= 0.7

    def test_depth_limit_respected(self):
        lat = lattice(hz=horizon(maximum_depth=1))
        assert max(s.horizon_step for s in lat.states) == 1


class TestDeterminism:
    def test_generation_is_deterministic(self):
        assert lattice().model_dump() == lattice().model_dump()

    def test_method_labeled_heuristic_by_default(self):
        assert lattice().method == ScenarioProbabilityMethod.HEURISTIC_V1

    def test_empty_generator_yields_root_only(self):
        class EmptyGenerator:
            def generate_events(self, *, temporal_state, horizon, context):
                return []

        lat = generate_scenario_lattice(
            temporal_state=temporal_state(),
            generator=EmptyGenerator(),
            horizon=horizon(),
            extra_factors=factors(),
        )
        assert len(lat.states) == 1
        assert lat.transitions == []
