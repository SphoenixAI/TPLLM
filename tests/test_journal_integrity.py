"""Tests for Commit 31 — corruption is loud, never quietly repaired.

A durable history stored in a file someone can open with `sqlite3` will
eventually be opened with `sqlite3`. The realistic failures are an
accidental UPDATE, a truncated copy, a partially written backup — and
each silently changes what the organization believes its own history
was. Detecting them is the job; fixing them is emphatically not.
"""

from __future__ import annotations

import pytest

from lltpm.persistence import (
    InMemoryEventJournalStore,
    JournalIntegrityError,
    assert_journal_hash_chain,
    calculate_journal_record_hash,
    canonical_hash,
    content_fingerprint,
    stamp_record_hash,
    verify_journal,
    verify_journal_hash_chain,
)
from tests._persistence_fixtures import record


@pytest.fixture
def journal():
    store = InMemoryEventJournalStore()
    for i in range(5):
        store.append(record=record(source_key=f"k{i}", payload={"n": i}))
    return store


class TestCleanChain:
    def test_a_clean_chain_verifies(self, journal):
        assert verify_journal(journal=journal).valid is True

    def test_verification_names_the_head(self, journal):
        assert "sequence 5" in verify_journal(journal=journal).explanation

    def test_asserting_a_clean_chain_does_not_raise(self, journal):
        assert_journal_hash_chain(journal.read_from(global_sequence=1))

    def test_each_record_links_to_its_predecessor(self, journal):
        records = journal.read_from(global_sequence=1)
        for previous, current in zip(records, records[1:]):
            assert current.previous_record_hash == previous.record_hash

    def test_the_first_record_has_no_predecessor(self, journal):
        assert journal.read_from(global_sequence=1)[0].previous_record_hash is (
            None
        )


class TestMutationDetection:
    def test_editing_a_payload_is_detected(self, journal):
        journal._corrupt_payload_for_testing(
            global_sequence=3, payload={"n": 999},
        )
        report = verify_journal(journal=journal)
        assert report.valid is False
        assert report.corrupted_at_sequence == 3
        assert report.corrupted_record_id

    def test_the_position_of_corruption_is_reported(self, journal):
        journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"n": 999},
        )
        assert verify_journal(journal=journal).corrupted_at_sequence == 2

    def test_the_expected_and_actual_hashes_are_reported(self, journal):
        journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"n": 999},
        )
        report = verify_journal(journal=journal)
        assert report.expected_hash
        assert report.actual_hash
        assert report.expected_hash != report.actual_hash

    def test_corruption_is_not_silently_repaired(self, journal):
        journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"n": 999},
        )
        assert verify_journal(journal=journal).valid is False
        assert verify_journal(journal=journal).valid is False, (
            "verifying twice must not have 'fixed' anything"
        )

    def test_asserting_a_broken_chain_raises(self, journal):
        journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"n": 999},
        )
        with pytest.raises(JournalIntegrityError, match="modified"):
            assert_journal_hash_chain(journal.read_from(global_sequence=1))

    def test_editing_the_first_record_is_detected(self, journal):
        journal._corrupt_payload_for_testing(
            global_sequence=1, payload={"n": 999},
        )
        assert verify_journal(journal=journal).corrupted_at_sequence == 1

    def test_editing_the_last_record_is_detected(self, journal):
        journal._corrupt_payload_for_testing(
            global_sequence=5, payload={"n": 999},
        )
        assert verify_journal(journal=journal).corrupted_at_sequence == 5


class TestTruncation:
    def test_a_shorter_journal_than_expected_is_reported(self, journal):
        journal._truncate_for_testing(keep_through_sequence=3)
        report = verify_journal(journal=journal, expected_head_sequence=5)
        assert report.valid is False
        assert report.truncated_after_sequence == 3

    def test_the_missing_records_are_not_invented(self, journal):
        journal._truncate_for_testing(keep_through_sequence=3)
        report = verify_journal(journal=journal, expected_head_sequence=5)
        assert "not invented" in report.explanation
        assert "incomplete" in report.explanation

    def test_truncation_without_an_expectation_still_verifies(self, journal):
        journal._truncate_for_testing(keep_through_sequence=3)
        assert verify_journal(journal=journal).valid is True, (
            "a journal that legitimately ends at 3 is not corrupt; only a "
            "journal that should have reached 5 is"
        )

    def test_an_empty_journal_expected_to_have_records_is_reported(self):
        report = verify_journal(
            journal=InMemoryEventJournalStore(), expected_head_sequence=10,
        )
        assert report.valid is False
        assert "missing rather than absent" in report.explanation


class TestGapsAndReordering:
    def test_a_removed_middle_record_is_detected(self, journal):
        records = journal.read_from(global_sequence=1)
        report = verify_journal_hash_chain(records[:2] + records[3:])
        assert report.valid is False
        assert report.missing_sequences == [3]

    def test_a_relinked_record_is_detected(self, journal):
        records = journal.read_from(global_sequence=1)
        relinked = records[2].model_copy(update={
            "previous_record_hash": "forged",
        })
        report = verify_journal_hash_chain(
            records[:2] + [relinked] + records[3:],
        )
        assert report.valid is False
        assert report.broken_link_at_sequence == 3

    def test_reordering_does_not_fool_verification(self, journal):
        records = journal.read_from(global_sequence=1)
        shuffled = [records[2], records[0], records[1], records[3], records[4]]
        # Verification sorts by sequence, so a shuffled read still checks
        # the real chain rather than the order it arrived in.
        assert verify_journal_hash_chain(shuffled).valid is True

    def test_an_empty_list_verifies(self):
        assert verify_journal_hash_chain([]).valid is True


class TestHashHelpers:
    def test_canonical_hash_is_deterministic(self):
        assert canonical_hash({"a": 1, "b": 2}) == canonical_hash(
            {"b": 2, "a": 1}
        )

    def test_content_fingerprint_is_stable(self):
        assert content_fingerprint("hello") == content_fingerprint("hello")

    def test_content_fingerprint_of_none_is_none(self):
        assert content_fingerprint(None) is None

    def test_different_content_fingerprints_differently(self):
        assert content_fingerprint("a") != content_fingerprint("b")

    def test_stamping_produces_a_verifiable_record(self):
        stamped = stamp_record_hash(record(), previous_record_hash=None)
        assert calculate_journal_record_hash(
            stamped, previous_record_hash=None,
        ) == stamped.record_hash


class TestRealFileCorruption:
    def test_a_hand_edited_database_fails_verification(self, tmp_path):
        import sqlite3

        from lltpm.persistence import SQLitePersistenceConfig
        from lltpm.persistence.sqlite import initialize_sqlite_persistence

        config = SQLitePersistenceConfig(path=str(tmp_path / "lltpm.db"))
        db = initialize_sqlite_persistence(config)
        for i in range(4):
            db.journal.append(record=record(source_key=f"k{i}"))
        db.close()

        raw = sqlite3.connect(config.path)
        raw.execute(
            "UPDATE journal_records SET payload = ? WHERE global_sequence = 3",
            ('{"note":"someone edited this"}',),
        )
        raw.commit()
        raw.close()

        reopened = initialize_sqlite_persistence(config)
        report = verify_journal(journal=reopened.journal)
        assert report.valid is False
        assert report.corrupted_at_sequence == 3
        assert "modified after it was committed" in report.explanation
        reopened.close()
