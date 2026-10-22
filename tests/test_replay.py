"""Replay from durable history.

Including the defining acceptance test: delete every derived artifact,
replay the journal, and get semantically equivalent organizational
intelligence back.

Artifact ids may carry generated identifiers, so equivalence is checked
on semantic state fingerprints rather than byte identity — which is the
honest comparison, and the one the spec asks for.
"""

from __future__ import annotations

import pytest

from tpllm.persistence import (
    InMemoryEventJournalStore,
    JournalEventType,
    ReplayMode,
    ReplayRequest,
    UnsupportedJournalEventError,
    historical_records,
    inspect_persistence_health,
    rebuild_runtime_projections,
    recover_runtime_state,
    replay_journal,
    verify_replay_equivalence,
)
from tests._persistence_fixtures import (
    ORGANIZATION,
    PROJECT_A,
    PROJECT_C,
    PROJECT_D,
    Persistence,
    at,
    build_live_organization,
    engage_kill_switch,
    make_snapshot,
    manifest,
    mark_source_deleted,
    reconstructor,
    record,
    services,
)


@pytest.fixture(scope="module")
def live():
    """One synthetic organization, run live and fully journalled."""
    return build_live_organization()


def _replay(persistence, **overrides):
    request = ReplayRequest(
        organization_id=ORGANIZATION,
        mode=overrides.pop("mode", ReplayMode.RECONSTRUCT_CURRENT),
        **overrides,
    )
    return replay_journal(
        request=request,
        journal=persistence.journal,
        snapshots=persistence.snapshots,
        state_store=persistence.state,
        artifact_store=persistence.artifacts,
        reconstructor=reconstructor(),
        manifest=manifest(),
    )


class TestDefiningAcceptance:
    """Delete everything derived. Replay. Compare."""

    def _rebuild(self):
        persistence, projection, captured = build_live_organization()
        original = dict(captured["fingerprints"])
        persistence.delete_all_derived_state()
        result = rebuild_runtime_projections(
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            snapshots=persistence.snapshots,
            reconstructor=reconstructor(),
            manifest=manifest(),
        )
        return persistence, original, result

    def test_deletion_really_removes_the_projections(self):
        persistence, _, captured = build_live_organization()
        assert persistence.state.get_current_revision(ORGANIZATION)
        persistence.delete_all_derived_state()
        assert persistence.state.get_current_revision(ORGANIZATION) is None
        assert persistence.state.get_project_state(PROJECT_A) is None
        assert len(persistence.artifacts) == 0

    def test_the_journal_alone_is_enough(self):
        _, original, result = self._rebuild()
        assert result.complete is True
        assert result.records_replayed > 0

    def test_every_project_fingerprint_is_reconstructed_identically(self):
        _, original, result = self._rebuild()
        for key, value in original.items():
            if not key.startswith("project:"):
                continue
            assert result.reconstructed_fingerprints[key] == value, (
                f"{key} did not reconstruct to the same semantic state"
            )

    def test_the_portfolio_fingerprint_is_reconstructed_identically(self):
        _, original, result = self._rebuild()
        assert result.reconstructed_fingerprints["portfolio"] == (
            original["portfolio"]
        )

    def test_the_full_fingerprint_set_matches(self):
        _, original, result = self._rebuild()
        assert result.reconstructed_fingerprints == original

    def test_the_reconstruction_matches_what_was_published(self):
        _, _, result = self._rebuild()
        assert result.expected_fingerprints == (
            result.reconstructed_fingerprints
        )
        assert result.mismatches == []

    def test_the_rebuilt_state_is_published_as_current(self):
        persistence, _, result = self._rebuild()
        assert result.published is True
        assert persistence.state.get_current_revision(ORGANIZATION) is not None

    def test_current_decisions_are_reconstructed(self):
        persistence, _, _ = self._rebuild()
        outcome = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=__import__(
                "tests._persistence_fixtures", fromlist=["replay_context"],
            ).replay_context(),
        )
        decisions = {
            d["decision_id"] for d in outcome.historical_decisions
        }
        assert "dec-contract-standard" in decisions
        assert "decision-auto-rec-rollout" in decisions

    def test_the_recommendation_lineage_is_reconstructed(self):
        persistence, _, _ = self._rebuild()
        outcome = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=__import__(
                "tests._persistence_fixtures", fromlist=["replay_context"],
            ).replay_context(),
        )
        assert any(
            r["recommendation_id"] == "rec-contract-standard"
            and r["confidence"] == 0.84
            for r in outcome.historical_recommendations
        )

    def test_outcomes_are_reconstructed_and_linked(self):
        persistence, _, _ = self._rebuild()
        outcome = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=__import__(
                "tests._persistence_fixtures", fromlist=["replay_context"],
            ).replay_context(),
        )
        recorded = outcome.historical_outcomes[0]
        assert recorded["decision_id"] == "dec-contract-standard"
        assert recorded["kpi_ids"] == ["kpi-activation-rate"]
        assert recorded["milestone_ids"] == ["milestone-integration"]

    def test_the_rebuild_is_deterministic(self):
        _, _, first = self._rebuild()
        _, _, second = self._rebuild()
        assert first.reconstructed_fingerprints == (
            second.reconstructed_fingerprints
        )

    def test_equivalence_is_reported(self):
        _, _, result = self._rebuild()
        report = verify_replay_equivalence(result=result)
        assert report.equivalent is True
        assert report.changed_artifacts == []
        assert report.manifest_compatible is True


class TestReplayRanges:
    def test_an_empty_journal_reconstructs_empty_state(self):
        empty = Persistence()
        result = _replay(empty)
        assert result.complete is True
        assert result.records_replayed == 0
        assert result.reconstructed_fingerprints == {}

    def test_replay_can_start_from_a_sequence(self, live):
        persistence, _, _ = live
        result = _replay(
            persistence, from_global_sequence=3, use_snapshot=False,
        )
        assert result.start_sequence == 3
        assert result.complete is True

    def test_replay_can_stop_at_a_sequence(self, live):
        persistence, _, _ = live
        head = persistence.journal.latest_position().global_sequence
        result = _replay(
            persistence, to_global_sequence=head - 2, use_snapshot=False,
        )
        assert result.end_sequence == head - 2
        assert result.records_replayed < head

    def test_a_bounded_replay_is_still_complete(self, live):
        persistence, _, _ = live
        result = _replay(persistence, to_global_sequence=4, use_snapshot=False)
        assert result.complete is True

    def test_replay_without_a_snapshot_works(self, live):
        persistence, _, _ = live
        result = _replay(persistence, use_snapshot=False)
        assert result.snapshot_id is None
        assert result.start_sequence == 1
        assert result.complete is True


class TestSnapshotAcceleration:
    def test_replay_uses_a_compatible_snapshot(self):
        persistence, projection, _ = build_live_organization()
        head = persistence.journal.latest_position().global_sequence
        make_snapshot(
            persistence=persistence, projection=projection,
            journal_global_sequence=head - 2,
        )
        result = _replay(persistence)
        assert result.snapshot_id == "snap-1"
        assert result.start_sequence == head - 1

    def test_snapshot_plus_delta_equals_a_full_replay(self):
        persistence, projection, _ = build_live_organization()
        full = _replay(persistence, use_snapshot=False)

        head = persistence.journal.latest_position().global_sequence
        make_snapshot(
            persistence=persistence, projection=projection,
            journal_global_sequence=head - 3,
        )
        accelerated = _replay(persistence)

        assert accelerated.snapshot_id is not None
        assert accelerated.start_sequence > full.start_sequence
        assert accelerated.expected_fingerprints == full.expected_fingerprints

    def test_a_corrupt_snapshot_falls_back_to_an_older_one(self):
        persistence, projection, _ = build_live_organization()
        make_snapshot(
            persistence=persistence, projection=projection,
            snapshot_id="snap-old", journal_global_sequence=4,
        )
        make_snapshot(
            persistence=persistence, projection=projection,
            snapshot_id="snap-new", journal_global_sequence=8,
        )
        persistence.snapshots._corrupt_for_testing("snap-new")

        result = _replay(persistence)
        assert result.snapshot_id == "snap-old"
        assert any("integrity hash" in w for w in result.warnings)

    def test_all_snapshots_corrupt_falls_back_to_the_journal(self):
        persistence, projection, _ = build_live_organization()
        make_snapshot(persistence=persistence, projection=projection)
        persistence.snapshots._corrupt_for_testing("snap-1")
        result = _replay(persistence)
        assert result.snapshot_id is None
        assert result.start_sequence == 1
        assert result.complete is True

    def test_an_incompatible_snapshot_is_skipped(self):
        persistence, projection, _ = build_live_organization()
        make_snapshot(
            persistence=persistence, projection=projection,
            reproducibility=manifest(
                runtime_derivation_version="runtime-v29",
            ),
        )
        result = _replay(persistence)
        assert result.snapshot_id is None
        assert any("built by different code" in w for w in result.warnings)


class TestCorruptionStopsReplay:
    def test_a_tampered_journal_stops_reconstruction(self):
        persistence, _, _ = build_live_organization()
        persistence.journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"note": "tampered"},
        )
        result = _replay(persistence, use_snapshot=False)
        assert result.complete is False
        assert result.corruption_detected_at == 2

    def test_the_corruption_position_is_reported(self):
        persistence, _, _ = build_live_organization()
        persistence.journal._corrupt_payload_for_testing(
            global_sequence=3, payload={"note": "tampered"},
        )
        result = _replay(persistence, use_snapshot=False)
        assert result.corruption_detected_at == 3
        assert result.mismatches

    def test_replay_refuses_to_continue_silently(self):
        persistence, _, _ = build_live_organization()
        persistence.journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"note": "tampered"},
        )
        result = _replay(persistence, use_snapshot=False)
        assert "not silently repaired" in " ".join(result.warnings)
        assert result.reconstructed_fingerprints == {}

    def test_a_truncated_journal_is_reported_as_incomplete(self):
        persistence, _, _ = build_live_organization()
        head = persistence.journal.latest_position().global_sequence
        persistence.journal._truncate_for_testing(
            keep_through_sequence=head - 2,
        )
        result = _replay(
            persistence, use_snapshot=False, to_global_sequence=head,
        )
        assert result.complete is False
        assert "incomplete" in " ".join(result.mismatches)


class TestUnknownEvents:
    def test_a_record_from_newer_code_fails_loudly_on_read(self, tmp_path):
        # Written by a future build that journals a transition this one
        # has never heard of. Skipping it would reconstruct a different
        # organization, so reading it must refuse.
        from tpllm.persistence import SQLitePersistenceConfig
        from tpllm.persistence.sqlite import initialize_sqlite_persistence

        db = initialize_sqlite_persistence(
            SQLitePersistenceConfig(path=str(tmp_path / "future.db"))
        )
        db.journal.append(record=record(source_key="a"))
        db.connection.execute(
            "UPDATE journal_records SET event_type = ? "
            "WHERE global_sequence = 1",
            ("organizational_merger_completed",),
        )
        with pytest.raises(
            UnsupportedJournalEventError, match="unknown to this build",
        ):
            db.journal.read_from(global_sequence=1)
        db.close()

    def test_the_refusal_explains_the_risk(self, tmp_path):
        from tpllm.persistence import SQLitePersistenceConfig
        from tpllm.persistence.sqlite import initialize_sqlite_persistence

        db = initialize_sqlite_persistence(
            SQLitePersistenceConfig(path=str(tmp_path / "future.db"))
        )
        db.journal.append(record=record(source_key="a"))
        db.connection.execute(
            "UPDATE journal_records SET event_type = ? "
            "WHERE global_sequence = 1",
            ("some_future_event",),
        )
        with pytest.raises(
            UnsupportedJournalEventError,
            match="reconstruct a different organization",
        ):
            db.journal.get(db.journal.read_from(global_sequence=1)[0].id)
        db.close()


class TestManifestReporting:
    def test_a_matching_manifest_is_compatible(self, live):
        persistence, _, _ = live
        result = _replay(persistence, use_snapshot=False)
        assert result.manifest_compatible is True

    def test_a_changed_engine_is_reported_not_hidden(self):
        persistence, projection, _ = build_live_organization()
        from tpllm.runtime import append_artifact_published

        append_artifact_published(
            journal=persistence.journal,
            organization_id=ORGANIZATION,
            artifact_type="scenario_lattice",
            artifact_id="s1",
            revision_id="revision-2",
            fingerprint="fp",
            manifest=manifest(scenario_method_version="heuristic_v1"),
            occurred_at=at(80), recorded_at=at(80),
        )
        result = replay_journal(
            request=ReplayRequest(
                organization_id=ORGANIZATION,
                mode=ReplayMode.VERIFY_EQUIVALENCE,
                use_snapshot=False,
                expected_manifest=manifest(
                    scenario_method_version="learned_v2",
                ),
            ),
            journal=persistence.journal,
            snapshots=persistence.snapshots,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            reconstructor=reconstructor(),
        )
        assert result.manifest_compatible is False
        assert any(
            "reproduces the inputs, not the conclusion" in w
            for w in result.warnings
        )


class TestReplaySafetyDefaults:
    def test_replay_does_not_publish_unless_asked(self):
        persistence, _, _ = build_live_organization()
        before = persistence.state.get_current_revision(ORGANIZATION)
        result = _replay(persistence, use_snapshot=False)
        assert result.published is False
        assert persistence.state.get_current_revision(ORGANIZATION) == before

    def test_publishing_is_opt_in(self):
        persistence, _, _ = build_live_organization()
        persistence.delete_all_derived_state()
        result = _replay(
            persistence, use_snapshot=False, publish_result=True,
        )
        assert result.published is True

    def test_reevaluation_never_overwrites_history(self):
        persistence, _, _ = build_live_organization()
        original = persistence.artifacts.get(
            "project_runtime_state", PROJECT_A,
        )
        result = _replay(
            persistence,
            mode=ReplayMode.REEVALUATE_WITH_CURRENT,
            use_snapshot=False,
        )
        assert result.published is False
        after = persistence.artifacts.get("project_runtime_state", PROJECT_A)
        assert after.fingerprint == original.fingerprint, (
            "re-evaluating under current code answers a different question "
            "and must not overwrite what was actually published"
        )


class TestEvidenceLimitations:
    def test_a_deleted_source_is_reported_honestly(self):
        persistence, _, _ = build_live_organization()
        mark_source_deleted(persistence=persistence, evidence_id="env-abc")
        result = _replay(persistence, use_snapshot=False)
        assert any(
            "cannot be retrieved" in limitation
            for limitation in result.evidence_limitations
        )

    def test_the_original_evidence_record_is_not_mutated(self):
        persistence, _, _ = build_live_organization()
        before = len(persistence.journal)
        mark_source_deleted(persistence=persistence, evidence_id="env-abc")
        assert len(persistence.journal) == before + 1, (
            "a source going away is a new record, never an edit to the old "
            "one"
        )

    def test_a_deleted_source_does_not_break_replay(self):
        persistence, _, _ = build_live_organization()
        mark_source_deleted(persistence=persistence, evidence_id="env-abc")
        result = _replay(persistence, use_snapshot=False)
        assert result.complete is True


class TestHistoricalQueries:
    def test_the_original_recommendation_is_recoverable(self, live):
        persistence, _, _ = live
        found = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.AGENT_RECOMMENDATION_PUBLISHED,
        )
        assert found
        assert found[0].payload["confidence"] == 0.84
        assert found[0].payload["state_fingerprint"] == "ctx-fp-august"

    def test_the_original_method_version_is_recoverable(self, live):
        persistence, _, _ = live
        found = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.AGENT_RECOMMENDATION_PUBLISHED,
        )
        assert found[0].payload["method_version"] == "evidence-weighted-v21"

    def test_a_reopened_decision_is_a_separate_record(self, live):
        persistence, _, _ = live
        recorded = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.ORGANIZATIONAL_DECISION_RECORDED,
        )
        reopened = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.ORGANIZATIONAL_DECISION_REOPENED,
        )
        assert recorded and reopened
        assert recorded[0].payload["decision_id"] == (
            reopened[0].payload["decision_id"]
        )
        assert recorded[0].occurred_at < reopened[0].occurred_at

    def test_the_human_origin_is_preserved(self, live):
        persistence, _, _ = live
        recorded = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.ORGANIZATIONAL_DECISION_RECORDED,
        )
        assert recorded[0].payload["origin"] == (
            "human_modified_agent_recommendation"
        )

    def test_the_agent_decision_is_distinguishable_from_the_human_one(
        self, live,
    ):
        persistence, _, _ = live
        autonomous = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.AUTONOMOUS_DECISION_FINALIZED,
        )
        human = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.ORGANIZATIONAL_DECISION_RECORDED,
        )
        assert autonomous[0].payload["decision_id"] != (
            human[0].payload["decision_id"]
        )
        assert autonomous[0].payload["authority_policy_id"]


class TestPolicyHistory:
    def test_policy_history_is_preserved(self, live):
        persistence, _, _ = live
        found = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.AUTHORITY_POLICY_CHANGED,
        )
        assert found
        assert found[0].payload["changed_fields"]
        assert found[0].payload["actor_id"] == "person_priya_rao"

    def test_kill_switch_history_is_preserved(self):
        persistence, _, _ = build_live_organization()
        engage_kill_switch(persistence=persistence, engaged=True, minutes=80)
        engage_kill_switch(persistence=persistence, engaged=False, minutes=90)
        found = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.AUTONOMY_KILL_SWITCH_CHANGED,
        )
        assert [r.payload["engaged"] for r in found] == [True, False]
        assert found[0].payload["reason"] == "incident response"
        assert found[0].payload["actor_id"]

    def test_replay_restores_the_current_kill_switch_state(self):
        persistence, _, _ = build_live_organization()
        engage_kill_switch(persistence=persistence, engaged=True, minutes=80)
        engage_kill_switch(persistence=persistence, engaged=False, minutes=90)
        outcome = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=__import__(
                "tests._persistence_fixtures", fromlist=["replay_context"],
            ).replay_context(),
        )
        assert outcome.kill_switch_engaged is False

    def test_replay_restores_the_current_policy_fingerprint(self, live):
        persistence, _, _ = live
        outcome = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=__import__(
                "tests._persistence_fixtures", fromlist=["replay_context"],
            ).replay_context(),
        )
        assert outcome.current_policy_fingerprint == "policy-fp-v1"

    def test_replay_cannot_widen_authority(self):
        # Reconstruction reads policy history; it has no path that grants
        # authority the journal does not record.
        import ast
        import inspect

        import tpllm.runtime.durability as durability

        source = inspect.getsource(durability)
        tree = ast.parse(source)
        assignments = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in (
                "autonomous_eligible", "maximum_autonomous_risk",
                "minimum_autonomous_confidence",
            )
            and isinstance(node.ctx, ast.Store)
        ]
        assert assignments == [], (
            "replay must never grant the agent authority; only a journalled "
            "policy change can"
        )


class TestHealth:
    def test_a_healthy_store_reports_healthy(self, live):
        persistence, _, _ = live
        report = inspect_persistence_health(
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            snapshots=persistence.snapshots,
            state_store=persistence.state,
            manifest=manifest(),
        )
        assert report.journal_hash_valid is True
        assert report.journal_head_sequence > 0
        assert report.current_revision_id

    def test_corruption_appears_in_health(self):
        persistence, _, _ = build_live_organization()
        persistence.journal._corrupt_payload_for_testing(
            global_sequence=2, payload={"note": "tampered"},
        )
        report = inspect_persistence_health(
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
        )
        assert report.journal_hash_valid is False
        assert report.warnings
        assert report.healthy is False

    def test_missing_projections_appear_in_health(self):
        persistence, _, _ = build_live_organization()
        persistence.delete_all_derived_state()
        report = inspect_persistence_health(
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
        )
        assert report.current_revision_id is None
        assert any("rebuilding" in w for w in report.warnings)

    def test_a_corrupt_snapshot_appears_in_health(self):
        persistence, projection, _ = build_live_organization()
        make_snapshot(persistence=persistence, projection=projection)
        persistence.snapshots._corrupt_for_testing("snap-1")
        report = inspect_persistence_health(
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            snapshots=persistence.snapshots,
            state_store=persistence.state,
            manifest=manifest(),
        )
        assert report.latest_snapshot_valid is False
        assert report.latest_snapshot_id == "snap-1"

    def test_an_empty_store_is_healthy(self):
        report = inspect_persistence_health(
            organization_id=ORGANIZATION,
            journal=InMemoryEventJournalStore(),
        )
        assert report.journal_head_sequence == 0
        assert report.healthy is True
