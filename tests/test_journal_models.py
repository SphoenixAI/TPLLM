"""Journal vocabulary, canonical bytes, hashing.

Everything downstream rests on canonical serialization. If the same
record can serialize two ways, the hash chain proves nothing and
"verified history" is a decoration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

import pytest

from tpllm.persistence import (
    AUTHORITATIVE_EVENT_TYPES,
    EVENT_TYPE_CATEGORY,
    DurableEvidenceRecord,
    ExecutionContext,
    ExecutionMode,
    JournalEventType,
    JournalPosition,
    JournalRecord,
    JournalRecordCategory,
    JournalRetentionClass,
    ReplayClock,
    ReplayMode,
    ReplayRequest,
    SecretMaterialError,
    assert_no_secret_material,
    build_journal_idempotency_key,
    calculate_journal_record_hash,
    canonical_json_bytes,
    derive_simulation_seed,
    organization_stream,
    policy_stream,
    project_stream,
    stamp_record_hash,
)
from tests._persistence_fixtures import ORGANIZATION, T0, at, record


class TestEventTaxonomy:
    def test_every_spec_event_type_exists(self):
        for name in (
            "EVIDENCE_ACCEPTED", "IDENTITY_LINKED", "ENTITY_MERGE_APPLIED",
            "AGENT_RECOMMENDATION_PUBLISHED", "HUMAN_RECOMMENDATION_MODIFIED",
            "ORGANIZATIONAL_DECISION_REOPENED",
            "AUTONOMOUS_DECISION_FINALIZED", "AUTHORITY_POLICY_CHANGED",
            "AUTONOMY_KILL_SWITCH_CHANGED", "OUTCOME_RECORDED",
            "SOURCE_AVAILABILITY_CHANGED",
            "INTELLIGENCE_REVISION_PUBLISHED", "DERIVED_ARTIFACT_PUBLISHED",
        ):
            assert hasattr(JournalEventType, name)

    def test_every_event_type_has_a_category(self):
        assert set(EVENT_TYPE_CATEGORY) == set(JournalEventType)

    def test_audit_records_are_not_authoritative(self):
        assert JournalEventType.INTELLIGENCE_REVISION_PUBLISHED not in (
            AUTHORITATIVE_EVENT_TYPES
        )
        assert JournalEventType.DERIVED_ARTIFACT_PUBLISHED not in (
            AUTHORITATIVE_EVENT_TYPES
        )

    def test_organizational_transitions_are_authoritative(self):
        assert JournalEventType.EVIDENCE_ACCEPTED in AUTHORITATIVE_EVENT_TYPES
        assert JournalEventType.ORGANIZATIONAL_DECISION_RECORDED in (
            AUTHORITATIVE_EVENT_TYPES
        )
        assert JournalEventType.AUTHORITY_POLICY_CHANGED in (
            AUTHORITATIVE_EVENT_TYPES
        )

    def test_categories_match_the_spec(self):
        assert EVENT_TYPE_CATEGORY[
            JournalEventType.EVIDENCE_ACCEPTED
        ] == JournalRecordCategory.INGEST
        assert EVENT_TYPE_CATEGORY[
            JournalEventType.AUTHORITY_POLICY_CHANGED
        ] == JournalRecordCategory.POLICY
        assert EVENT_TYPE_CATEGORY[
            JournalEventType.OUTCOME_RECORDED
        ] == JournalRecordCategory.OUTCOME


class TestPosition:
    def test_sequences_start_at_one(self):
        with pytest.raises(ValueError, match="global_sequence starts at 1"):
            JournalPosition(
                global_sequence=0, stream_id="s", stream_sequence=1,
            )
        with pytest.raises(ValueError, match="stream_sequence starts at 1"):
            JournalPosition(
                global_sequence=1, stream_id="s", stream_sequence=0,
            )

    def test_sequences_cannot_be_negative(self):
        with pytest.raises(ValueError):
            JournalPosition(
                global_sequence=-1, stream_id="s", stream_sequence=1,
            )

    def test_a_record_must_belong_to_a_stream(self):
        with pytest.raises(ValueError, match="must belong to a stream"):
            JournalPosition(
                global_sequence=1, stream_id="", stream_sequence=1,
            )

    def test_stream_naming(self):
        assert organization_stream("acme") == "organization:acme"
        assert project_stream("project-a") == "project:project-a"
        assert policy_stream("acme") == "policy:acme"


class TestRecordValidation:
    def test_a_valid_record_constructs(self):
        built = record()
        assert built.event_type == JournalEventType.EVIDENCE_ACCEPTED
        assert built.organization_id == ORGANIZATION

    def test_an_idempotency_key_is_required(self):
        with pytest.raises(ValueError, match="idempotency key"):
            JournalRecord(
                id="r", position=JournalPosition(
                    global_sequence=1, stream_id="s", stream_sequence=1,
                ),
                category=JournalRecordCategory.INGEST,
                event_type=JournalEventType.EVIDENCE_ACCEPTED,
                occurred_at=T0, recorded_at=T0,
                idempotency_key="", payload_schema_version=1,
            )

    def test_payload_schema_version_is_required_and_positive(self):
        with pytest.raises(ValueError, match="payload_schema_version"):
            JournalRecord(
                id="r", position=JournalPosition(
                    global_sequence=1, stream_id="s", stream_sequence=1,
                ),
                category=JournalRecordCategory.INGEST,
                event_type=JournalEventType.EVIDENCE_ACCEPTED,
                occurred_at=T0, recorded_at=T0,
                idempotency_key="k", payload_schema_version=0,
            )

    def test_a_record_cannot_be_recorded_before_it_happened(self):
        with pytest.raises(ValueError, match="recorded before it happened"):
            record(occurred_at=at(10), recorded_at=at(5))

    def test_a_committed_record_is_immutable(self):
        built = record()
        with pytest.raises(Exception):
            built.id = "changed"

    def test_a_late_recorded_record_is_fine(self):
        # The 09:58 transcript recorded at 10:08 is ordinary, not invalid.
        built = record(occurred_at=at(-2), recorded_at=at(8))
        assert built.occurred_at < built.recorded_at


class TestSecretRejection:
    # Pydantic wraps a validator's ValueError, so model construction is
    # matched on message; the helper itself is asserted directly below.
    def test_a_token_key_is_refused(self):
        with pytest.raises(ValueError, match="looks like a secret"):
            record(payload={"access_token": "abc"})

    def test_a_nested_secret_is_refused(self):
        with pytest.raises(ValueError, match="refresh_token"):
            record(payload={"auth": {"refresh_token": "abc"}})

    def test_a_secret_inside_a_list_is_refused(self):
        with pytest.raises(ValueError, match="api_key"):
            record(payload={"items": [{"api_key": "abc"}]})

    def test_the_helper_raises_its_own_error_type(self):
        with pytest.raises(SecretMaterialError, match="looks like a secret"):
            assert_no_secret_material({"access_token": "abc"})

    def test_credential_shaped_values_are_refused(self):
        with pytest.raises(SecretMaterialError, match="credential material"):
            assert_no_secret_material({"note": "xoxb-1234567890"})

    def test_a_private_key_block_is_refused(self):
        with pytest.raises(SecretMaterialError):
            assert_no_secret_material(
                {"blob": "-----BEGIN RSA PRIVATE KEY-----"}
            )

    def test_ordinary_payloads_pass(self):
        assert_no_secret_material({
            "decision_id": "dec-1",
            "conclusion": "opt-b",
            "evidence": {"ids": ["ev-1", "ev-2"]},
        })

    def test_the_word_token_in_prose_is_still_refused_as_a_key(self):
        # Deliberately conservative: a key is a key.
        with pytest.raises(SecretMaterialError):
            assert_no_secret_material({"signing_key_note": "rotated"})


class TestIdempotencyKeys:
    def test_the_key_is_deterministic(self):
        first = build_journal_idempotency_key(
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            source_key="slack:C1:123",
        )
        second = build_journal_idempotency_key(
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            source_key="slack:C1:123",
        )
        assert first == second

    def test_different_events_from_one_source_differ(self):
        assert build_journal_idempotency_key(
            event_type=JournalEventType.EVIDENCE_ACCEPTED, source_key="s",
        ) != build_journal_idempotency_key(
            event_type=JournalEventType.OUTCOME_RECORDED, source_key="s",
        )

    def test_the_key_names_its_event_type(self):
        key = build_journal_idempotency_key(
            event_type=JournalEventType.EVIDENCE_ACCEPTED, source_key="s",
        )
        assert key.startswith("evidence_accepted:")


class TestCanonicalSerialization:
    def test_key_order_does_not_matter(self):
        assert canonical_json_bytes({"a": 1, "b": 2}) == (
            canonical_json_bytes({"b": 2, "a": 1})
        )

    def test_the_same_semantic_payload_yields_the_same_bytes(self):
        first = {"ids": ["a", "b"], "when": datetime(2026, 11, 16, 9)}
        second = {"when": datetime(2026, 11, 16, 9), "ids": ["a", "b"]}
        assert canonical_json_bytes(first) == canonical_json_bytes(second)

    def test_naive_and_aware_utc_datetimes_agree(self):
        naive = datetime(2026, 11, 16, 9)
        aware = datetime(2026, 11, 16, 9, tzinfo=timezone.utc)
        assert canonical_json_bytes(naive) == canonical_json_bytes(aware)

    def test_datetimes_render_stably(self):
        assert canonical_json_bytes(datetime(2026, 11, 16, 9)) == (
            b'"2026-11-16T09:00:00Z"'
        )

    def test_enums_render_by_value(self):
        class Colour(str, Enum):
            RED = "red"

        assert canonical_json_bytes(Colour.RED) == b'"red"'

    def test_sets_render_in_a_stable_order(self):
        assert canonical_json_bytes({"s": {"b", "a", "c"}}) == (
            canonical_json_bytes({"s": {"c", "a", "b"}})
        )

    def test_list_order_is_preserved(self):
        assert canonical_json_bytes(["a", "b"]) != canonical_json_bytes(
            ["b", "a"]
        )

    def test_pydantic_models_serialize_by_content(self):
        first = DurableEvidenceRecord(
            evidence_id="ev-1", source_system="slack", source_type="message",
            source_id="1", event_type="chat_message",
            observed_at=T0, received_at=T0,
        )
        second = first.model_copy(deep=True)
        assert canonical_json_bytes(first) == canonical_json_bytes(second)

    def test_no_python_repr_leaks_in(self):
        class Opaque:
            pass

        with pytest.raises(TypeError, match="JSON-compatible"):
            canonical_json_bytes({"x": Opaque()})

    def test_floats_are_stable(self):
        assert canonical_json_bytes(0.1 + 0.2) == canonical_json_bytes(
            0.30000000000000004
        )

    def test_output_is_bytes(self):
        assert isinstance(canonical_json_bytes({"a": 1}), bytes)


class TestRecordHashing:
    def test_the_hash_is_deterministic(self):
        built = record()
        assert calculate_journal_record_hash(built) == (
            calculate_journal_record_hash(built)
        )

    def test_the_hash_excludes_itself(self):
        built = record()
        stamped = built.model_copy(update={"record_hash": "anything"})
        assert calculate_journal_record_hash(
            built,
        ) == calculate_journal_record_hash(stamped)

    def test_the_hash_includes_the_previous_link(self):
        built = record()
        assert calculate_journal_record_hash(
            built, previous_record_hash="a",
        ) != calculate_journal_record_hash(built, previous_record_hash="b")

    def test_a_changed_payload_changes_the_hash(self):
        first = record(payload={"note": "one"})
        second = record(payload={"note": "two"})
        assert calculate_journal_record_hash(
            first,
        ) != calculate_journal_record_hash(second)

    def test_stamping_fills_in_both_links(self):
        stamped = stamp_record_hash(record(), previous_record_hash="prev")
        assert stamped.previous_record_hash == "prev"
        assert stamped.record_hash
        assert stamped.record_hash == calculate_journal_record_hash(
            stamped, previous_record_hash="prev",
        )


class TestDurableEvidence:
    def test_minimal_retention_reports_the_omission(self):
        evidence = DurableEvidenceRecord(
            evidence_id="ev-1", source_system="slack", source_type="message",
            source_id="1", event_type="chat_message",
            observed_at=T0, received_at=T0, normalized_content=None,
            retention_class=JournalRetentionClass.MINIMAL.value,
        )
        assert evidence.content_omitted_by_retention is True

    def test_normalized_retention_with_content_is_not_an_omission(self):
        evidence = DurableEvidenceRecord(
            evidence_id="ev-1", source_system="slack", source_type="message",
            source_id="1", event_type="chat_message",
            observed_at=T0, received_at=T0, normalized_content="hello",
        )
        assert evidence.content_omitted_by_retention is False

    def test_secrets_in_evidence_metadata_are_refused(self):
        with pytest.raises(ValueError, match="signing_key"):
            DurableEvidenceRecord(
                evidence_id="ev-1", source_system="slack",
                source_type="message", source_id="1",
                event_type="chat_message", observed_at=T0, received_at=T0,
                metadata={"webhook_signing_key": "abc"},
            )

    def test_the_default_retention_is_normalized(self):
        evidence = DurableEvidenceRecord(
            evidence_id="ev-1", source_system="slack", source_type="message",
            source_id="1", event_type="chat_message",
            observed_at=T0, received_at=T0,
        )
        assert evidence.retention_class == "normalized"


class TestExecutionContext:
    def test_a_replay_context_must_suppress_side_effects(self):
        with pytest.raises(ValueError, match="must suppress side effects"):
            ExecutionContext(
                mode=ExecutionMode.REPLAY,
                clock=ReplayClock(replay_as_of=T0),
                suppress_side_effects=False,
            )

    def test_a_live_context_may_act(self):
        context = ExecutionContext(
            mode=ExecutionMode.LIVE, clock=ReplayClock(replay_as_of=T0),
        )
        assert context.is_replay is False

    def test_the_clock_is_the_replay_clock(self):
        context = ExecutionContext(
            mode=ExecutionMode.REPLAY, clock=ReplayClock(replay_as_of=at(10)),
            suppress_side_effects=True,
        )
        assert context.clock.now() == at(10)
        assert context.is_replay is True


class TestReplayRequestSafety:
    def test_replay_does_not_publish_by_default(self):
        assert ReplayRequest(organization_id=ORGANIZATION).publish_result is (
            False
        )

    def test_reevaluation_may_never_overwrite_history(self):
        with pytest.raises(ValueError, match="historical agent output"):
            ReplayRequest(
                organization_id=ORGANIZATION,
                mode=ReplayMode.REEVALUATE_WITH_CURRENT,
                publish_result=True,
            )

    def test_a_backwards_range_is_refused(self):
        with pytest.raises(ValueError, match="precedes"):
            ReplayRequest(
                organization_id=ORGANIZATION,
                from_global_sequence=10, to_global_sequence=5,
            )


class TestDeterministicSeeds:
    def test_the_seed_is_derived_from_semantic_state(self):
        first = derive_simulation_seed(
            revision_id="revision-1", project_id="project-a",
            scenario_input_fingerprint="fp-1",
        )
        second = derive_simulation_seed(
            revision_id="revision-1", project_id="project-a",
            scenario_input_fingerprint="fp-1",
        )
        assert first == second

    def test_different_state_gives_a_different_seed(self):
        assert derive_simulation_seed(
            revision_id="revision-1", project_id="project-a",
            scenario_input_fingerprint="fp-1",
        ) != derive_simulation_seed(
            revision_id="revision-1", project_id="project-a",
            scenario_input_fingerprint="fp-2",
        )

    def test_the_seed_is_a_usable_integer(self):
        seed = derive_simulation_seed(
            revision_id="r", project_id="p", scenario_input_fingerprint="f",
        )
        assert isinstance(seed, int)
        assert seed >= 0
