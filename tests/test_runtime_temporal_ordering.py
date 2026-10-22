"""Arrival order is not temporal authority.

The canonical case, straight from the field:

    09:58  a meeting ends with the approval still pending
    10:03  Slack records the approval
    10:05  Jira shows implementation starting
    10:08  the 09:58 meeting webhook finally arrives

Arrival order is approval, implementation, pending. Temporal order is
pending, approval, implementation. A system that trusted arrival would
helpfully move an approved decision back to pending — and the late
transcript, which genuinely explains *why* the approval happened, would
be the thing that broke it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.runtime import (
    assert_no_future_events,
    classify_historical_effect,
    deduplicate_events,
    detect_contested_subjects,
    envelope_to_runtime_event,
    is_historical_insertion,
    latest_occurred_at,
    order_runtime_events,
    partition_planned_events,
    subject_latest_map,
    superseding_subject_keys,
)
from tests._runtime_fixtures import (
    PROJECT_A,
    at,
    envelope,
    event,
    kpi_envelope,
)

DECISION_SUBJECT = "decision:contract-standard"


def _approval():
    return event(
        envelope_id="env-approval", event_class="decision_conclusive",
        occurred_at=at(3), received_at=at(3),
        subject_keys=[DECISION_SUBJECT], asserted_state="approved",
    )


def _implementation():
    return event(
        envelope_id="env-impl", event_class="blocker_resolved",
        occurred_at=at(5), received_at=at(5),
        subject_keys=["blocker:integration"], asserted_state="resolved",
    )


def _late_transcript():
    """Recorded at 09:58, delivered at 10:08."""
    return event(
        envelope_id="env-transcript", event_class="meeting_transcript",
        occurred_at=at(-2), received_at=at(8),
        subject_keys=[DECISION_SUBJECT], asserted_state="pending",
    )


class TestOrdering:
    def test_events_order_by_when_they_happened(self):
        arrival = [_approval(), _implementation(), _late_transcript()]
        ordered = order_runtime_events(arrival)
        assert [e.envelope_id for e in ordered] == [
            "env-transcript", "env-approval", "env-impl",
        ]

    def test_arrival_order_does_not_change_the_result(self):
        events = [_approval(), _implementation(), _late_transcript()]
        assert order_runtime_events(events) == order_runtime_events(
            list(reversed(events))
        )

    def test_knowledge_time_only_breaks_ties(self):
        early_arrival = event(
            envelope_id="env-a", event_class="kpi_observation",
            occurred_at=at(1), received_at=at(1),
        )
        late_arrival = event(
            envelope_id="env-b", event_class="kpi_observation",
            occurred_at=at(1), received_at=at(9),
        )
        ordered = order_runtime_events([late_arrival, early_arrival])
        assert [e.envelope_id for e in ordered] == ["env-a", "env-b"]

    def test_ordering_is_total_and_stable(self):
        same = [
            event(
                envelope_id=f"env-{i}", event_class="kpi_observation",
                occurred_at=at(1), received_at=at(1),
            )
            for i in "ba"
        ]
        assert [e.envelope_id for e in order_runtime_events(same)] == [
            "env-a", "env-b",
        ]

    def test_the_latest_reality_time_is_reported(self):
        assert latest_occurred_at(
            [_late_transcript(), _approval(), _implementation()]
        ) == at(5)

    def test_no_events_means_no_watermark(self):
        assert latest_occurred_at([]) is None


class TestHistoricalInsertion:
    def test_an_old_event_is_recognized_as_historical(self):
        assert is_historical_insertion(
            _late_transcript(), latest_known_occurred_at=at(5),
        ) is True

    def test_a_new_event_is_not(self):
        assert is_historical_insertion(
            _approval(), latest_known_occurred_at=at(1),
        ) is False

    def test_the_first_event_ever_is_not_historical(self):
        assert is_historical_insertion(
            _approval(), latest_known_occurred_at=None,
        ) is False

    def test_a_superseded_subject_is_identified(self):
        assert superseding_subject_keys(
            _late_transcript(),
            subject_latest_occurred_at={DECISION_SUBJECT: at(3)},
        ) == [DECISION_SUBJECT]

    def test_an_unsuperseded_subject_is_not(self):
        assert superseding_subject_keys(
            _late_transcript(),
            subject_latest_occurred_at={DECISION_SUBJECT: at(-5)},
        ) == []


class TestLateEventCannotRegressCurrentState:
    """The canonical out-of-order case."""

    def _classify(self, event_to_classify, known):
        return classify_historical_effect(
            event_to_classify,
            latest_known_occurred_at=latest_occurred_at(known),
            subject_latest_occurred_at=subject_latest_map(known),
        )

    def test_the_late_transcript_is_lineage_only(self):
        known = [_approval(), _implementation()]
        assert self._classify(_late_transcript(), known) == "lineage_only", (
            "the approval is newer on the same decision, so the transcript "
            "enriches lineage and must not reopen the decision"
        )

    def test_a_current_event_is_classified_as_current(self):
        assert self._classify(_implementation(), [_approval()]) == "current"

    def test_an_old_event_on_an_untouched_subject_still_counts(self):
        # Nothing newer has been said about this blocker, so the old event
        # does change what is currently true.
        old_blocker = event(
            envelope_id="env-old-blocker", event_class="blocker_opened",
            occurred_at=at(1), received_at=at(9),
            subject_keys=["blocker:unrelated"],
        )
        assert self._classify(
            old_blocker, [_approval(), _implementation()],
        ) == "historical_current", (
            "an old event is not automatically an irrelevant event"
        )

    def test_an_old_event_with_no_subject_enriches_history_only(self):
        anonymous = event(
            envelope_id="env-anon", event_class="meeting_transcript",
            occurred_at=at(-10), received_at=at(9),
        )
        assert self._classify(
            anonymous, [_approval(), _implementation()],
        ) == "lineage_only"


class TestFutureLeakage:
    def test_an_event_from_the_future_is_refused(self):
        with pytest.raises(ValueError, match="History is not prediction"):
            assert_no_future_events(
                [
                    event(
                        envelope_id="env-future",
                        event_class="kpi_observation",
                        occurred_at=at(60), received_at=at(60),
                    )
                ],
                now=at(10),
            )

    def test_past_and_present_events_pass(self):
        assert_no_future_events(
            [_approval(), _implementation()], now=at(10),
        )


class TestPlansAreNotObservations:
    def test_a_scheduled_freeze_is_separated_out(self):
        observed, planned = partition_planned_events([
            _approval(),
            event(
                envelope_id="env-freeze", event_class="scheduled_freeze",
                occurred_at=at(1),
            ),
        ])
        assert [e.envelope_id for e in observed] == ["env-approval"]
        assert [e.envelope_id for e in planned] == ["env-freeze"]

    def test_a_commitment_is_a_plan(self):
        _, planned = partition_planned_events([
            event(
                envelope_id="env-commit", event_class="commitment_made",
                occurred_at=at(1),
            ),
        ])
        assert len(planned) == 1

    def test_ordinary_observations_are_all_observed(self):
        observed, planned = partition_planned_events(
            [_approval(), _implementation()]
        )
        assert len(observed) == 2
        assert planned == []


class TestContestedEvidence:
    def test_conflicting_simultaneous_evidence_stays_contested(self):
        team_a = event(
            envelope_id="env-team-a", event_class="decision_conclusive",
            occurred_at=at(3), subject_keys=[DECISION_SUBJECT],
            asserted_state="approved",
        )
        team_b = event(
            envelope_id="env-team-b", event_class="decision_proposed",
            occurred_at=at(4), subject_keys=[DECISION_SUBJECT],
            asserted_state="pending",
        )
        assert detect_contested_subjects([team_a, team_b]) == [
            DECISION_SUBJECT
        ]

    def test_processing_order_is_not_the_tie_breaker(self):
        team_a = event(
            envelope_id="env-team-a", event_class="decision_conclusive",
            occurred_at=at(3), subject_keys=[DECISION_SUBJECT],
            asserted_state="approved",
        )
        team_b = event(
            envelope_id="env-team-b", event_class="decision_proposed",
            occurred_at=at(4), subject_keys=[DECISION_SUBJECT],
            asserted_state="pending",
        )
        assert detect_contested_subjects([team_a, team_b]) == (
            detect_contested_subjects([team_b, team_a])
        )

    def test_agreeing_evidence_is_not_contested(self):
        first = event(
            envelope_id="env-1", event_class="decision_conclusive",
            occurred_at=at(3), subject_keys=[DECISION_SUBJECT],
            asserted_state="approved",
        )
        second = event(
            envelope_id="env-2", event_class="decision_conclusive",
            occurred_at=at(4), subject_keys=[DECISION_SUBJECT],
            asserted_state="approved",
        )
        assert detect_contested_subjects([first, second]) == []

    def test_a_later_correction_outside_the_window_is_not_a_conflict(self):
        first = event(
            envelope_id="env-1", event_class="decision_proposed",
            occurred_at=at(0), subject_keys=[DECISION_SUBJECT],
            asserted_state="pending",
        )
        second = event(
            envelope_id="env-2", event_class="decision_conclusive",
            occurred_at=at(120), subject_keys=[DECISION_SUBJECT],
            asserted_state="approved",
        )
        assert detect_contested_subjects([first, second]) == [], (
            "a decision that progressed over two hours is progress, not "
            "disagreement"
        )

    def test_a_wider_window_catches_more(self):
        first = event(
            envelope_id="env-1", event_class="decision_proposed",
            occurred_at=at(0), subject_keys=[DECISION_SUBJECT],
            asserted_state="pending",
        )
        second = event(
            envelope_id="env-2", event_class="decision_conclusive",
            occurred_at=at(120), subject_keys=[DECISION_SUBJECT],
            asserted_state="approved",
        )
        assert detect_contested_subjects(
            [first, second], window=timedelta(hours=3),
        ) == [DECISION_SUBJECT]

    def test_events_without_an_asserted_state_cannot_conflict(self):
        assert detect_contested_subjects([_implementation()]) == []


class TestDeduplication:
    def test_the_same_webhook_twice_is_one_event(self):
        first = envelope_to_runtime_event(kpi_envelope())
        second = envelope_to_runtime_event(kpi_envelope())
        fresh, duplicates = deduplicate_events([first, second])
        assert len(fresh) == 1
        assert len(duplicates) == 1

    def test_previously_processed_keys_are_remembered(self):
        runtime_event = envelope_to_runtime_event(kpi_envelope())
        fresh, duplicates = deduplicate_events(
            [runtime_event], known_keys={runtime_event.idempotency_key},
        )
        assert fresh == []
        assert len(duplicates) == 1

    def test_distinct_events_both_survive(self):
        first = envelope_to_runtime_event(kpi_envelope())
        second = envelope_to_runtime_event(
            kpi_envelope(source_id="kpi-activation-2")
        )
        fresh, duplicates = deduplicate_events([first, second])
        assert len(fresh) == 2
        assert duplicates == []


class TestEnvelopeNormalization:
    def test_metadata_becomes_scheduling_facts(self):
        runtime_event = envelope_to_runtime_event(
            envelope(
                source_id="src-1", event_type="capacity_changed",
                shared_resource_ids=["Platform Architecture"],
                kpi_ids=["kpi-1"], subject_keys=["capacity:platform"],
                asserted_state="capacity=1",
            )
        )
        assert runtime_event.shared_resource_ids == ["Platform Architecture"]
        assert runtime_event.kpi_ids == ["kpi-1"]
        assert runtime_event.subject_keys == ["capacity:platform"]
        assert runtime_event.asserted_state == "capacity=1"

    def test_the_envelope_id_is_the_default_causal_root(self):
        runtime_event = envelope_to_runtime_event(kpi_envelope())
        assert runtime_event.causal_event_id == runtime_event.envelope_id

    def test_an_explicit_causal_root_wins(self):
        runtime_event = envelope_to_runtime_event(
            envelope(
                source_id="src-1", event_type="capacity_changed",
                causal_event_id="causal-incident",
            )
        )
        assert runtime_event.causal_event_id == "causal-incident"

    def test_the_caller_can_override_the_causal_root(self):
        runtime_event = envelope_to_runtime_event(
            kpi_envelope(), causal_event_id="causal-override",
        )
        assert runtime_event.causal_event_id == "causal-override"

    def test_root_evidence_defaults_to_the_envelope(self):
        runtime_event = envelope_to_runtime_event(kpi_envelope())
        assert runtime_event.root_evidence_ids == [runtime_event.envelope_id]

    def test_project_ids_carry_over(self):
        runtime_event = envelope_to_runtime_event(kpi_envelope())
        assert runtime_event.project_ids == [PROJECT_A]

    def test_the_event_type_becomes_the_event_class(self):
        assert envelope_to_runtime_event(
            kpi_envelope(),
        ).event_class == "kpi_observation"
