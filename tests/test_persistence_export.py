"""Tests for Commit 35 — inspectable export of durable history.

Commit 31 stored JSON rather than pickle specifically so this would be
possible later. The tests here check the three things that make an
export worth more than a `SELECT *`: it carries its own integrity
verdict, it is honest about gaps, and it cannot change anything.
"""

from __future__ import annotations

import json

import pytest

from lltpm.persistence import (
    DECISION_EVENTS,
    POLICY_EVENTS,
    RECOMMENDATION_EVENTS,
    JournalEventType,
    JournalRetentionClass,
    canonical_json_bytes,
    export_decision_history,
    export_evidence_provenance,
    export_journal,
    source_availability,
)
from lltpm.runtime import append_evidence_accepted
from tests._persistence_fixtures import (
    ORGANIZATION,
    PROJECT_A,
    Persistence,
    at,
    build_live_organization,
    engage_kill_switch,
    manifest,
    mark_source_deleted,
)
from tests._runtime_fixtures import envelope


@pytest.fixture(scope="module")
def live():
    return build_live_organization()


def _export(persistence, **overrides):
    defaults = dict(
        journal=persistence.journal,
        organization_id=ORGANIZATION,
        exported_at=at(200),
        manifest=manifest(),
    )
    defaults.update(overrides)
    return export_journal(**defaults)


class TestTheExportIsSelfDescribing:
    def test_it_carries_the_integrity_verdict(self, live):
        persistence, _, _ = live
        bundle = _export(persistence)
        assert bundle.integrity.hash_chain_valid is True
        assert "verified through sequence" in (
            bundle.integrity.verification_explanation
        )
        assert bundle.trustworthy is True

    def test_it_records_the_journal_head(self, live):
        persistence, _, _ = live
        bundle = _export(persistence)
        assert bundle.integrity.journal_head_sequence == (
            persistence.journal.latest_position().global_sequence
        )

    def test_it_carries_the_manifest_that_produced_the_artifacts(self, live):
        persistence, _, _ = live
        bundle = _export(persistence)
        assert bundle.manifest is not None
        assert bundle.manifest.runtime_derivation_version

    def test_a_corrupt_journal_exports_as_untrustworthy(self):
        persistence, _, _ = build_live_organization()
        persistence.journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"note": "edited by hand"},
        )
        bundle = _export(persistence)
        assert bundle.trustworthy is False
        assert bundle.integrity.corrupted_at_sequence == 2
        assert any("not repaired" in note for note in bundle.notes)

    def test_the_verdict_travels_with_the_data(self):
        """A copy that was never verified must not look like one that
        was."""
        persistence, _, _ = build_live_organization()
        persistence.journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"note": "tampered"},
        )
        restored = json.loads(
            canonical_json_bytes(_export(persistence).model_dump(mode="json"))
        )
        assert restored["integrity"]["hash_chain_valid"] is False


class TestWhatGetsExported:
    def test_every_record_is_included(self, live):
        persistence, _, _ = live
        bundle = _export(persistence)
        assert bundle.record_count == len(
            persistence.journal.read_from(global_sequence=1)
        )

    def test_decisions_are_broken_out(self, live):
        persistence, _, _ = live
        bundle = _export(persistence)
        assert bundle.decisions
        assert all(
            row["event_type"] in {e.value for e in DECISION_EVENTS}
            for row in bundle.decisions
        )

    def test_recommendations_are_broken_out(self, live):
        persistence, _, _ = live
        bundle = _export(persistence)
        assert bundle.recommendations
        assert all(
            row["event_type"] in {e.value for e in RECOMMENDATION_EVENTS}
            for row in bundle.recommendations
        )

    def test_policy_history_is_broken_out(self):
        persistence, _, _ = build_live_organization()
        engage_kill_switch(persistence=persistence, engaged=True, minutes=80)
        bundle = _export(persistence)
        assert bundle.policy_history
        assert any(
            row["event_type"] == (
                JournalEventType.AUTONOMY_KILL_SWITCH_CHANGED.value
            )
            for row in bundle.policy_history
        )
        assert all(
            row["event_type"] in {e.value for e in POLICY_EVENTS}
            for row in bundle.policy_history
        )

    def test_rows_carry_their_hash_links(self, live):
        persistence, _, _ = live
        first = _export(persistence).records[0]
        assert first["record_hash"]
        assert "previous_record_hash" in first

    def test_rows_are_ordered_by_sequence(self, live):
        persistence, _, _ = live
        sequences = [
            row["global_sequence"] for row in _export(persistence).records
        ]
        assert sequences == sorted(sequences)

    def test_payloads_can_be_omitted(self, live):
        persistence, _, _ = live
        bundle = _export(persistence, include_payloads=False)
        assert bundle.record_count > 0
        assert all(row["payload"] == {} for row in bundle.records)
        assert bundle.evidence_provenance, (
            "omitting payloads keeps the index and provenance"
        )

    def test_another_organization_is_not_exported(self):
        persistence, _, _ = build_live_organization()
        bundle = _export(persistence, organization_id="someone-else")
        assert all(
            row["organization_id"] in (None, "someone-else")
            for row in bundle.records
        )

    def test_the_export_is_json_serializable(self, live):
        persistence, _, _ = live
        restored = json.loads(
            canonical_json_bytes(_export(persistence).model_dump(mode="json"))
        )
        assert restored["record_count"] > 0

    def test_an_empty_journal_exports_cleanly(self):
        bundle = _export(Persistence())
        assert bundle.record_count == 0
        assert bundle.trustworthy is True


class TestEvidenceProvenance:
    def test_every_evidence_item_has_provenance(self, live):
        persistence, _, _ = live
        provenance = export_evidence_provenance(
            journal=persistence.journal, organization_id=ORGANIZATION,
        )
        assert provenance
        for item in provenance:
            assert item.evidence_id
            assert item.source_system
            assert item.observed_at
            assert item.received_at

    def test_a_content_hash_is_exported_even_when_content_is_not(self):
        persistence = Persistence()
        append_evidence_accepted(
            journal=persistence.journal,
            envelope=envelope(
                source_id="m1", event_type="chat_message",
                project_ids=[PROJECT_A], content="Something was said.",
            ),
            organization_id=ORGANIZATION,
            recorded_at=at(5),
            retention_class=JournalRetentionClass.MINIMAL,
        )
        item = export_evidence_provenance(
            journal=persistence.journal, organization_id=ORGANIZATION,
        )[0]
        assert item.content_present is False
        assert item.content_omitted_by_retention is True
        assert item.content_hash, (
            "the hash survives so the artifact can still be matched later"
        )

    def test_retention_omission_is_reported_as_intentional(self):
        persistence = Persistence()
        append_evidence_accepted(
            journal=persistence.journal,
            envelope=envelope(
                source_id="m1", event_type="chat_message",
                project_ids=[PROJECT_A], content="Something was said.",
            ),
            organization_id=ORGANIZATION,
            recorded_at=at(5),
            retention_class=JournalRetentionClass.MINIMAL,
        )
        bundle = _export(persistence)
        assert any(
            "intentional, not loss" in note for note in bundle.notes
        )

    def test_a_deleted_source_is_flagged_without_invalidating_it(self):
        persistence, _, _ = build_live_organization()
        # Evidence that carries content, so the hash is there to check.
        evidence_id = next(
            p.evidence_id for p in export_evidence_provenance(
                journal=persistence.journal, organization_id=ORGANIZATION,
            )
            if p.content_hash
        )
        mark_source_deleted(
            persistence=persistence, evidence_id=evidence_id,
        )
        item = next(
            p for p in export_evidence_provenance(
                journal=persistence.journal, organization_id=ORGANIZATION,
            )
            if p.evidence_id == evidence_id
        )
        assert item.source_available is False
        assert item.source_state == "deleted"
        assert item.content_hash, (
            "the evidence was real and was used; only the original "
            "artifact is now unreachable"
        )

    def test_an_unretrievable_source_is_noted_in_the_bundle(self):
        persistence, _, _ = build_live_organization()
        evidence_id = export_evidence_provenance(
            journal=persistence.journal, organization_id=ORGANIZATION,
        )[0].evidence_id
        mark_source_deleted(persistence=persistence, evidence_id=evidence_id)
        bundle = _export(persistence)
        assert any(
            "can no longer be retrieved" in note for note in bundle.notes
        )

    def test_the_latest_availability_statement_wins(self):
        from lltpm.persistence import SourceAvailability
        from lltpm.runtime import append_source_availability_changed

        persistence, _, _ = build_live_organization()
        append_source_availability_changed(
            journal=persistence.journal, organization_id=ORGANIZATION,
            evidence_id="ev-x", availability=SourceAvailability.DELETED,
            occurred_at=at(70), recorded_at=at(70),
        )
        append_source_availability_changed(
            journal=persistence.journal, organization_id=ORGANIZATION,
            evidence_id="ev-x", availability=SourceAvailability.AVAILABLE,
            occurred_at=at(90), recorded_at=at(90),
        )
        assert source_availability(
            persistence.journal.read_from(global_sequence=1)
        )["ev-x"] == "available", (
            "a source deleted and then restored reads as available"
        )

    def test_availability_with_no_statements_is_empty(self):
        persistence, _, _ = build_live_organization()
        assert source_availability(
            persistence.journal.read_from(global_sequence=1)
        ) == {}


class TestDecisionHistory:
    def test_a_decision_trail_shows_every_step(self, live):
        persistence, _, _ = live
        history = export_decision_history(
            journal=persistence.journal,
            decision_id="dec-contract-standard",
            organization_id=ORGANIZATION,
        )
        assert len(history) >= 2, (
            "the decision was recorded and then reopened; both are records"
        )
        event_types = [row["event_type"] for row in history]
        assert "organizational_decision_recorded" in event_types
        assert "organizational_decision_reopened" in event_types

    def test_the_trail_is_in_order(self, live):
        persistence, _, _ = live
        history = export_decision_history(
            journal=persistence.journal,
            decision_id="dec-contract-standard",
            organization_id=ORGANIZATION,
        )
        assert [row["occurred_at"] for row in history] == sorted(
            row["occurred_at"] for row in history
        )

    def test_the_correction_is_a_separate_record_not_an_edit(self, live):
        persistence, _, _ = live
        history = export_decision_history(
            journal=persistence.journal,
            decision_id="dec-contract-standard",
            organization_id=ORGANIZATION,
        )
        recorded = next(
            r for r in history
            if r["event_type"] == "organizational_decision_recorded"
        )
        assert recorded["payload"]["conclusion"] == "opt-b-scoped", (
            "the original conclusion survives the reopen; history says "
            "what happened, not what was later preferred"
        )

    def test_an_unknown_decision_has_no_trail(self, live):
        persistence, _, _ = live
        assert export_decision_history(
            journal=persistence.journal,
            decision_id="dec-never-existed",
            organization_id=ORGANIZATION,
        ) == []


class TestExportIsReadOnly:
    def test_exporting_does_not_change_the_journal(self, live):
        persistence, _, _ = live
        before = persistence.journal.latest_position().global_sequence
        _export(persistence)
        export_evidence_provenance(
            journal=persistence.journal, organization_id=ORGANIZATION,
        )
        export_decision_history(
            journal=persistence.journal, decision_id="dec-contract-standard",
        )
        assert persistence.journal.latest_position().global_sequence == before

    def test_the_export_module_never_writes(self):
        # Scoped to calls against a store, not to the word "append" —
        # building a result list is not a write.
        import ast
        import inspect

        import lltpm.persistence.export as export_module

        tree = ast.parse(inspect.getsource(export_module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            receiver = node.func.value
            receiver_name = (
                receiver.id if isinstance(receiver, ast.Name)
                else getattr(receiver, "attr", "")
            )
            if receiver_name not in (
                "journal", "state", "artifacts", "snapshots", "connection",
                "cursor", "state_store", "artifact_store",
            ):
                continue
            assert node.func.attr not in (
                "append", "put", "put_snapshot", "put_project_state",
                "put_portfolio_state", "put_revision", "promote_revision",
                "execute", "commit", "clear",
            ), (
                f"export must not call {receiver_name}.{node.func.attr} — "
                f"exporting history must not be able to change it"
            )

    def test_exporting_twice_gives_the_same_bytes(self, live):
        persistence, _, _ = live
        first = canonical_json_bytes(
            _export(persistence).model_dump(mode="json")
        )
        second = canonical_json_bytes(
            _export(persistence).model_dump(mode="json")
        )
        assert first == second


class TestNoCredentialsCanReachAnExport:
    def test_a_credential_cannot_be_journalled_so_cannot_be_exported(self):
        """The guarantee is upstream: nothing secret is in there to leak."""
        from lltpm.persistence import build_journal_record
        from lltpm.persistence import organization_stream

        with pytest.raises(ValueError, match="looks like a secret"):
            build_journal_record(
                event_type=JournalEventType.EVIDENCE_ACCEPTED,
                stream_id=organization_stream(ORGANIZATION),
                occurred_at=at(0), recorded_at=at(0),
                payload={"access_token": "leaked"},
                source_key="s",
            )

    def test_an_export_of_real_history_holds_no_secret_keys(self, live):
        persistence, _, _ = live
        text = canonical_json_bytes(
            _export(persistence).model_dump(mode="json")
        ).decode()
        for marker in (
            "access_token", "refresh_token", "signing_key", "api_key",
            "password",
        ):
            assert marker not in text
