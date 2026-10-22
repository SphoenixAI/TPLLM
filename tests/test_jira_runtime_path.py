"""The Jira path end to end: payloads → envelopes → durable journal.

A normalizer that produces envelopes nothing ingests is a translation
into a language no one speaks. This file is the proof that the Jira
lane reaches the journal, and it asserts the four properties that would
each be invisible in a unit test of the normalizer alone:

- **the records land**, as durable evidence with a valid hash chain;
- **historical reality time survives the whole way down**, so a
  transition recorded in September is journalled as September and not
  as the day the ingest ran;
- **re-ingesting the same batch appends nothing** — the property the
  idempotency keys exist for, proven at the durable boundary rather
  than by comparing two strings;
- **nothing about an individual's time reaches durable storage**, which
  is where it would become permanent.

The pattern is the one ``TestRealPayloadsReachTheJournal`` establishes
for GitHub, because the point is that a second provider needs no new
seam: the orchestrator already accepts ``EvidenceEnvelope`` and the Jira
normalizer already produces them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tpllm.integrations.jira_normalizer import normalize_jira_payloads
from tests import _jira_fixtures as fx

RECEIVED = datetime(2026, 10, 22, 4, 40)
LATER = datetime(2026, 10, 25, 9, 15)


class TestJiraPayloadsReachTheJournal:
    def _ingest(self, envelopes, *, db, now):
        from tpllm.persistence import build_manifest
        from tpllm.runtime import (
            RuntimeStateStore as RuntimeProjection,
            process_durable_organizational_events,
        )
        from tpllm.runtime.baselines import baseline_providers
        from tpllm.runtime.services import LiveIntelligenceServices
        from tpllm.runtime.sources import JournalBackedDataSource
        from tpllm.scenarios.models import ScenarioHorizon

        journal = db["journal"]
        services = LiveIntelligenceServices(
            data_source=JournalBackedDataSource(
                journal=journal, organization_id="acme",
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
            organization_id="acme",
            journal=journal,
            state_store=db["state"],
            artifact_store=db["artifacts"],
            runtime_projection=RuntimeProjection(organization_id="acme"),
            intelligence_services=services,
            manifest=build_manifest(policy_fingerprint="test"),
            now=now,
        )

    @pytest.fixture
    def db(self):
        from tpllm.persistence import (
            InMemoryEventJournalStore,
            InMemoryIntelligenceArtifactStore,
            InMemoryRuntimeStateStore,
        )

        return {
            "journal": InMemoryEventJournalStore(),
            "state": InMemoryRuntimeStateStore(),
            "artifacts": InMemoryIntelligenceArtifactStore(),
        }

    def _normalize(self, *, received_at):
        outcome = normalize_jira_payloads(
            issues=fx.load("issues"), bindings=fx.bindings(),
            received_at=received_at, site=fx.SITE,
        )
        assert outcome.envelopes, "nothing to ingest"
        assert not outcome.needs_attention, (
            "an unbound project or a rejected payload would mean this test "
            "was measuring the failure path"
        )
        return outcome.envelopes

    @pytest.fixture
    def envelopes(self):
        return self._normalize(received_at=RECEIVED)

    def test_normalized_jira_data_becomes_durable_evidence(
        self, db, envelopes,
    ):
        durable, _ = self._ingest(envelopes, db=db, now=RECEIVED)
        assert durable.evidence_records_appended == len(envelopes)
        assert len(db["journal"]) >= len(envelopes)

    def test_the_journal_hash_chain_stays_valid(self, db, envelopes):
        from tpllm.persistence import verify_journal

        self._ingest(envelopes, db=db, now=RECEIVED)
        assert verify_journal(journal=db["journal"]).valid

    def test_re_reading_an_unchanged_project_appends_nothing(
        self, db, envelopes,
    ):
        """The whole point of a key that never moves with our clock,
        proven where it matters: at the durable boundary. A Jira project
        is polled on a schedule, so a key that drifted would turn one
        backlog into an unbounded stream of facts within a day."""
        self._ingest(envelopes, db=db, now=RECEIVED)
        before = len(db["journal"])

        again = self._normalize(received_at=LATER)
        durable, _ = self._ingest(again, db=db, now=LATER)

        assert durable.evidence_records_appended == 0
        assert durable.evidence_records_deduplicated == len(again)
        accepted = self._accepted(db)
        assert len(accepted) == len(envelopes)
        assert len(db["journal"]) >= before

    def test_a_transition_keeps_its_historical_reality_time(
        self, db, envelopes,
    ):
        """The September status change is journalled as September. The
        ingest ran in October, and a stack that recorded the poll time
        would place five weeks of history on one day — after which every
        scenario rooted in it inherits the error."""
        self._ingest(envelopes, db=db, now=RECEIVED)
        transitions = [
            record for record in self._accepted(db)
            if record.payload["evidence"]["metadata"].get(
                "reality_time_basis"
            ) == "changelog_entry_created"
        ]
        assert transitions, "no changelog-derived evidence reached the journal"
        assert any(record.occurred_at.month == 9 for record in transitions), (
            "the journal must record when the ticket moved, not when we "
            "read the changelog"
        )
        for record in transitions:
            assert record.recorded_at >= record.occurred_at, (
                "knowledge time is never earlier than reality time"
            )

    def test_reality_and_knowledge_time_stay_separate(self, db, envelopes):
        self._ingest(envelopes, db=db, now=RECEIVED)
        for record in self._accepted(db):
            assert record.recorded_at.date() == RECEIVED.date()
            assert record.occurred_at <= record.recorded_at

    def test_a_completed_ticket_is_journalled_as_a_ticket(
        self, db, envelopes,
    ):
        """It reaches the journal as evidence about a ticket, which is
        the narrow claim the mapping makes — not as a readiness or
        decision transition."""
        self._ingest(envelopes, db=db, now=RECEIVED)
        classes = {
            record.payload["evidence"]["event_type"]
            for record in self._accepted(db)
        }
        assert "ticket_completed" in classes
        assert classes <= {
            "ticket_completed", "ticket_updated", "chat_message",
            "blocker_opened", "blocker_resolved",
        }
        assert not classes & {
            "decision_conclusive", "outcome_observed", "deployment",
        }, "a Jira ticket must not reach the journal as a decision"

    def test_the_project_binding_survives_into_the_journal(
        self, db, envelopes,
    ):
        self._ingest(envelopes, db=db, now=RECEIVED)
        for record in self._accepted(db):
            assert fx.PROJECT in record.payload["evidence"]["project_ids"]

    def test_no_individual_time_record_reaches_durable_storage(
        self, db, envelopes,
    ):
        """The journal is append-only, so anything that lands here is
        permanent. Jira hands us logged hours on every expanded issue;
        the refusal has to hold at this boundary and not merely in a
        report."""
        self._ingest(envelopes, db=db, now=RECEIVED)
        for record in self._accepted(db):
            blob = record.model_dump_json()
            assert "timeSpent" not in blob
            assert "worklog" not in blob

    def test_the_evidence_is_replayable_as_temporal_events(
        self, db, envelopes,
    ):
        """What makes the journal worth writing to: the normalized Jira
        evidence can be rebuilt into temporal events without re-reading
        Jira, which is also why replay never re-sends anything."""
        from tpllm.runtime.sources import evidence_to_temporal_event

        self._ingest(envelopes, db=db, now=RECEIVED)
        events = []
        for record in self._accepted(db):
            evidence = _durable_evidence(record)
            event = evidence_to_temporal_event(
                evidence, project_id=fx.PROJECT,
            )
            if event is not None:
                events.append(event)
        assert events
        for event in events:
            assert event.occurred_at <= event.known_at

    def _accepted(self, db):
        return [
            record for record in db["journal"].read_from(global_sequence=1)
            if record.event_type.value == "evidence_accepted"
        ]


def _durable_evidence(record):
    from tpllm.persistence.models import DurableEvidenceRecord

    return DurableEvidenceRecord.model_validate(record.payload["evidence"])
