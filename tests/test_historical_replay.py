"""Leak-free historical replay."""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.evaluation import (
    ReplayLeakageError,
    assert_replay_is_leak_free,
    build_replay_case,
    build_replay_series,
    collect_observed_events,
    reconstruct_for_replay,
)
from lltpm.temporal import TemporalEventType
from tests._scenario_fixtures import horizon, lattice, temporal_state
from tests._temporal_fixtures import PROJECT, at, event

E = TemporalEventType


def _history():
    return [
        # Known before the prediction point.
        event("e-early", E.CLAIM_SUPPORTED, "claim-1",
              occurred_at=at(-24), known_at=at(-24)),
        event("e-blocker", E.BLOCKER_OPENED, "blk-1",
              occurred_at=at(-12), known_at=at(-12)),
        # Occurred before the prediction point but discovered after it.
        event("e-hidden", E.BLOCKER_OPENED, "blk-hidden",
              occurred_at=at(-1), known_at=at(6)),
        # Happens inside the forecast window.
        event("e-outcome", E.MILESTONE_SLIPS if hasattr(
            E, "MILESTONE_SLIPS") else E.BLOCKER_OPENED, "milestone-1",
              occurred_at=at(48), known_at=at(50)),
    ]


class TestLeakFreeReconstruction:
    def test_replay_excludes_future_evidence(self):
        state = reconstruct_for_replay(
            project_id=PROJECT, temporal_events=_history(),
            prediction_as_of=at(0),
        )
        assert "blk-1" in state.active_blocker_ids
        # Occurred before the prediction point, but was not yet known.
        assert "blk-hidden" not in state.active_blocker_ids

    def test_later_prediction_point_sees_more(self):
        later = reconstruct_for_replay(
            project_id=PROJECT, temporal_events=_history(),
            prediction_as_of=at(12),
        )
        assert "blk-hidden" in later.active_blocker_ids

    def test_cutoff_is_recorded(self):
        state = reconstruct_for_replay(
            project_id=PROJECT, temporal_events=_history(),
            prediction_as_of=at(0),
        )
        assert state.evidence_cutoff_at == at(0)


class TestLeakageGuard:
    def test_leaky_state_raises(self):
        state = reconstruct_for_replay(
            project_id=PROJECT, temporal_events=_history(),
            prediction_as_of=at(12),
        )
        with pytest.raises(ReplayLeakageError, match="after the prediction"):
            assert_replay_is_leak_free(
                state=state, lattice=None, prediction_as_of=at(0),
            )

    def test_clean_state_passes(self):
        state = reconstruct_for_replay(
            project_id=PROJECT, temporal_events=_history(),
            prediction_as_of=at(0),
        )
        assert_replay_is_leak_free(
            state=state, lattice=None, prediction_as_of=at(0),
        )

    def test_leaky_lattice_raises(self):
        state = reconstruct_for_replay(
            project_id=PROJECT, temporal_events=_history(),
            prediction_as_of=at(0),
        )
        future_lattice = lattice().model_copy(
            update={"evidence_cutoff_at": at(24)},
        )
        with pytest.raises(ReplayLeakageError, match="Lattice evidence"):
            assert_replay_is_leak_free(
                state=state, lattice=future_lattice,
                prediction_as_of=at(0),
            )


class TestObservedOutcomes:
    def test_outcomes_selected_by_reality_time(self):
        observed = collect_observed_events(
            temporal_events=_history(), project_id=PROJECT,
            prediction_as_of=at(0), horizon_end=at(72),
        )
        # The question is what happened, not when it was noticed.
        assert [e.id for e in observed] == ["e-outcome"]

    def test_events_outside_the_window_excluded(self):
        observed = collect_observed_events(
            temporal_events=_history(), project_id=PROJECT,
            prediction_as_of=at(0), horizon_end=at(24),
        )
        assert observed == []

    def test_other_projects_excluded(self):
        events = _history() + [
            event("e-other", E.BLOCKER_OPENED, "blk-other",
                  occurred_at=at(48), known_at=at(48),
                  project_id="phoenix"),
        ]
        observed = collect_observed_events(
            temporal_events=events, project_id=PROJECT,
            prediction_as_of=at(0), horizon_end=at(72),
        )
        assert "e-other" not in [e.id for e in observed]


class TestReplayCase:
    def test_case_records_state_and_outcomes(self):
        case, state = build_replay_case(
            project_id=PROJECT, temporal_events=_history(),
            prediction_as_of=at(0), horizon_end=at(72),
            observed_outcomes={"launch_on_time": False},
        )
        assert case.prediction_as_of == at(0)
        assert case.reconstructed_state_id == state.state_fingerprint
        assert case.observed_event_ids == ["e-outcome"]
        assert case.observed_outcomes == {"launch_on_time": False}

    def test_case_is_leak_checked_on_construction(self):
        leaky = lattice().model_copy(
            update={"evidence_cutoff_at": at(48)},
        )
        with pytest.raises(ReplayLeakageError):
            build_replay_case(
                project_id=PROJECT, temporal_events=_history(),
                prediction_as_of=at(0), horizon_end=at(72),
                lattice=leaky,
            )

    def test_rolling_series_is_ordered_and_leak_free(self):
        series = build_replay_series(
            project_id=PROJECT, temporal_events=_history(),
            prediction_points=[at(12), at(0), at(6)],
            horizon=horizon(),
        )
        points = [case.prediction_as_of for case, _ in series]
        assert points == sorted(points)
        for case, state in series:
            assert state.evidence_cutoff_at <= case.prediction_as_of

    def test_replay_is_deterministic(self):
        first, _ = build_replay_case(
            project_id=PROJECT, temporal_events=_history(),
            prediction_as_of=at(0), horizon_end=at(72),
        )
        second, _ = build_replay_case(
            project_id=PROJECT, temporal_events=_history(),
            prediction_as_of=at(0), horizon_end=at(72),
        )
        assert first.model_dump() == second.model_dump()
