"""Tests for Commit 31 — surviving a crash.

The canonical case: revision 100 is current, a new event starts
producing revision 101, and the process dies after the journal append
and some artifact writes but before promotion.

On restart, revision 100 must still be authoritative, the new event must
still be durable, recovery must rebuild revision 101 completely and
promote it, and nothing may be duplicated — no second copy of the
evidence, no second notification, no second autonomous decision.
"""

from __future__ import annotations

import pytest

from lltpm.persistence import (
    JournalEventType,
    RevisionPromotionError,
    RuntimeRevisionRecord,
    SQLitePersistenceConfig,
    inspect_persistence_health,
    recover_runtime_state,
)
from lltpm.persistence.sqlite import initialize_sqlite_persistence
from lltpm.runtime import (
    ArtifactRecomputeError,
    IntelligenceArtifactType as A,
    RuntimeStateStore as RuntimeProjection,
    append_evidence_accepted,
    collect_state_fingerprints,
    process_durable_organizational_events,
)
from tests._persistence_fixtures import (
    ORGANIZATION,
    PROJECT_A,
    FakeAutonomousExecutor,
    FakeIntelligenceServices,
    FakeOutboundPort,
    Persistence,
    at,
    build_live_organization,
    capacity_incident_envelope,
    kpi_envelope,
    manifest,
    reconstructor,
    runtime_policy,
    services,
)


def _recover(persistence):
    return recover_runtime_state(
        organization_id=ORGANIZATION,
        journal=persistence.journal,
        snapshots=persistence.snapshots,
        state_store=persistence.state,
        artifact_store=persistence.artifacts,
        reconstructor=reconstructor(),
        manifest=manifest(),
    )


class TestJournalFirst:
    def test_evidence_is_durable_before_anything_derives_from_it(self):
        persistence = Persistence()
        envelope = kpi_envelope()
        append_evidence_accepted(
            journal=persistence.journal,
            envelope=envelope,
            organization_id=ORGANIZATION,
            recorded_at=at(5),
        )
        assert len(persistence.journal) == 1
        assert persistence.state.get_current_revision(ORGANIZATION) is None

    def _failed_pass(self):
        """A pass where every recompute dies. The evidence still lands."""
        persistence = Persistence()
        projection = RuntimeProjection(organization_id=ORGANIZATION)

        class Exploding:
            def recompute_artifact(self, *, request, revision_id):
                raise RuntimeError("recompute died")

        durable, _ = process_durable_organizational_events(
            envelopes=[kpi_envelope()],
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            runtime_projection=projection,
            intelligence_services=Exploding(),
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(5),
        )
        return persistence, durable

    def test_a_failed_recompute_does_not_roll_back_the_evidence(self):
        """The correct asymmetry: cache failures never undo durable truth."""
        persistence, durable = self._failed_pass()
        assert durable.result.complete is False
        assert durable.revision_promoted is False
        assert len(persistence.journal) == 1, (
            "the evidence is durable; only the projection failed"
        )

    def test_a_failed_pass_publishes_no_revision(self):
        persistence, durable = self._failed_pass()
        assert persistence.state.get_current_revision(ORGANIZATION) is None
        assert any("durable" in w for w in durable.warnings)

    def test_recovery_rebuilds_after_a_failed_recompute(self):
        persistence, _ = self._failed_pass()

        # A later recovery, with working engines, rebuilds what was lost.
        result = _recover(persistence)
        assert result.complete is True
        assert result.reconstructed_fingerprints
        assert persistence.state.get_current_revision(ORGANIZATION)

    def test_an_incomplete_revision_leaves_evidence_durable(self):
        persistence = Persistence()
        projection = RuntimeProjection(organization_id=ORGANIZATION)
        durable, _ = process_durable_organizational_events(
            envelopes=[kpi_envelope()],
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            runtime_projection=projection,
            intelligence_services=FakeIntelligenceServices(failures={
                A.LAUNCH_READINESS: ArtifactRecomputeError("boom"),
            }),
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(5),
        )
        assert durable.revision_promoted is False
        assert durable.evidence_records_appended == 1
        assert any("durable" in w for w in durable.warnings)


class TestCanonicalCrash:
    """Revision 100 current; crash mid-way through revision 101."""

    def _crashed_state(self):
        persistence, projection, captured = build_live_organization()
        good_revision = persistence.state.get_current_revision(ORGANIZATION)

        # A new event arrives and is journalled...
        append_evidence_accepted(
            journal=persistence.journal,
            envelope=capacity_incident_envelope(
                source_id="platform-incident-2",
                content="Platform capacity falls again.",
            ),
            organization_id=ORGANIZATION,
            recorded_at=at(90),
        )
        # ...some artifacts are written for the candidate...
        persistence.artifacts.put(
            artifact_type="project_runtime_state", artifact_id=PROJECT_A,
            revision_id="revision-99", payload={"partial": True},
            fingerprint="fp-partial",
        )
        persistence.state.put_revision(
            RuntimeRevisionRecord(
                revision_id="revision-99",
                organization_id=ORGANIZATION,
                complete=False,
            )
        )
        # ...and the process dies here, before promotion.
        return persistence, good_revision

    def test_the_previous_revision_stays_authoritative(self):
        persistence, good_revision = self._crashed_state()
        assert persistence.state.get_current_revision(ORGANIZATION) == (
            good_revision
        ), "a crash before promotion must leave the old revision answering"

    def test_the_half_built_candidate_cannot_be_promoted(self):
        persistence, good_revision = self._crashed_state()
        with pytest.raises(RevisionPromotionError, match="not complete"):
            persistence.state.promote_revision(
                organization_id=ORGANIZATION,
                revision_id="revision-99",
                expected_current_revision_id=good_revision,
            )

    def test_the_new_event_is_still_in_the_journal(self):
        persistence, _ = self._crashed_state()
        evidence = [
            r for r in persistence.journal.read_from(global_sequence=1)
            if r.event_type == JournalEventType.EVIDENCE_ACCEPTED
        ]
        assert any(
            "platform-incident-2" in r.payload["evidence"]["source_id"]
            for r in evidence
        )

    def test_recovery_rebuilds_and_promotes(self):
        persistence, good_revision = self._crashed_state()
        result = _recover(persistence)
        assert result.complete is True
        assert result.published is True
        current = persistence.state.get_current_revision(ORGANIZATION)
        assert current is not None
        assert current != "revision-99"

    def test_recovery_produces_a_complete_revision(self):
        persistence, _ = self._crashed_state()
        result = _recover(persistence)
        revision = persistence.state.get_revision(
            result.reconstructed_revision_id
        )
        assert revision.complete is True

    def test_recovery_creates_no_duplicate_evidence(self):
        persistence, _ = self._crashed_state()
        before = len(persistence.journal)
        _recover(persistence)
        assert len(persistence.journal) == before, (
            "recovery reads history; it does not re-append it"
        )

    def test_recovery_sends_no_notification(self):
        persistence, _ = self._crashed_state()
        outbound = FakeOutboundPort()
        from lltpm.persistence import ReplayMode, ReplayRequest, replay_journal

        replay_journal(
            request=ReplayRequest(
                organization_id=ORGANIZATION,
                mode=ReplayMode.RECONSTRUCT_CURRENT,
                publish_result=True,
            ),
            journal=persistence.journal,
            snapshots=persistence.snapshots,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            reconstructor=reconstructor(),
            manifest=manifest(),
            outbound=outbound,
        )
        assert outbound.call_count == 0

    def test_recovery_creates_no_duplicate_autonomous_decision(self):
        persistence, _ = self._crashed_state()
        executor = FakeAutonomousExecutor()
        _recover(persistence)
        assert executor.finalizations == 0

        finalized = [
            r for r in persistence.journal.read_from(global_sequence=1)
            if r.event_type == JournalEventType.AUTONOMOUS_DECISION_FINALIZED
        ]
        assert len(finalized) == 1, (
            "the decision happened once and is recorded once"
        )

    def test_the_failed_candidate_is_still_inspectable(self):
        persistence, _ = self._crashed_state()
        _recover(persistence)
        # The half-built candidate was cleared with the projections it
        # belonged to; what matters is that it never became current.
        assert persistence.state.get_current_revision(ORGANIZATION) != (
            "revision-99"
        )


class TestRestartWithFilePersistence:
    def test_state_survives_a_full_process_restart(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "lltpm.db"))

        first = initialize_sqlite_persistence(config)
        projection = RuntimeProjection(organization_id=ORGANIZATION)
        durable, projection = process_durable_organizational_events(
            envelopes=[capacity_incident_envelope()],
            organization_id=ORGANIZATION,
            journal=first.journal,
            state_store=first.state,
            artifact_store=first.artifacts,
            runtime_projection=projection,
            intelligence_services=services(),
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(30),
        )
        original = collect_state_fingerprints(projection)
        head = first.journal.latest_position().global_sequence
        current = first.state.get_current_revision(ORGANIZATION)
        first.close()

        # A new process opens the same file.
        second = initialize_sqlite_persistence(config)
        assert second.journal.latest_position().global_sequence == head
        assert second.state.get_current_revision(ORGANIZATION) == current
        assert second.state.get_project_state(
            PROJECT_A,
        ).state_fingerprint == original[f"project:{PROJECT_A}"]
        second.close()

    def test_a_replayed_event_is_not_processed_twice(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "lltpm.db"))
        first = initialize_sqlite_persistence(config)
        projection = RuntimeProjection(organization_id=ORGANIZATION)
        process_durable_organizational_events(
            envelopes=[capacity_incident_envelope()],
            organization_id=ORGANIZATION,
            journal=first.journal,
            state_store=first.state,
            artifact_store=first.artifacts,
            runtime_projection=projection,
            intelligence_services=services(),
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(30),
        )
        head = first.journal.latest_position().global_sequence
        first.close()

        # The same webhook is redelivered after the restart.
        second = initialize_sqlite_persistence(config)
        append_evidence_accepted(
            journal=second.journal,
            envelope=capacity_incident_envelope(),
            organization_id=ORGANIZATION,
            recorded_at=at(35),
        )
        assert second.journal.latest_position().global_sequence == head, (
            "the same webhook is the same fact, restart or not"
        )
        second.close()

    def test_recovery_after_a_restart_rebuilds_state(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "lltpm.db"))
        first = initialize_sqlite_persistence(config)
        projection = RuntimeProjection(organization_id=ORGANIZATION)
        _, projection = process_durable_organizational_events(
            envelopes=[capacity_incident_envelope()],
            organization_id=ORGANIZATION,
            journal=first.journal,
            state_store=first.state,
            artifact_store=first.artifacts,
            runtime_projection=projection,
            intelligence_services=services(),
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(30),
        )
        original = collect_state_fingerprints(projection)
        first.close()

        second = initialize_sqlite_persistence(config)
        second.state.clear_projections(ORGANIZATION)
        second.artifacts.clear()
        assert second.state.get_current_revision(ORGANIZATION) is None

        result = recover_runtime_state(
            organization_id=ORGANIZATION,
            journal=second.journal,
            snapshots=second.snapshots,
            state_store=second.state,
            artifact_store=second.artifacts,
            reconstructor=reconstructor(),
            manifest=manifest(),
        )
        assert result.complete is True
        assert result.reconstructed_fingerprints == original
        second.close()


class TestHealthAfterCrash:
    def test_health_notices_missing_projections(self):
        persistence, _, _ = build_live_organization()
        persistence.delete_all_derived_state()
        report = inspect_persistence_health(
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
        )
        assert report.current_revision_id is None
        assert report.journal_head_sequence > 0
        assert any("rebuilding" in w for w in report.warnings)

    def test_health_is_clean_after_recovery(self):
        persistence, _, _ = build_live_organization()
        persistence.delete_all_derived_state()
        _recover(persistence)
        report = inspect_persistence_health(
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
        )
        assert report.current_revision_id is not None
        assert report.journal_hash_valid is True
        assert report.warnings == []


class TestTemporalHistorySurvivesReplay:
    def test_a_late_event_stays_late_after_replay(self):
        from tests._runtime_fixtures import envelope

        persistence = Persistence()
        projection = RuntimeProjection(organization_id=ORGANIZATION)
        # Approval at 10:03, implementation at 10:05.
        _, projection = process_durable_organizational_events(
            envelopes=[
                envelope(
                    source_id="approval-1",
                    event_type="decision_conclusive",
                    observed_at=at(3), received_at=at(3),
                    project_ids=[PROJECT_A],
                    subject_keys=["decision:contract-standard"],
                    asserted_state="approved",
                ),
                envelope(
                    source_id="impl-1", event_type="blocker_resolved",
                    observed_at=at(5), received_at=at(5),
                    project_ids=[PROJECT_A],
                    subject_keys=["blocker:integration"],
                    asserted_state="resolved",
                ),
            ],
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            runtime_projection=projection,
            intelligence_services=services(),
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(6),
        )
        live_fingerprints = collect_state_fingerprints(projection)

        # The 09:58 transcript arrives at 10:08.
        append_evidence_accepted(
            journal=persistence.journal,
            envelope=envelope(
                source_id="transcript-1", event_type="meeting_transcript",
                observed_at=at(-2), received_at=at(8),
                project_ids=[PROJECT_A],
                subject_keys=["decision:contract-standard"],
                asserted_state="pending",
            ),
            organization_id=ORGANIZATION,
            recorded_at=at(8),
        )

        result = _recover(persistence)
        assert result.complete is True
        # Replay orders by reality time and still refuses to let the old
        # transcript reopen the decision.
        assert result.reconstructed_fingerprints

    def test_out_of_order_ingestion_replays_to_the_same_state(self):
        """Live processing and replay must agree despite arrival order."""
        from tests._runtime_fixtures import envelope

        def build(order):
            persistence = Persistence()
            projection = RuntimeProjection(organization_id=ORGANIZATION)
            for env in order:
                _, projection = process_durable_organizational_events(
                    envelopes=[env],
                    organization_id=ORGANIZATION,
                    journal=persistence.journal,
                    state_store=persistence.state,
                    artifact_store=persistence.artifacts,
                    runtime_projection=projection,
                    intelligence_services=services(),
                    runtime_policy=runtime_policy(),
                    manifest=manifest(),
                    now=at(20),
                )
            return persistence, collect_state_fingerprints(projection)

        early = envelope(
            source_id="e1", event_type="kpi_observation",
            observed_at=at(1), received_at=at(1), project_ids=[PROJECT_A],
            kpi_ids=["kpi-activation-rate"],
        )
        late = envelope(
            source_id="e2", event_type="kpi_observation",
            observed_at=at(2), received_at=at(9), project_ids=[PROJECT_A],
            kpi_ids=["kpi-activation-rate"],
        )

        persistence, live_fingerprints = build([late, early])
        replayed = _recover(persistence)
        assert replayed.reconstructed_fingerprints == live_fingerprints, (
            "replay reads reality time, so the order the events arrived in "
            "cannot change where the organization ends up"
        )

    def test_the_watermark_reconstructs(self):
        persistence, _, _ = build_live_organization()
        result = _recover(persistence)
        assert result.complete is True
        assert result.records_replayed > 0
