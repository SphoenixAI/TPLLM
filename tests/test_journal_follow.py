"""Tailing the journal as records land.

A durable history that can only be read after the fact leaves "is
anything reaching the journal right now?" unanswerable. These tests pin
the follower's three obligations: it delivers every record exactly once
in sequence order, it never writes, and it terminates.

The clock and the sleep are injected, so the loop is tested as it
actually runs rather than only in a shape that happens to finish.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from lltpm.persistence import (
    InMemoryEventJournalStore,
    JournalEventType,
    SQLitePersistenceConfig,
    build_journal_record,
    follow_journal,
    organization_stream,
)
from lltpm.persistence.follow import DEFAULT_BATCH_SIZE
from lltpm.persistence.sqlite import initialize_sqlite_persistence

NOW = datetime(2026, 10, 19, 9, 0)


def _record(index: int):
    return build_journal_record(
        event_type=JournalEventType.EVIDENCE_ACCEPTED,
        stream_id=organization_stream("acme"),
        occurred_at=NOW,
        recorded_at=NOW,
        payload={"evidence": {"evidence_id": f"ev-{index}"}},
        organization_id="acme",
        project_ids=["atlas"],
        source_key=f"ev-{index}",
    )


class _Clock:
    """A monotonic clock that only advances when something sleeps."""

    def __init__(self) -> None:
        self.elapsed = 0.0

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds

    def now(self) -> float:
        return self.elapsed


class _CountingJournal:
    """Wraps a journal and records which methods a caller used."""

    def __init__(self, inner):
        self.inner = inner
        self.methods: list[str] = []

    def __getattr__(self, name: str):
        self.methods.append(name)
        return getattr(self.inner, name)


@pytest.fixture
def journal():
    store = InMemoryEventJournalStore()
    for index in range(3):
        store.append(record=_record(index))
    return store


class TestFollowing:
    def test_it_delivers_the_existing_tail_from_a_given_sequence(
        self, journal,
    ):
        seen = []
        clock = _Clock()
        last = follow_journal(
            journal=journal,
            on_record=lambda r: seen.append(r.position.global_sequence),
            after_sequence=1,
            max_records=2,
            sleep=clock.sleep,
            monotonic=clock.now,
        )
        assert seen == [2, 3]
        assert last == 3

    def test_it_delivers_records_that_arrive_while_it_waits(self, journal):
        """The point of the command: new facts appear without a restart."""
        clock = _Clock()
        arrivals = iter([3, 4])
        seen = []

        def sleep(seconds: float) -> None:
            clock.sleep(seconds)
            try:
                journal.append(record=_record(next(arrivals)))
            except StopIteration:
                pass

        follow_journal(
            journal=journal,
            on_record=lambda r: seen.append(r.position.global_sequence),
            after_sequence=3,
            max_records=2,
            sleep=sleep,
            monotonic=clock.now,
        )
        assert seen == [4, 5], "a record appended mid-wait must be delivered"

    def test_nothing_is_delivered_twice(self, journal):
        clock = _Clock()
        seen = []
        follow_journal(
            journal=journal,
            on_record=lambda r: seen.append(r.position.global_sequence),
            after_sequence=0,
            idle_timeout_seconds=1,
            poll_seconds=1,
            sleep=clock.sleep,
            monotonic=clock.now,
        )
        assert seen == sorted(set(seen)) == [1, 2, 3]

    def test_records_are_delivered_in_sequence_order(self, journal):
        clock = _Clock()
        seen = []
        follow_journal(
            journal=journal,
            on_record=lambda r: seen.append(r.position.global_sequence),
            after_sequence=0,
            max_records=3,
            sleep=clock.sleep,
            monotonic=clock.now,
        )
        assert seen == sorted(seen)

    def test_it_resumes_from_the_sequence_it_returned(self, journal):
        clock = _Clock()
        first = follow_journal(
            journal=journal, on_record=lambda r: None, after_sequence=0,
            max_records=2, sleep=clock.sleep, monotonic=clock.now,
        )
        seen = []
        follow_journal(
            journal=journal,
            on_record=lambda r: seen.append(r.position.global_sequence),
            after_sequence=first,
            max_records=1,
            sleep=clock.sleep,
            monotonic=clock.now,
        )
        assert seen == [3], "the returned cursor must be resumable as-is"


class TestTermination:
    def test_an_idle_timeout_ends_the_loop(self, journal):
        clock = _Clock()
        last = follow_journal(
            journal=journal, on_record=lambda r: None, after_sequence=3,
            idle_timeout_seconds=5, poll_seconds=1,
            sleep=clock.sleep, monotonic=clock.now,
        )
        assert last == 3
        assert clock.elapsed >= 5

    def test_the_idle_clock_restarts_when_a_record_arrives(self, journal):
        clock = _Clock()
        appended = {"count": 0}

        def sleep(seconds: float) -> None:
            clock.sleep(seconds)
            if appended["count"] < 4:
                appended["count"] += 1
                journal.append(record=_record(10 + appended["count"]))

        seen = []
        follow_journal(
            journal=journal,
            on_record=lambda r: seen.append(r.position.global_sequence),
            after_sequence=3,
            idle_timeout_seconds=2,
            poll_seconds=1,
            sleep=sleep,
            monotonic=clock.now,
        )
        assert len(seen) == 4, (
            "an active stream must not be cut off by the idle timeout"
        )

    def test_max_records_stops_mid_batch(self, journal):
        seen = []
        clock = _Clock()
        follow_journal(
            journal=journal,
            on_record=lambda r: seen.append(r.position.global_sequence),
            after_sequence=0, max_records=1,
            sleep=clock.sleep, monotonic=clock.now,
        )
        assert seen == [1]
        assert clock.elapsed == 0, "stopping must not require a sleep first"

    def test_a_backlog_larger_than_one_batch_is_drained_without_waiting(self):
        store = InMemoryEventJournalStore()
        for index in range(DEFAULT_BATCH_SIZE + 5):
            store.append(record=_record(index))
        clock = _Clock()
        seen = []
        follow_journal(
            journal=store,
            on_record=lambda r: seen.append(r.position.global_sequence),
            after_sequence=0,
            idle_timeout_seconds=0,
            poll_seconds=1,
            sleep=clock.sleep,
            monotonic=clock.now,
        )
        assert len(seen) == DEFAULT_BATCH_SIZE + 5
        assert clock.elapsed == 0, (
            "a full batch means more is probably waiting; sleeping between "
            "batches would make catching up take minutes"
        )

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_non_positive_poll_interval_is_refused(self, journal, bad):
        with pytest.raises(ValueError, match="poll_seconds"):
            follow_journal(
                journal=journal, on_record=lambda r: None,
                poll_seconds=bad,
            )

    def test_a_non_positive_batch_size_is_refused(self, journal):
        with pytest.raises(ValueError, match="batch_size"):
            follow_journal(
                journal=journal, on_record=lambda r: None, batch_size=0,
            )


class TestFollowingIsReadOnly:
    def test_it_only_ever_reads(self, journal):
        watched = _CountingJournal(journal)
        clock = _Clock()
        follow_journal(
            journal=watched, on_record=lambda r: None, after_sequence=0,
            max_records=3, sleep=clock.sleep, monotonic=clock.now,
        )
        assert set(watched.methods) == {"read_from"}, (
            f"following history must not be able to alter it; called "
            f"{sorted(set(watched.methods))}"
        )

    def test_an_empty_journal_is_followable(self):
        clock = _Clock()
        last = follow_journal(
            journal=InMemoryEventJournalStore(),
            on_record=lambda r: None,
            idle_timeout_seconds=1, poll_seconds=1,
            sleep=clock.sleep, monotonic=clock.now,
        )
        assert last == 0, (
            "an empty journal is exactly when someone wants to watch it"
        )


class TestAgainstARealDatabase:
    """A follower on its own connection must see another writer's commits.

    This is what makes `lltpm runtime journal --follow` useful while a
    separate `lltpm runtime ingest` is running.
    """

    @pytest.fixture
    def database(self, tmp_path):
        return str(tmp_path / "lltpm.db")

    def test_a_second_connection_sees_appends_as_they_commit(self, database):
        writer = initialize_sqlite_persistence(
            SQLitePersistenceConfig(path=database)
        )
        reader = initialize_sqlite_persistence(
            SQLitePersistenceConfig(path=database)
        )
        try:
            writer.journal.append(record=_record(0))

            clock = _Clock()
            appended = {"done": False}

            def sleep(seconds: float) -> None:
                clock.sleep(seconds)
                if not appended["done"]:
                    appended["done"] = True
                    writer.journal.append(record=_record(1))

            seen = []
            follow_journal(
                journal=reader.journal,
                on_record=lambda r: seen.append(r.position.global_sequence),
                after_sequence=0,
                max_records=2,
                sleep=sleep,
                monotonic=clock.now,
            )
            assert seen == [1, 2]
        finally:
            reader.close()
            writer.close()
