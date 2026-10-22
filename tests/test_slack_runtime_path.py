"""Normalized Slack evidence reaching durable history.

The gap this closes for Slack is the one the normalizers closed for GitHub:
before a normalizer existed, real chat data could reach the Claude tool
loop and could not reach a single engine. What is asserted here is the
whole path — documented payload → ``EvidenceEnvelope`` →
``process_durable_organizational_events`` → hash-chained journal — and
the four properties that make that path trustworthy rather than merely
working:

- **Reality time survives.** A February message ingested in October is
  journalled as February. Stamping it ``now`` would corrupt every
  scenario rooted in it, and the journal is append-only, so the
  corruption would be permanent.
- **The chain stays valid.** Evidence appended through this path does
  not break the hash chain that makes the journal auditable.
- **Re-ingesting appends nothing.** The point of a clock-free
  idempotency key, proven where it matters: at the durable boundary,
  not in a unit test of the key function.
- **Nothing durable is erased.** An edit lands beside the original and
  a deletion lands beside both, so a question asked as of any past
  moment still gets the answer that was true then.

Nothing here reaches the network. The journal and stores are in-memory,
the payloads are committed fixtures, and the reader is not involved —
this is the ingest path, not the fetch path.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pytest

from lltpm.integrations.slack_normalizer import (
    SOURCE_AVAILABILITY_DELETED,
    normalize_slack_message_changed,
    normalize_slack_message_deleted,
    normalize_slack_payloads,
)
from tests import _slack_fixtures as fx

ORGANIZATION = "acme"

#: When we learned. Deliberately eight months after the facts, so a
#: normalizer that stamped the fetch time would be caught rather than
#: merely suspected.
RECEIVED = datetime(2026, 10, 21, 4, 40)
LATER = datetime(2026, 10, 22, 9, 15)

SAID_AT = datetime(2026, 2, 25, 9, 0, 0, 100)
EDITED_AT = datetime(2026, 2, 25, 9, 20, 0, 300)


@pytest.fixture
def db():
    from lltpm.persistence import (
        InMemoryEventJournalStore,
        InMemoryIntelligenceArtifactStore,
        InMemoryRuntimeStateStore,
    )

    return {
        "journal": InMemoryEventJournalStore(),
        "state": InMemoryRuntimeStateStore(),
        "artifacts": InMemoryIntelligenceArtifactStore(),
    }


def _ingest(envelopes, *, db, now):
    from lltpm.persistence import build_manifest
    from lltpm.runtime import (
        RuntimeStateStore as RuntimeProjection,
        process_durable_organizational_events,
    )
    from lltpm.runtime.baselines import baseline_providers
    from lltpm.runtime.services import LiveIntelligenceServices
    from lltpm.runtime.sources import JournalBackedDataSource
    from lltpm.scenarios.models import ScenarioHorizon

    journal = db["journal"]
    services = LiveIntelligenceServices(
        data_source=JournalBackedDataSource(
            journal=journal, organization_id=ORGANIZATION,
        ),
        as_of=now,
        scenario_horizon=ScenarioHorizon(
            start_at=now, end_at=now + timedelta(days=14),
            maximum_depth=4, minimum_branch_probability=0.02,
        ),
        **baseline_providers(),
    )
    return process_durable_organizational_events(
        envelopes=envelopes,
        organization_id=ORGANIZATION,
        journal=journal,
        state_store=db["state"],
        artifact_store=db["artifacts"],
        runtime_projection=RuntimeProjection(organization_id=ORGANIZATION),
        intelligence_services=services,
        manifest=build_manifest(policy_fingerprint="test"),
        now=now,
    )


def _normalize(*, received_at=RECEIVED, bindings=None):
    """The whole fixture corpus, the way an ingest run would see it.

    Polled messages and delivered events together on purpose: a
    deployment reads both, and the keys have to keep them straight.
    """
    outcome = normalize_slack_payloads(
        messages=(
            fx.load("messages") + fx.load("declined_messages")
            + fx.load("channel_scope") + fx.load("message_changed")
            + fx.load("message_deleted")
        ),
        events=fx.load("events"),
        bindings=bindings or fx.bindings(),
        received_at=received_at,
        workspace_id=fx.WORKSPACE,
        channel_id=fx.CHANNEL,
    )
    assert outcome.envelopes, "nothing to ingest; the fixtures changed"
    return outcome


def _evidence_records(journal):
    return [
        record for record in journal.read_from(global_sequence=1)
        if record.event_type.value == "evidence_accepted"
    ]


# ── The path exists ──────────────────────────────────────────────────


class TestSlackDataReachesDurableHistory:
    @pytest.fixture
    def envelopes(self):
        return _normalize().envelopes

    def test_normalized_slack_data_becomes_durable_evidence(
        self, db, envelopes,
    ):
        durable, _ = _ingest(envelopes, db=db, now=RECEIVED)
        assert durable.evidence_records_appended == len(envelopes)
        assert len(db["journal"]) >= len(envelopes)

    def test_the_journal_hash_chain_stays_valid(self, db, envelopes):
        from lltpm.persistence import verify_journal

        _ingest(envelopes, db=db, now=RECEIVED)
        assert verify_journal(journal=db["journal"]).valid

    def test_every_record_carries_the_project_the_binding_declared(
        self, db, envelopes,
    ):
        _ingest(envelopes, db=db, now=RECEIVED)
        for record in _evidence_records(db["journal"]):
            assert record.payload["evidence"]["project_ids"] == [fx.PROJECT]

    def test_the_source_system_survives_into_the_journal(
        self, db, envelopes,
    ):
        _ingest(envelopes, db=db, now=RECEIVED)
        systems = {
            record.payload["evidence"]["source_system"]
            for record in _evidence_records(db["journal"])
        }
        assert systems == {"slack"}

    def test_no_credential_reaches_the_journal(self, db):
        """The journal is append-only, so a credential that reaches it is
        permanent. Checked over the whole serialized journal rather than
        over one envelope, because the append path is where it would
        actually land."""
        import json

        from lltpm.integrations.envelopes import redact_secret_text

        payload = copy.deepcopy(fx.thread_root())
        payload["text"] = (
            "repro with ghp_abcdefghijklmnopqrstuvwxyz0123 and "
            "xoxb-1234567890-abcdefghijklmnop"
        )
        outcome = normalize_slack_payloads(
            messages=[payload], bindings=fx.bindings(),
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        _ingest(outcome.envelopes, db=db, now=RECEIVED)
        serialized = json.dumps([
            record.model_dump(mode="json")
            for record in db["journal"].read_from(global_sequence=1)
        ])
        _, found = redact_secret_text(serialized)
        assert not found


# ── Reality time ─────────────────────────────────────────────────────


class TestRealityTimeSurvives:
    @pytest.fixture
    def envelopes(self):
        return _normalize().envelopes

    def test_february_facts_are_journalled_as_february(
        self, db, envelopes,
    ):
        """A message said in February and read in October occurred in
        February. The whole bitemporal layer depends on this one
        conversion not defaulting."""
        _ingest(envelopes, db=db, now=RECEIVED)
        records = _evidence_records(db["journal"])
        assert records
        for record in records:
            assert record.occurred_at.year == 2026
            assert record.occurred_at.month == 2, (
                "the journal must record when it was said, not when we "
                "polled for it"
            )

    def test_knowledge_time_is_recorded_separately_and_later(
        self, db, envelopes,
    ):
        _ingest(envelopes, db=db, now=RECEIVED)
        for record in _evidence_records(db["journal"]):
            assert record.recorded_at == RECEIVED
            assert record.recorded_at > record.occurred_at, (
                "valid time and transaction time are never collapsed"
            )

    def test_the_microsecond_suffix_reaches_the_journal(
        self, db, envelopes,
    ):
        """Slack's six-digit suffix is what keeps two messages in the
        same second apart. Losing it in the journal would merge them in
        any temporal ordering."""
        _ingest(envelopes, db=db, now=RECEIVED)
        moments = {
            record.occurred_at
            for record in _evidence_records(db["journal"])
        }
        assert SAID_AT in moments
        assert SAID_AT.microsecond == 100
        assert all(moment.microsecond for moment in moments), (
            "a truncated suffix would make two messages in one second "
            "indistinguishable in any temporal ordering"
        )

    def test_two_messages_a_fraction_apart_stay_two_moments(self, db):
        """The failure a float round-trip would cause, asserted at the
        journal rather than at the parser."""
        close = []
        for suffix in ("000001", "000002"):
            payload = copy.deepcopy(fx.thread_root())
            payload["ts"] = f"1772010000.{suffix}"
            payload["thread_ts"] = payload["ts"]
            close.append(payload)
        outcome = normalize_slack_payloads(
            messages=close, bindings=fx.bindings(), received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        )
        _ingest(outcome.envelopes, db=db, now=RECEIVED)
        moments = sorted(
            record.occurred_at
            for record in _evidence_records(db["journal"])
        )
        assert len(moments) == 2
        assert (moments[1] - moments[0]) == timedelta(microseconds=1)

    def test_one_message_read_twice_by_two_routes_is_two_records(
        self, db,
    ):
        """A known and accepted limitation, stated rather than hidden.
        A delivered event keys on Slack's ``event_id`` and the same
        message polled from history keys on its ts, so a deployment that
        both receives webhooks and polls the same window records the
        utterance twice.

        The alternative would be to key a delivery on its content or its
        ts and throw away the provider's own event id — which is the
        thing published webhook guidance is unanimous about not doing,
        because a retried delivery would then become a second fact. The
        two records carry the same ``source_id``, so a consumer that
        cares can collapse them; nothing here silently picks one.

        GitHub has exactly the same seam between a delivery id and
        ``node_id`` + ``updated_at``, so this is a property of the design
        rather than of Slack.
        """
        polled = next(
            message for message in fx.load("messages")
            if message.get("event_ts")
        )
        outcome = normalize_slack_payloads(
            messages=[polled], events=[fx.event("message")],
            bindings=fx.bindings(), received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        )
        assert len(outcome.envelopes) == 2
        durable, _ = _ingest(outcome.envelopes, db=db, now=RECEIVED)
        assert durable.evidence_records_appended == 2
        records = _evidence_records(db["journal"])
        assert len({
            record.payload["evidence"]["source_id"] for record in records
        }) == 1, (
            "the two records must at least agree on which message they "
            "describe, or they cannot be collapsed later"
        )
        assert len({record.occurred_at for record in records}) == 1

    def test_a_later_ingest_does_not_move_a_fact(self, db, envelopes):
        _ingest(envelopes, db=db, now=RECEIVED)
        before = {
            record.payload["evidence"]["idempotency_key"]:
                record.occurred_at
            for record in _evidence_records(db["journal"])
        }
        _ingest(_normalize(received_at=LATER).envelopes, db=db, now=LATER)
        after = {
            record.payload["evidence"]["idempotency_key"]:
                record.occurred_at
            for record in _evidence_records(db["journal"])
        }
        assert before == after, (
            "arrival order is not temporal authority; re-reading a channel "
            "cannot move when something was said"
        )


# ── Idempotency at the durable boundary ──────────────────────────────


class TestReIngestingAppendsNothing:
    @pytest.fixture
    def envelopes(self):
        return _normalize().envelopes

    def test_the_same_batch_a_day_later_appends_nothing(
        self, db, envelopes,
    ):
        """The point of a stable key, proven where it matters: at the
        durable boundary, not in a unit test of the key function."""
        _ingest(envelopes, db=db, now=RECEIVED)
        before = len(db["journal"])
        baseline = len(_evidence_records(db["journal"]))

        repeat = _normalize(received_at=LATER, bindings=fx.bindings())
        durable, _ = _ingest(repeat.envelopes, db=db, now=LATER)

        assert durable.evidence_records_appended == 0
        assert durable.evidence_records_deduplicated == len(
            repeat.envelopes
        )
        assert len(_evidence_records(db["journal"])) == baseline
        assert len(db["journal"]) >= before

    def test_a_retried_delivery_is_one_record(self, db):
        """Slack retries a delivery under the same ``event_id``. The key
        is that id, so the retry deduplicates even though our receive
        time moved."""
        first = normalize_slack_payloads(
            events=[fx.event("message")], bindings=fx.bindings(),
            received_at=RECEIVED,
        )
        _ingest(first.envelopes, db=db, now=RECEIVED)
        second = normalize_slack_payloads(
            events=[fx.event("message")], bindings=fx.bindings(),
            received_at=LATER,
        )
        durable, _ = _ingest(second.envelopes, db=db, now=LATER)
        assert durable.evidence_records_appended == 0
        assert len(_evidence_records(db["journal"])) == 1

    def test_the_chain_is_still_valid_after_a_duplicate_batch(
        self, db, envelopes,
    ):
        from lltpm.persistence import verify_journal

        _ingest(envelopes, db=db, now=RECEIVED)
        _ingest(_normalize(received_at=LATER).envelopes, db=db, now=LATER)
        assert verify_journal(journal=db["journal"]).valid

    def test_every_appended_record_has_a_distinct_key(
        self, db, envelopes,
    ):
        _ingest(envelopes, db=db, now=RECEIVED)
        keys = [
            record.payload["evidence"]["idempotency_key"]
            for record in _evidence_records(db["journal"])
        ]
        assert len(keys) == len(set(keys))
        for key in keys:
            assert key.startswith("slack:")
            for stamp in (str(RECEIVED.year), RECEIVED.isoformat()):
                assert stamp not in key, (
                    "a key carrying our clock would turn one message into "
                    "an unbounded stream of messages"
                )


# ── Edits and deletions in durable history ───────────────────────────


class TestEditsAndDeletionsDoNotRewriteHistory:
    """The behaviour an append-only bitemporal store needs and a chat
    client cannot have."""

    @pytest.fixture
    def versions(self):
        original = normalize_slack_payloads(
            messages=[fx.thread_root()], bindings=fx.bindings(),
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        edit = normalize_slack_message_changed(
            fx.message_changed(), bindings=fx.bindings(),
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
        )
        assert len(original.envelopes) == 1 and len(edit.envelopes) == 1
        return original.envelopes + edit.envelopes

    def test_both_versions_are_in_the_journal(self, db, versions):
        durable, _ = _ingest(versions, db=db, now=RECEIVED)
        assert durable.evidence_records_appended == 2
        texts = [
            record.payload["evidence"]["normalized_content"]
            for record in _evidence_records(db["journal"])
        ]
        assert any("v2 import path" in text for text in texts)
        assert any("v3 import path" in text for text in texts)

    def test_a_query_as_of_nine_ten_sees_v2_and_not_v3(self, db, versions):
        """The question an overwrite would answer wrongly, asked against
        the journal rather than against the envelopes."""
        _ingest(versions, db=db, now=RECEIVED)
        as_of = datetime(2026, 2, 25, 9, 10)
        visible = [
            record for record in _evidence_records(db["journal"])
            if record.occurred_at <= as_of
        ]
        assert len(visible) == 1
        assert "v2 import path" in (
            visible[0].payload["evidence"]["normalized_content"]
        )

    def test_a_query_as_of_nine_thirty_sees_v3(self, db, versions):
        _ingest(versions, db=db, now=RECEIVED)
        as_of = datetime(2026, 2, 25, 9, 30)
        visible = sorted(
            (
                record for record in _evidence_records(db["journal"])
                if record.occurred_at <= as_of
            ),
            key=lambda record: record.occurred_at,
        )
        assert len(visible) == 2
        assert "v3 import path" in (
            visible[-1].payload["evidence"]["normalized_content"]
        )

    def test_the_two_versions_are_dated_to_their_own_moments(
        self, db, versions,
    ):
        _ingest(versions, db=db, now=RECEIVED)
        moments = sorted(
            record.occurred_at
            for record in _evidence_records(db["journal"])
        )
        assert moments == [SAID_AT, EDITED_AT]

    def test_the_edit_keeps_the_originals_source_id(self, db, versions):
        """So the two are joinable as versions of one message rather
        than as two unrelated utterances."""
        _ingest(versions, db=db, now=RECEIVED)
        source_ids = {
            record.payload["evidence"]["source_id"]
            for record in _evidence_records(db["journal"])
        }
        assert len(source_ids) == 1

    def test_a_deletion_leaves_both_versions_in_place(self, db, versions):
        """Somebody deleting the message in Slack cannot un-say what the
        organization already read and acted on."""
        _ingest(versions, db=db, now=RECEIVED)
        before = len(_evidence_records(db["journal"]))

        deletion = normalize_slack_message_deleted(
            fx.message_deleted(), bindings=fx.bindings(),
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
        )
        durable, _ = _ingest(deletion.envelopes, db=db, now=RECEIVED)

        assert durable.evidence_records_appended == 1
        records = _evidence_records(db["journal"])
        assert len(records) == before + 1
        texts = [
            record.payload["evidence"]["normalized_content"]
            for record in records
        ]
        assert any("v2 import path" in (text or "") for text in texts)
        assert any("v3 import path" in (text or "") for text in texts)

    def test_the_deletion_record_names_the_source_that_went_away(
        self, db, versions,
    ):
        """The seam the durability layer reads: it can append a
        SOURCE_AVAILABILITY_CHANGED record against this source without
        touching the evidence itself."""
        _ingest(versions, db=db, now=RECEIVED)
        deletion = normalize_slack_message_deleted(
            fx.message_deleted(), bindings=fx.bindings(),
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
        )
        _ingest(deletion.envelopes, db=db, now=RECEIVED)

        record = next(
            record for record in _evidence_records(db["journal"])
            if record.payload["evidence"]["source_type"]
            == "source_availability"
        )
        metadata = record.payload["evidence"]["metadata"]
        assert metadata["source_availability"] == SOURCE_AVAILABILITY_DELETED
        assert metadata["unavailable_source_id"] in {
            other.payload["evidence"]["source_id"]
            for other in _evidence_records(db["journal"])
        }

    def test_the_deletion_carries_no_text_into_the_journal(self, db):
        """A layer whose job is to respect a deletion has no business
        re-journalling the deleted text as new content; the original is
        already there as the fact it was."""
        deletion = normalize_slack_message_deleted(
            fx.message_deleted(), bindings=fx.bindings(),
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
        )
        _ingest(deletion.envelopes, db=db, now=RECEIVED)
        record = _evidence_records(db["journal"])[0]
        assert record.payload["evidence"]["normalized_content"] is None

    def test_the_chain_survives_an_edit_and_a_deletion(self, db, versions):
        from lltpm.persistence import verify_journal

        _ingest(versions, db=db, now=RECEIVED)
        deletion = normalize_slack_message_deleted(
            fx.message_deleted(), bindings=fx.bindings(),
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
        )
        _ingest(deletion.envelopes, db=db, now=RECEIVED)
        assert verify_journal(journal=db["journal"]).valid


# ── Replay ───────────────────────────────────────────────────────────


class TestReplayTouchesNothingExternal:
    def test_the_evidence_is_rebuildable_without_slack(self, db):
        """Replay must not need Slack to still be reachable, which is
        the whole reason evidence is journalled in normalized form
        rather than as a pointer."""
        from lltpm.persistence.models import DurableEvidenceRecord
        from lltpm.runtime.durability import durable_record_to_envelope

        envelopes = _normalize().envelopes
        _ingest(envelopes, db=db, now=RECEIVED)
        rebuilt = [
            durable_record_to_envelope(DurableEvidenceRecord.model_validate(
                record.payload["evidence"]
            ))
            for record in _evidence_records(db["journal"])
        ]
        assert len(rebuilt) == len(envelopes)
        assert {envelope.idempotency_key for envelope in rebuilt} == {
            envelope.idempotency_key for envelope in envelopes
        }
        assert {envelope.observed_at for envelope in rebuilt} == {
            envelope.observed_at for envelope in envelopes
        }
