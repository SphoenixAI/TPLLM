"""Snapshots as accelerators, never as truth.

A snapshot that fails its hash is discarded rather than used carefully.
Losing an accelerator costs time; trusting a corrupt one costs
correctness, and the journal can always rebuild what the snapshot
claimed to hold.
"""

from __future__ import annotations

import pytest

from tpllm.persistence import (
    InMemorySnapshotStore,
    SQLitePersistenceConfig,
    build_runtime_snapshot,
    calculate_snapshot_hash,
    snapshot_is_usable,
    snapshot_state_fingerprints,
    snapshots_are_compatible,
    verify_snapshot_integrity,
)
from tpllm.persistence.sqlite import initialize_sqlite_persistence
from tests._persistence_fixtures import (
    ORGANIZATION,
    PROJECT_A,
    at,
    manifest,
)


def _snapshot(
    snapshot_id: str = "snap-1",
    *,
    sequence: int = 100,
    reproducibility=None,
    **overrides,
):
    defaults = dict(
        snapshot_id=snapshot_id,
        organization_id=ORGANIZATION,
        journal_global_sequence=sequence,
        active_revision_id="revision-1",
        project_states={PROJECT_A: {"project_id": PROJECT_A}},
        portfolio_state={"bottleneck_ids": ["b1"]},
        state_fingerprints={f"project:{PROJECT_A}": "fp-a"},
        manifest=reproducibility or manifest(),
        created_at=at(60),
    )
    defaults.update(overrides)
    return build_runtime_snapshot(**defaults)


@pytest.fixture(params=["memory", "sqlite"])
def snapshots(request, tmp_path):
    if request.param == "memory":
        yield InMemorySnapshotStore()
    else:
        db = initialize_sqlite_persistence(
            SQLitePersistenceConfig(path=str(tmp_path / "snap.db"))
        )
        yield db.snapshots
        db.close()


class TestCreation:
    def test_a_snapshot_is_stamped_with_its_hash(self):
        snapshot = _snapshot()
        assert snapshot.snapshot_hash
        assert verify_snapshot_integrity(snapshot) is True

    def test_the_hash_is_deterministic(self):
        assert calculate_snapshot_hash(_snapshot()) == calculate_snapshot_hash(
            _snapshot()
        )

    def test_the_hash_covers_the_content(self):
        assert calculate_snapshot_hash(
            _snapshot(),
        ) != calculate_snapshot_hash(
            _snapshot(state_fingerprints={"project:project-a": "different"})
        )

    def test_a_snapshot_records_its_journal_position(self):
        assert _snapshot(sequence=750).journal_global_sequence == 750

    def test_a_negative_position_is_refused(self):
        with pytest.raises(ValueError, match="before the start"):
            _snapshot(sequence=-1)

    def test_fingerprints_are_readable(self):
        assert snapshot_state_fingerprints(_snapshot()) == {
            "project:project-a": "fp-a",
        }


class TestIntegrity:
    def test_an_unstamped_snapshot_fails(self):
        snapshot = _snapshot().model_copy(update={"snapshot_hash": ""})
        assert verify_snapshot_integrity(snapshot) is False

    def test_a_tampered_snapshot_fails(self):
        snapshot = _snapshot().model_copy(update={
            "active_revision_id": "revision-99",
        })
        assert verify_snapshot_integrity(snapshot) is False

    def test_a_corrupt_snapshot_is_not_usable(self):
        snapshot = _snapshot().model_copy(update={
            "state_fingerprints": {"tampered": "yes"},
        })
        usable, reason = snapshot_is_usable(
            snapshot=snapshot, manifest=manifest(),
        )
        assert usable is False
        assert "failed its integrity hash" in reason
        assert "journal can rebuild" in reason

    def test_a_valid_compatible_snapshot_is_usable(self):
        usable, reason = snapshot_is_usable(
            snapshot=_snapshot(), manifest=manifest(),
        )
        assert usable is True
        assert "valid and compatible" in reason


class TestManifestCompatibility:
    def test_an_identical_manifest_is_compatible(self):
        assert snapshots_are_compatible(
            snapshot=_snapshot(), manifest=manifest(),
        ) is True

    def test_a_different_derivation_version_is_incompatible(self):
        assert snapshots_are_compatible(
            snapshot=_snapshot(),
            manifest=manifest(runtime_derivation_version="runtime-v31"),
        ) is False

    def test_a_different_scenario_method_is_incompatible(self):
        assert snapshots_are_compatible(
            snapshot=_snapshot(),
            manifest=manifest(scenario_method_version="learned_v2"),
        ) is False

    def test_a_different_policy_fingerprint_is_incompatible(self):
        assert snapshots_are_compatible(
            snapshot=_snapshot(),
            manifest=manifest(policy_fingerprint="policy-fp-v2"),
        ) is False

    def test_an_incompatible_snapshot_is_skipped_with_a_reason(self):
        usable, reason = snapshot_is_usable(
            snapshot=_snapshot(),
            manifest=manifest(runtime_derivation_version="runtime-v31"),
        )
        assert usable is False
        assert "built by different code" in reason
        assert "runtime_derivation_version" in reason

    def test_no_expected_manifest_accepts_anything_intact(self):
        usable, _ = snapshot_is_usable(snapshot=_snapshot(), manifest=None)
        assert usable is True

    def test_a_version_bump_alone_does_not_break_compatibility(self):
        # The library version is metadata; the derivation identity is
        # what decides whether the state would be reproduced.
        assert snapshots_are_compatible(
            snapshot=_snapshot(), manifest=manifest(tpllm_version="0.99.0"),
        ) is True


class TestStore:
    def test_a_snapshot_persists(self, snapshots):
        snapshots.put_snapshot(_snapshot())
        assert len(snapshots.list_snapshots(ORGANIZATION)) == 1

    def test_snapshots_list_newest_first(self, snapshots):
        snapshots.put_snapshot(_snapshot("snap-1", sequence=100))
        snapshots.put_snapshot(_snapshot("snap-2", sequence=750))
        listed = snapshots.list_snapshots(ORGANIZATION)
        assert [s.id for s in listed] == ["snap-2", "snap-1"]

    def test_the_latest_compatible_snapshot_is_returned(self, snapshots):
        snapshots.put_snapshot(_snapshot("snap-1", sequence=100))
        snapshots.put_snapshot(_snapshot("snap-2", sequence=750))
        found = snapshots.latest_compatible_snapshot(
            organization_id=ORGANIZATION, manifest=manifest(),
        )
        assert found.id == "snap-2"

    def test_a_corrupt_newest_falls_back_to_an_older_valid_one(
        self, snapshots,
    ):
        snapshots.put_snapshot(_snapshot("snap-old", sequence=4500))
        snapshots.put_snapshot(_snapshot("snap-new", sequence=5000))
        snapshots._corrupt_for_testing("snap-new")
        found = snapshots.latest_compatible_snapshot(
            organization_id=ORGANIZATION, manifest=manifest(),
        )
        assert found.id == "snap-old", (
            "an older valid checkpoint still saves most of the replay; only "
            "the corrupt one is discarded"
        )

    def test_all_corrupt_snapshots_yield_none(self, snapshots):
        snapshots.put_snapshot(_snapshot("snap-1", sequence=100))
        snapshots._corrupt_for_testing("snap-1")
        assert snapshots.latest_compatible_snapshot(
            organization_id=ORGANIZATION, manifest=manifest(),
        ) is None

    def test_an_incompatible_snapshot_is_skipped(self, snapshots):
        snapshots.put_snapshot(_snapshot("snap-1", sequence=100))
        assert snapshots.latest_compatible_snapshot(
            organization_id=ORGANIZATION,
            manifest=manifest(runtime_derivation_version="runtime-v31"),
        ) is None

    def test_snapshots_from_other_organizations_are_not_returned(
        self, snapshots,
    ):
        snapshots.put_snapshot(
            _snapshot("snap-other", organization_id="other-org")
        )
        assert snapshots.list_snapshots(ORGANIZATION) == []

    def test_an_empty_store_returns_none(self, snapshots):
        assert snapshots.latest_compatible_snapshot(
            organization_id=ORGANIZATION, manifest=manifest(),
        ) is None

    def test_a_snapshot_round_trips_intact(self, snapshots):
        snapshots.put_snapshot(_snapshot())
        found = snapshots.list_snapshots(ORGANIZATION)[0]
        assert verify_snapshot_integrity(found) is True
        assert found.project_states == {PROJECT_A: {"project_id": PROJECT_A}}
        assert found.portfolio_state == {"bottleneck_ids": ["b1"]}

    def test_clearing_removes_everything(self, snapshots):
        snapshots.put_snapshot(_snapshot())
        snapshots.clear()
        assert snapshots.list_snapshots(ORGANIZATION) == []


class TestRejectionReporting:
    def test_rejected_snapshots_are_reported_with_reasons(self):
        store = InMemorySnapshotStore()
        store.put_snapshot(_snapshot("snap-old", sequence=4500))
        store.put_snapshot(_snapshot("snap-new", sequence=5000))
        store._corrupt_for_testing("snap-new")
        rejected = store.rejected_snapshots(
            organization_id=ORGANIZATION, manifest=manifest(),
        )
        assert [snapshot_id for snapshot_id, _ in rejected] == ["snap-new"]
        assert "integrity" in rejected[0][1]

    def test_nothing_is_rejected_when_the_newest_is_fine(self):
        store = InMemorySnapshotStore()
        store.put_snapshot(_snapshot())
        assert store.rejected_snapshots(
            organization_id=ORGANIZATION, manifest=manifest(),
        ) == []


class TestSnapshotBaselineSeedsReconstruction:
    """A fix to snapshot behaviour.

    ``RuntimeJournalReconstructor.apply_records`` ignored the snapshot
    baseline entirely and always started from an empty projection. A
    snapshot covering the whole journal therefore produced an empty
    reconstruction and reported every project as "published but not
    reconstructed" — when the snapshot held their state all along.

    A snapshot is an accelerator, so starting from one must give the same
    answer as replaying from record one.
    """

    def _baseline(self):
        return {
            "active_revision_id": "revision-7",
            "project_states": {
                PROJECT_A: {
                    "project_id": PROJECT_A,
                    "state_fingerprint": "fp-a",
                    "kpi_state_ids": ["kpi-1"],
                },
            },
            "portfolio_state": {
                "state_fingerprint": "fp-portfolio",
                "bottleneck_ids": ["b1"],
            },
            "state_fingerprints": {f"project:{PROJECT_A}": "fp-a"},
        }

    def test_an_empty_delta_returns_the_baseline_state(self):
        from tests._persistence_fixtures import replay_context
        from tests._persistence_fixtures import reconstructor

        outcome = reconstructor().apply_records(
            records=[],
            organization_id=ORGANIZATION,
            context=replay_context(),
            baseline=self._baseline(),
        )
        assert outcome.revision_id == "revision-7"
        assert PROJECT_A in outcome.project_states
        assert outcome.state_fingerprints[
            f"project:{PROJECT_A}"
        ] == "fp-a", (
            "the snapshot held this state; a replay starting from it must "
            "not report the project as missing"
        )

    def test_the_portfolio_state_survives_too(self):
        from tests._persistence_fixtures import reconstructor, replay_context

        outcome = reconstructor().apply_records(
            records=[],
            organization_id=ORGANIZATION,
            context=replay_context(),
            baseline=self._baseline(),
        )
        assert outcome.portfolio_state is not None
        assert outcome.state_fingerprints["portfolio"] == "fp-portfolio"

    def test_no_baseline_still_starts_from_nothing(self):
        from tests._persistence_fixtures import reconstructor, replay_context

        outcome = reconstructor().apply_records(
            records=[],
            organization_id=ORGANIZATION,
            context=replay_context(),
            baseline=None,
        )
        assert outcome.project_states == {}
        assert outcome.revision_id is None

    def test_a_malformed_baseline_entry_is_skipped_not_fatal(self):
        from tests._persistence_fixtures import reconstructor, replay_context

        baseline = self._baseline()
        baseline["project_states"]["broken"] = {"not": "a project state"}
        outcome = reconstructor().apply_records(
            records=[],
            organization_id=ORGANIZATION,
            context=replay_context(),
            baseline=baseline,
        )
        assert PROJECT_A in outcome.project_states
        assert "broken" not in outcome.project_states
