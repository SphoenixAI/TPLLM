"""The retained anchor.

Reading Haber and Stornetta's 1990 timestamping paper against this
codebase turned up a gap between what the journal does and what the
documentation implied. Hash chaining makes an in-place edit detectable:
the link to the predecessor breaks, or a record stops matching its own
hash. It does not make a *wholesale rewrite* detectable, because
whoever rewrites the file can recompute every link after the one they
changed. Their own answer was to publish the head hash where it could
not be retracted.

So the central test here is the uncomfortable one: a forged history that
passes chain verification and is caught only by an anchor. Everything
else follows from it.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tpllm.persistence import (
    InMemoryEventJournalStore,
    JournalAnchor,
    JournalEventType,
    build_journal_anchor,
    build_journal_record,
    organization_stream,
    verify_journal,
)
from tpllm.persistence.integrity import (
    calculate_journal_record_hash,
    verify_journal_hash_chain,
)

NOW = datetime(2026, 10, 21, 5, 0)


def _record(index: int):
    return build_journal_record(
        event_type=JournalEventType.EVIDENCE_ACCEPTED,
        stream_id=organization_stream("acme"),
        occurred_at=NOW,
        recorded_at=NOW,
        payload={"evidence": {"evidence_id": f"ev-{index}"}},
        organization_id="acme",
        source_key=f"ev-{index}",
    )


@pytest.fixture
def journal():
    store = InMemoryEventJournalStore()
    for index in range(4):
        store.append(record=_record(index))
    return store


@pytest.fixture
def anchor(journal):
    built = build_journal_anchor(
        journal=journal, created_at=NOW, organization_id="acme",
    )
    assert built is not None
    return built


def _rewrite_history(journal, *, at_sequence: int, payload: dict) -> None:
    """Forge a record and recompute every link after it.

    This is what an actor with write access to the file can do, and it
    is the one thing a self-contained chain cannot notice.
    """
    records = list(journal._records)
    index = at_sequence - 1
    records[index] = records[index].model_copy(update={"payload": payload})
    previous = (
        records[index - 1].record_hash if index > 0
        else records[index].previous_record_hash
    )
    for position in range(index, len(records)):
        record = records[position].model_copy(
            update={"previous_record_hash": previous}
        )
        record = record.model_copy(update={
            "record_hash": calculate_journal_record_hash(
                record, previous_record_hash=previous,
            ),
        })
        records[position] = record
        previous = record.record_hash
    journal._records = records
    journal._by_id = {r.id: r for r in records}
    journal._by_idempotency_key = {r.idempotency_key: r for r in records}


# ── The gap the research surfaced ────────────────────────────────────


class TestWhatTheChainAloneCannotDetect:
    def test_a_rewritten_history_passes_chain_verification(self, journal):
        """Uncomfortable and true. Stated as a test so the limit is a
        known property rather than an assumption someone might make."""
        _rewrite_history(
            journal, at_sequence=2,
            payload={"evidence": {"evidence_id": "FORGED"}},
        )
        report = verify_journal(journal=journal)
        assert report.valid, (
            "every link in a rewritten chain recomputes correctly; that is "
            "why an anchor exists"
        )

    def test_verification_without_an_anchor_says_what_it_checked(
        self, journal,
    ):
        report = verify_journal(journal=journal)
        assert report.valid
        assert "no anchor" in report.explanation
        assert report.anchor_matched is None

    def test_an_anchor_catches_the_rewrite(self, journal, anchor):
        _rewrite_history(
            journal, at_sequence=2,
            payload={"evidence": {"evidence_id": "FORGED"}},
        )
        report = verify_journal(journal=journal, anchor=anchor)
        assert not report.valid
        assert report.anchor_matched is False
        assert report.expected_hash == anchor.record_hash
        assert report.actual_hash != anchor.record_hash

    def test_the_explanation_distinguishes_a_rewrite_from_an_edit(
        self, journal, anchor,
    ):
        _rewrite_history(
            journal, at_sequence=1,
            payload={"evidence": {"evidence_id": "FORGED"}},
        )
        report = verify_journal(journal=journal, anchor=anchor)
        assert "internally consistent" in report.explanation
        assert "rewritten" in report.explanation


class TestWhatTheChainDoesDetect:
    def test_an_in_place_edit_breaks_the_chain_without_an_anchor(
        self, journal,
    ):
        """The realistic failure — a bad restore, a manual UPDATE — is
        still caught with nothing retained."""
        journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"evidence": {"evidence_id": "X"}},
        )
        assert not verify_journal(journal=journal).valid

    def test_truncation_is_caught_by_the_anchor_sequence(
        self, journal, anchor,
    ):
        journal._truncate_for_testing(keep_through_sequence=2)
        report = verify_journal(journal=journal, anchor=anchor)
        assert not report.valid
        assert report.truncated_after_sequence == 2

    def test_a_matching_anchor_verifies(self, journal, anchor):
        report = verify_journal(journal=journal, anchor=anchor)
        assert report.valid
        assert report.anchor_matched is True
        assert "matches the retained anchor" in report.explanation

    def test_appending_after_an_anchor_moves_the_head(
        self, journal, anchor,
    ):
        """An anchor is a statement about a point in history, not a
        freeze. Growth past it is ordinary and must be distinguishable
        from a rewrite."""
        journal.append(record=_record(99))
        report = verify_journal(journal=journal, anchor=anchor)
        assert not report.valid
        assert report.anchor_matched is False
        later = build_journal_anchor(journal=journal, created_at=NOW)
        assert verify_journal(journal=journal, anchor=later).valid


# ── The anchor itself ────────────────────────────────────────────────


class TestBuildingAnAnchor:
    def test_it_carries_the_head_sequence_and_hash(self, journal, anchor):
        assert anchor.global_sequence == 4
        assert anchor.record_hash == journal._records[-1].record_hash

    def test_an_empty_journal_cannot_be_anchored(self):
        assert build_journal_anchor(
            journal=InMemoryEventJournalStore(), created_at=NOW,
        ) is None, (
            "an anchor at sequence zero would be a statement about history "
            "that does not exist"
        )

    def test_it_is_frozen(self, anchor):
        with pytest.raises(Exception):
            anchor.record_hash = "something-else"

    def test_an_anchor_needs_a_hash(self):
        with pytest.raises(ValueError, match="anchors nothing"):
            JournalAnchor(
                global_sequence=1, record_hash="", created_at=NOW,
            )

    def test_an_anchor_needs_a_real_sequence(self):
        with pytest.raises(ValueError, match="at least one record"):
            JournalAnchor(
                global_sequence=0, record_hash="abc", created_at=NOW,
            )

    def test_it_round_trips_as_json(self, anchor):
        restored = JournalAnchor.model_validate_json(
            anchor.model_dump_json()
        )
        assert restored == anchor

    def test_it_records_the_version_that_wrote_it(self, journal):
        built = build_journal_anchor(
            journal=journal, created_at=NOW, tpllm_version="9.9.9",
        )
        assert built.tpllm_version == "9.9.9"


class TestTheReportCarriesTheHead:
    def test_a_valid_report_exposes_the_head_hash(self, journal):
        report = verify_journal(journal=journal)
        assert report.head_record_hash == journal._records[-1].record_hash
        assert report.head_sequence == 4

    def test_an_empty_journal_has_no_head(self):
        report = verify_journal(journal=InMemoryEventJournalStore())
        assert report.valid
        assert report.head_record_hash is None

    def test_an_expected_hash_against_an_empty_journal_fails(self):
        report = verify_journal_hash_chain([], expected_head_hash="abc")
        assert not report.valid
        assert report.anchor_matched is False


class TestTheExportCarriesItsAnchor:
    def test_an_export_states_the_head_hash(self, journal):
        from tpllm.persistence import build_manifest, export_journal

        bundle = export_journal(
            journal=journal, organization_id="acme", exported_at=NOW,
            manifest=build_manifest(policy_fingerprint="t"),
        )
        assert bundle.integrity.journal_head_hash == (
            journal._records[-1].record_hash
        ), (
            "an export carrying only 'the chain verified' is "
            "re-verifiable against itself and nothing else"
        )
