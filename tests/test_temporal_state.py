"""Bitemporal semantics and state reconstruction."""

from __future__ import annotations

import pytest

from lltpm.temporal import (
    ProjectTemporalState,
    TemporalEvent,
    TemporalEventType,
    TemporalFactWindow,
    assert_no_future_leakage,
    build_state_fingerprint,
    events_known_at,
    events_true_at,
    reconstruct_project_state,
)
from tests._temporal_fixtures import PROJECT, T0, at, event

E = TemporalEventType


class TestTemporalFactWindow:
    def test_reality_and_knowledge_time_diverge(self):
        # Failed at 09:00, discovered at 10:15.
        window = TemporalFactWindow(
            valid_from=at(0), known_from=at(1.25),
        )
        # At 09:30 it was true but not yet known.
        assert window.was_true_at(at(0.5)) is True
        assert window.was_known_at(at(0.5)) is False
        # By 10:30 both hold.
        assert window.was_true_at(at(1.5)) is True
        assert window.was_known_at(at(1.5)) is True

    def test_closed_windows(self):
        window = TemporalFactWindow(
            valid_from=at(0), valid_until=at(2),
            known_from=at(0), known_until=at(3),
        )
        assert window.was_true_at(at(2.5)) is False
        assert window.was_known_at(at(2.5)) is True

    def test_invalid_windows_rejected(self):
        with pytest.raises(ValueError, match="valid_until"):
            TemporalFactWindow(
                valid_from=at(2), valid_until=at(1), known_from=at(2),
            )
        with pytest.raises(ValueError, match="known_until"):
            TemporalFactWindow(
                valid_from=at(0), known_from=at(2), known_until=at(1),
            )


class TestTemporalEvent:
    def test_cannot_be_known_before_it_happens(self):
        with pytest.raises(ValueError, match="known before it happens"):
            TemporalEvent(
                id="e1", project_id=PROJECT,
                event_type=E.EVIDENCE_OBSERVED, occurred_at=at(2),
                known_at=at(1), source_object_id="x", summary="s",
            )

    def test_knowledge_lag_measured(self):
        e = event(
            "e1", E.BLOCKER_OPENED, "blk-1",
            occurred_at=at(0), known_at=at(1.25),
        )
        assert e.knowledge_lag_seconds == pytest.approx(4500.0)


class TestKnowledgeVsRealityFiltering:
    def _events(self):
        return [
            # Occurred 09:00, discovered 10:15 — the integration failure.
            event("e-fail", E.BLOCKER_OPENED, "blk-integration",
                  occurred_at=at(0), known_at=at(1.25)),
            event("e-known", E.CLAIM_SUPPORTED, "claim-1",
                  occurred_at=at(-1), known_at=at(-1)),
        ]

    def test_forecast_at_0930_cannot_see_the_failure(self):
        known = events_known_at(self._events(), at(0.5))
        assert [e.id for e in known] == ["e-known"]

    def test_reality_query_does_see_the_failure(self):
        true_events = events_true_at(self._events(), at(0.5))
        assert "e-fail" in [e.id for e in true_events]

    def test_the_two_queries_differ(self):
        moment = at(0.5)
        known = {e.id for e in events_known_at(self._events(), moment)}
        actual = {e.id for e in events_true_at(self._events(), moment)}
        assert known != actual
        assert actual - known == {"e-fail"}


class TestStateReconstruction:
    def _events(self):
        return [
            event("e1", E.CLAIM_SUPPORTED, "claim-scope", known_at=at(0)),
            event("e2", E.DECISION_CONCLUSIVE, "dec-1", known_at=at(1)),
            event("e3", E.BLOCKER_OPENED, "blk-1", known_at=at(2)),
            event("e4", E.KPI_OBSERVED, "kpi-activation", known_at=at(2)),
            event("e5", E.KPI_OBSERVED, "kpi-activation", known_at=at(6)),
            event("e6", E.BLOCKER_RESOLVED, "blk-1", known_at=at(8)),
            event("e7", E.CLAIM_CONTESTED, "claim-scope", known_at=at(9)),
            event("e8", E.DECISION_SUPERSEDED, "dec-1", known_at=at(10)),
        ]

    def _state(self, as_of):
        return reconstruct_project_state(
            project_id=PROJECT, temporal_events=self._events(), as_of=as_of,
        )

    def test_future_events_excluded(self):
        state = self._state(at(3))
        assert state.active_claim_ids == ["claim-scope"]
        assert state.active_decision_ids == ["dec-1"]
        assert state.active_blocker_ids == ["blk-1"]
        assert state.contested_claim_ids == []  # contested only at hour 9

    def test_historical_kpi_observation_selected(self):
        early = self._state(at(3))
        later = self._state(at(7))
        # Latest observation known at each point.
        assert early.current_kpi_state_ids == ["e4"]
        assert later.current_kpi_state_ids == ["e5"]

    def test_blocker_resolution_applied(self):
        assert self._state(at(9)).active_blocker_ids == []

    def test_contested_claim_stays_contested_historically(self):
        state = self._state(at(9.5))
        assert "claim-scope" in state.contested_claim_ids

    def test_superseded_decision_not_authoritative(self):
        assert self._state(at(11)).active_decision_ids == []

    def test_stale_claim_dropped(self):
        events = [
            event("e1", E.CLAIM_SUPPORTED, "claim-x", known_at=at(0)),
            event("e2", E.CLAIM_STALE, "claim-x", known_at=at(1)),
        ]
        state = reconstruct_project_state(
            project_id=PROJECT, temporal_events=events, as_of=at(2),
        )
        assert state.active_claim_ids == []
        assert state.stale_claim_ids == ["claim-x"]

    def test_reopened_decision_not_authoritative(self):
        events = [
            event("e1", E.DECISION_CONCLUSIVE, "dec-1", known_at=at(0)),
            event("e2", E.DECISION_REOPENED, "dec-1", known_at=at(1)),
        ]
        state = reconstruct_project_state(
            project_id=PROJECT, temporal_events=events, as_of=at(2),
        )
        assert state.active_decision_ids == []

    def test_unknowns_preserved(self):
        events = [
            event("e1", E.EVIDENCE_OBSERVED, "pev-1",
                  entity_ids=["unk-mobile-scope"], known_at=at(0)),
        ]
        state = reconstruct_project_state(
            project_id=PROJECT, temporal_events=events, as_of=at(1),
        )
        assert state.unresolved_unknown_ids == ["unk-mobile-scope"]

    def test_other_projects_excluded(self):
        events = self._events() + [
            event("e-other", E.DECISION_CONCLUSIVE, "dec-other",
                  known_at=at(0), project_id="phoenix"),
        ]
        state = reconstruct_project_state(
            project_id=PROJECT, temporal_events=events, as_of=at(3),
        )
        assert "dec-other" not in state.active_decision_ids


class TestFingerprint:
    def test_deterministic(self):
        events = [
            event("e1", E.CLAIM_SUPPORTED, "claim-1", known_at=at(0)),
            event("e2", E.DECISION_CONCLUSIVE, "dec-1", known_at=at(1)),
        ]
        a = reconstruct_project_state(
            project_id=PROJECT, temporal_events=events, as_of=at(2),
        )
        b = reconstruct_project_state(
            project_id=PROJECT, temporal_events=list(reversed(events)),
            as_of=at(2),
        )
        assert a.state_fingerprint == b.state_fingerprint
        assert a.model_dump() == b.model_dump()

    def test_different_state_different_fingerprint(self):
        base = [event("e1", E.CLAIM_SUPPORTED, "claim-1", known_at=at(0))]
        extra = base + [
            event("e2", E.BLOCKER_OPENED, "blk-1", known_at=at(1)),
        ]
        a = reconstruct_project_state(
            project_id=PROJECT, temporal_events=base, as_of=at(2),
        )
        b = reconstruct_project_state(
            project_id=PROJECT, temporal_events=extra, as_of=at(2),
        )
        assert a.state_fingerprint != b.state_fingerprint

    def test_fingerprint_helper_is_order_insensitive(self):
        assert build_state_fingerprint(
            {"a": ["x", "y"], "b": "1"},
        ) == build_state_fingerprint({"b": "1", "a": ["y", "x"]})


class TestLeakageGuard:
    def test_leakage_detected(self):
        events = [
            event("e-late", E.DECISION_CONCLUSIVE, "dec-late",
                  known_at=at(10)),
        ]
        leaked = ProjectTemporalState(
            project_id=PROJECT, as_of=at(1),
            active_decision_ids=["dec-late"],  # must not be here
            evidence_cutoff_at=at(1), state_fingerprint="x",
        )
        with pytest.raises(ValueError, match="Future leakage"):
            assert_no_future_leakage(state=leaked, events=events)

    def test_clean_state_passes(self):
        events = [
            event("e1", E.DECISION_CONCLUSIVE, "dec-1", known_at=at(0)),
            event("e-late", E.DECISION_CONCLUSIVE, "dec-late",
                  known_at=at(10)),
        ]
        state = reconstruct_project_state(
            project_id=PROJECT, temporal_events=events, as_of=at(1),
        )
        assert_no_future_leakage(state=state, events=events)
        assert state.active_decision_ids == ["dec-1"]
