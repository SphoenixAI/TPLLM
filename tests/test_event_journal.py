"""Tests for Commit 31 — append-only journal semantics.

Append, read, deduplicate, detect a race, verify. Nothing else. The
guarantees are what matter, and they are identical for the in-memory
store and SQLite.
"""

from __future__ import annotations

import threading

import pytest

from lltpm.persistence import (
    InMemoryEventJournalStore,
    JournalConcurrencyError,
    JournalEventType,
    JournalIdempotencyError,
    inspect_journal_range,
    organization_stream,
    project_stream,
    records_by_event_type,
    verify_journal,
    verify_journal_hash_chain,
)
from tests._persistence_fixtures import ORGANIZATION, at, record


@pytest.fixture
def journal():
    return InMemoryEventJournalStore()


class TestAppend:
    def test_the_first_record_starts_at_sequence_one(self, journal):
        committed = journal.append(record=record(source_key="a"))
        assert committed.position.global_sequence == 1
        assert committed.position.stream_sequence == 1

    def test_global_sequence_increments(self, journal):
        journal.append(record=record(source_key="a"))
        second = journal.append(record=record(source_key="b"))
        assert second.position.global_sequence == 2

    def test_stream_sequence_increments_per_stream(self, journal):
        journal.append(record=record(
            source_key="a", stream_id=project_stream("project-a"),
        ))
        second = journal.append(record=record(
            source_key="b", stream_id=project_stream("project-a"),
        ))
        assert second.position.stream_sequence == 2

    def test_streams_have_independent_sequences(self, journal):
        journal.append(record=record(
            source_key="a", stream_id=project_stream("project-a"),
        ))
        other = journal.append(record=record(
            source_key="b", stream_id=project_stream("project-b"),
        ))
        assert other.position.global_sequence == 2
        assert other.position.stream_sequence == 1

    def test_the_hash_chain_links_forward(self, journal):
        first = journal.append(record=record(source_key="a"))
        second = journal.append(record=record(source_key="b"))
        assert first.previous_record_hash is None
        assert second.previous_record_hash == first.record_hash

    def test_a_committed_record_carries_its_hash(self, journal):
        assert journal.append(record=record(source_key="a")).record_hash


class TestIdempotency:
    def test_the_same_webhook_four_times_is_one_record(self, journal):
        for _ in range(4):
            journal.append(record=record(source_key="slack:C1:123"))
        assert len(journal) == 1

    def test_a_duplicate_returns_the_original(self, journal):
        first = journal.append(record=record(source_key="a"))
        again = journal.append(record=record(source_key="a"))
        assert again.id == first.id
        assert again.position.global_sequence == first.position.global_sequence

    def test_a_reused_key_with_different_content_is_refused(self, journal):
        journal.append(record=record(source_key="a", payload={"n": 1}))
        with pytest.raises(JournalIdempotencyError, match="different content"):
            journal.append(record=record(source_key="a", payload={"n": 2}))

    def test_a_record_is_findable_by_its_key(self, journal):
        committed = journal.append(record=record(source_key="a"))
        assert journal.find_by_idempotency_key(
            committed.idempotency_key,
        ).id == committed.id

    def test_an_unknown_key_returns_none(self, journal):
        assert journal.find_by_idempotency_key("nope") is None

    def test_concurrent_duplicates_produce_one_record(self, journal):
        errors: list[Exception] = []

        def append():
            try:
                journal.append(record=record(source_key="shared"))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=append) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert len(journal) == 1


class TestOptimisticConcurrency:
    def test_a_matching_expected_sequence_succeeds(self, journal):
        journal.append(
            record=record(source_key="a"), expected_stream_sequence=0,
        )
        journal.append(
            record=record(source_key="b"), expected_stream_sequence=1,
        )
        assert len(journal) == 2

    def test_a_stale_expected_sequence_is_refused(self, journal):
        journal.append(
            record=record(source_key="a"), expected_stream_sequence=0,
        )
        with pytest.raises(JournalConcurrencyError, match="committed first"):
            journal.append(
                record=record(source_key="b"), expected_stream_sequence=0,
            )

    def test_the_loser_can_reread_and_retry(self, journal):
        journal.append(
            record=record(source_key="a"), expected_stream_sequence=0,
        )
        with pytest.raises(JournalConcurrencyError):
            journal.append(
                record=record(source_key="b"), expected_stream_sequence=0,
            )
        head = journal.stream_head(organization_stream(ORGANIZATION))
        retried = journal.append(
            record=record(source_key="b"), expected_stream_sequence=head,
        )
        assert retried.position.stream_sequence == 2

    def test_the_refused_append_wrote_nothing(self, journal):
        journal.append(
            record=record(source_key="a"), expected_stream_sequence=0,
        )
        with pytest.raises(JournalConcurrencyError):
            journal.append(
                record=record(source_key="b"), expected_stream_sequence=0,
            )
        assert len(journal) == 1

    def test_omitting_the_expectation_skips_the_check(self, journal):
        journal.append(record=record(source_key="a"))
        journal.append(record=record(source_key="b"))
        assert len(journal) == 2

    def test_only_one_of_two_racing_writers_wins(self, journal):
        journal.append(
            record=record(source_key="seed"), expected_stream_sequence=0,
        )
        results: list[str] = []

        def write(key: str):
            try:
                journal.append(
                    record=record(source_key=key),
                    expected_stream_sequence=1,
                )
                results.append("ok")
            except JournalConcurrencyError:
                results.append("conflict")

        threads = [
            threading.Thread(target=write, args=(f"w{i}",)) for i in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == ["conflict", "ok"]


class TestReading:
    def _seed(self, journal, count: int = 5):
        return [
            journal.append(record=record(source_key=f"k{i}"))
            for i in range(count)
        ]

    def test_read_from_is_ordered(self, journal):
        self._seed(journal)
        sequences = [
            r.position.global_sequence
            for r in journal.read_from(global_sequence=1)
        ]
        assert sequences == sorted(sequences) == [1, 2, 3, 4, 5]

    def test_read_from_starts_where_asked(self, journal):
        self._seed(journal)
        assert [
            r.position.global_sequence
            for r in journal.read_from(global_sequence=3)
        ] == [3, 4, 5]

    def test_read_from_honours_a_limit(self, journal):
        self._seed(journal)
        assert len(journal.read_from(global_sequence=1, limit=2)) == 2

    def test_read_range_is_inclusive(self, journal):
        self._seed(journal)
        assert [
            r.position.global_sequence
            for r in journal.read_range(start_sequence=2, end_sequence=4)
        ] == [2, 3, 4]

    def test_a_stream_read_is_isolated(self, journal):
        journal.append(record=record(
            source_key="a", stream_id=project_stream("project-a"),
        ))
        journal.append(record=record(
            source_key="b", stream_id=project_stream("project-b"),
        ))
        found = journal.read_stream(stream_id=project_stream("project-a"))
        assert len(found) == 1
        assert found[0].position.stream_id == "project:project-a"

    def test_a_stream_read_can_start_after_a_sequence(self, journal):
        stream = project_stream("project-a")
        journal.append(record=record(source_key="a", stream_id=stream))
        journal.append(record=record(source_key="b", stream_id=stream))
        assert len(
            journal.read_stream(stream_id=stream, after_sequence=1)
        ) == 1

    def test_get_by_id(self, journal):
        committed = journal.append(record=record(source_key="a"))
        assert journal.get(committed.id).id == committed.id

    def test_an_unknown_record_id_returns_none(self, journal):
        assert journal.get("no-such-record") is None

    def test_latest_position_tracks_the_head(self, journal):
        assert journal.latest_position() is None
        self._seed(journal, 3)
        assert journal.latest_position().global_sequence == 3

    def test_an_empty_journal_reads_as_empty(self, journal):
        assert journal.read_from(global_sequence=1) == []


class TestInspection:
    def test_a_range_is_readable(self, journal):
        for i in range(5):
            journal.append(record=record(source_key=f"k{i}"))
        found = inspect_journal_range(
            journal=journal, start_sequence=2, end_sequence=4,
        )
        assert [r.position.global_sequence for r in found] == [2, 3, 4]

    def test_a_range_can_filter_by_event_type(self, journal):
        journal.append(record=record(source_key="a"))
        journal.append(record=record(
            source_key="b", event_type=JournalEventType.OUTCOME_RECORDED,
            payload={"outcome_id": "o1"},
        ))
        found = inspect_journal_range(
            journal=journal, start_sequence=1, end_sequence=2,
            event_types=[JournalEventType.OUTCOME_RECORDED],
        )
        assert len(found) == 1

    def test_a_range_can_filter_by_organization(self, journal):
        journal.append(record=record(source_key="a"))
        journal.append(record=record(
            source_key="b", organization_id="other-org",
        ))
        assert len(inspect_journal_range(
            journal=journal, start_sequence=1, end_sequence=2,
            organization_id=ORGANIZATION,
        )) == 1

    def test_a_backwards_range_is_refused(self, journal):
        with pytest.raises(ValueError, match="precedes"):
            inspect_journal_range(
                journal=journal, start_sequence=5, end_sequence=1,
            )

    def test_inspection_does_not_mutate(self, journal):
        journal.append(record=record(source_key="a"))
        before = len(journal)
        inspect_journal_range(
            journal=journal, start_sequence=1, end_sequence=1,
        )
        assert len(journal) == before

    def test_records_can_be_filtered_by_type(self, journal):
        journal.append(record=record(source_key="a"))
        journal.append(record=record(
            source_key="b", event_type=JournalEventType.OUTCOME_RECORDED,
            payload={"outcome_id": "o1"},
        ))
        assert len(records_by_event_type(
            journal.read_from(global_sequence=1),
            JournalEventType.OUTCOME_RECORDED,
        )) == 1


class TestChainVerification:
    def test_a_clean_journal_verifies(self, journal):
        for i in range(5):
            journal.append(record=record(source_key=f"k{i}"))
        report = verify_journal(journal=journal)
        assert report.valid is True
        assert "verified through sequence 5" in report.explanation

    def test_an_empty_journal_verifies(self, journal):
        assert verify_journal(journal=journal).valid is True

    def test_a_mutated_payload_breaks_verification(self, journal):
        for i in range(3):
            journal.append(record=record(source_key=f"k{i}"))
        journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"note": "tampered"},
        )
        report = verify_journal(journal=journal)
        assert report.valid is False
        assert report.corrupted_at_sequence == 2
        assert "modified after it was committed" in report.explanation

    def test_corruption_is_reported_not_repaired(self, journal):
        journal.append(record=record(source_key="a"))
        journal._corrupt_payload_for_testing(
            global_sequence=1, payload={"note": "tampered"},
        )
        first = verify_journal(journal=journal)
        second = verify_journal(journal=journal)
        assert first.valid is False and second.valid is False

    def test_truncation_is_reported_as_incomplete_history(self, journal):
        for i in range(5):
            journal.append(record=record(source_key=f"k{i}"))
        journal._truncate_for_testing(keep_through_sequence=3)
        report = verify_journal(journal=journal, expected_head_sequence=5)
        assert report.valid is False
        assert report.truncated_after_sequence == 3
        assert "not invented" in report.explanation

    def test_an_empty_journal_that_should_not_be_is_reported(self, journal):
        report = verify_journal(journal=journal, expected_head_sequence=10)
        assert report.valid is False
        assert "missing rather than absent" in report.explanation

    def test_a_gap_in_the_middle_is_detected(self, journal):
        records = [
            journal.append(record=record(source_key=f"k{i}"))
            for i in range(4)
        ]
        report = verify_journal_hash_chain(
            [records[0], records[1], records[3]],
        )
        assert report.valid is False
        assert report.missing_sequences == [3]

    def test_a_broken_link_is_located(self, journal):
        records = [
            journal.append(record=record(source_key=f"k{i}"))
            for i in range(3)
        ]
        relinked = records[1].model_copy(update={
            "previous_record_hash": "not-the-real-hash",
        })
        report = verify_journal_hash_chain(
            [records[0], relinked, records[2]],
        )
        assert report.valid is False
        assert report.broken_link_at_sequence == 2


class TestRedeliveryIsNotAConflict:
    """A Commit 32 fix to Commit 31 behaviour.

    The same webhook arriving twice is the normal case idempotency exists
    for, but the duplicate-content check compared whole payloads —
    including ``received_at``, which differs by definition between two
    deliveries. Redelivery therefore raised instead of deduping.
    """

    def _evidence_payload(self, received_at: str) -> dict:
        return {
            "evidence": {
                "evidence_id": "env-1",
                "source_system": "slack",
                "source_type": "chat",
                "source_id": "C1-1",
                "event_type": "chat_message",
                "observed_at": "2026-11-16T09:00:00",
                "received_at": received_at,
                "project_ids": ["project-a"],
                "normalized_content": "Platform approval is pending.",
            },
        }

    def test_a_later_delivery_of_the_same_fact_dedupes(self, journal):
        first = journal.append(record=record(
            source_key="slack:C1:1",
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            payload=self._evidence_payload("2026-11-16T09:00:05"),
        ))
        again = journal.append(record=record(
            source_key="slack:C1:1",
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            payload=self._evidence_payload("2026-11-16T11:42:13"),
        ))
        assert again.id == first.id
        assert len(journal) == 1

    def test_the_first_delivery_time_is_what_is_kept(self, journal):
        journal.append(record=record(
            source_key="slack:C1:1",
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            payload=self._evidence_payload("2026-11-16T09:00:05"),
        ))
        journal.append(record=record(
            source_key="slack:C1:1",
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            payload=self._evidence_payload("2026-11-16T11:42:13"),
        ))
        stored = journal.read_from(global_sequence=1)[0]
        assert stored.payload["evidence"]["received_at"] == (
            "2026-11-16T09:00:05"
        ), "we did learn it then; the later delivery told us nothing new"

    def test_a_genuinely_different_fact_is_still_refused(self, journal):
        journal.append(record=record(
            source_key="slack:C1:1",
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            payload=self._evidence_payload("2026-11-16T09:00:05"),
        ))
        conflicting = self._evidence_payload("2026-11-16T09:00:05")
        conflicting["evidence"]["normalized_content"] = "Something else."
        with pytest.raises(JournalIdempotencyError, match="one identity"):
            journal.append(record=record(
                source_key="slack:C1:1",
                event_type=JournalEventType.EVIDENCE_ACCEPTED,
                payload=conflicting,
            ))

    def test_a_reused_key_across_event_types_is_still_refused(self, journal):
        journal.append(record=record(
            source_key="k",
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            payload=self._evidence_payload("2026-11-16T09:00:05"),
        ))
        # Same derived key, different kind of fact.
        from lltpm.persistence import build_journal_idempotency_key

        key = build_journal_idempotency_key(
            event_type=JournalEventType.EVIDENCE_ACCEPTED, source_key="k",
        )
        with pytest.raises(JournalIdempotencyError):
            journal.append(record=record(
                source_key="k",
                event_type=JournalEventType.EVIDENCE_ACCEPTED,
                payload={"evidence": {"evidence_id": "different"}},
                idempotency_key=key,
            ))

    def test_only_delivery_metadata_is_ignored(self):
        from lltpm.persistence import comparable_payload

        stripped = comparable_payload(
            self._evidence_payload("2026-11-16T09:00:05")
        )
        assert "received_at" not in stripped["evidence"]
        assert stripped["evidence"]["observed_at"] == (
            "2026-11-16T09:00:00"
        ), "reality time is content, not delivery metadata"
        assert stripped["evidence"]["normalized_content"]

    def test_stripping_does_not_mutate_the_original(self):
        from lltpm.persistence import comparable_payload

        payload = self._evidence_payload("2026-11-16T09:00:05")
        comparable_payload(payload)
        assert payload["evidence"]["received_at"] == "2026-11-16T09:00:05"
