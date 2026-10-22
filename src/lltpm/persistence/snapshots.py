"""Runtime snapshots.

Replaying from record one is correct and, eventually, slow. Snapshots
fix that without changing what is authoritative: a snapshot is a
checkpoint of a projection, and the journal can always rebuild what it
claims to hold.

Two consequences follow, and both are enforced here rather than left to
discipline.

A snapshot that fails its hash is discarded — not repaired, not used
"carefully". Replay falls back to the next older valid one, and if there
is none, to the journal from the beginning. Losing an accelerator costs
time; trusting a corrupt one costs correctness.

A snapshot whose manifest disagrees with the expected derivation is
skipped for the same reason. It was built by different code, so the
state inside it is not the state this run would produce.
"""

from __future__ import annotations

import threading
from datetime import datetime

from lltpm.persistence.integrity import (
    stamp_snapshot_hash,
    verify_snapshot_integrity,
)
from lltpm.persistence.models import (
    ReproducibilityManifest,
    RuntimeSnapshot,
)
from lltpm.persistence.versioning import (
    SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS,
    manifest_incompatibilities,
    manifests_compatible,
)


def build_runtime_snapshot(
    *,
    snapshot_id: str,
    organization_id: str,
    journal_global_sequence: int,
    active_revision_id: str,
    project_states: dict[str, dict],
    portfolio_state: dict | None,
    state_fingerprints: dict[str, str],
    manifest: ReproducibilityManifest,
    created_at: datetime,
) -> RuntimeSnapshot:
    """Build a snapshot with its integrity hash already stamped."""
    return stamp_snapshot_hash(
        RuntimeSnapshot(
            id=snapshot_id,
            organization_id=organization_id,
            journal_global_sequence=journal_global_sequence,
            active_revision_id=active_revision_id,
            project_states=project_states,
            portfolio_state=portfolio_state,
            state_fingerprints=state_fingerprints,
            manifest=manifest,
            created_at=created_at,
        )
    )


def snapshot_is_usable(
    *,
    snapshot: RuntimeSnapshot,
    manifest: ReproducibilityManifest | None,
) -> tuple[bool, str]:
    """Whether this snapshot may be used, and why not if it may not."""
    if snapshot.manifest.snapshot_schema_version not in (
        SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS
    ):
        return False, (
            f"Snapshot {snapshot.id} uses schema version "
            f"{snapshot.manifest.snapshot_schema_version}, which this build "
            f"does not read."
        )
    if not verify_snapshot_integrity(snapshot):
        return False, (
            f"Snapshot {snapshot.id} failed its integrity hash and was "
            f"discarded; the journal can rebuild what it held."
        )
    incompatibilities = manifest_incompatibilities(
        expected=manifest, actual=snapshot.manifest,
    )
    if incompatibilities:
        return False, (
            f"Snapshot {snapshot.id} was built by different code "
            f"({'; '.join(incompatibilities)}); its state is not what this "
            f"build would produce."
        )
    return True, f"Snapshot {snapshot.id} is valid and compatible."


class InMemorySnapshotStore:
    """Reference snapshot store, newest-first by journal position."""

    def __init__(self) -> None:
        self._snapshots: dict[str, RuntimeSnapshot] = {}
        self._lock = threading.Lock()

    def put_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        with self._lock:
            self._snapshots[snapshot.id] = snapshot.model_copy(deep=True)

    def get_snapshot(self, snapshot_id: str) -> RuntimeSnapshot | None:
        found = self._snapshots.get(snapshot_id)
        return found.model_copy(deep=True) if found else None

    def list_snapshots(self, organization_id: str) -> list[RuntimeSnapshot]:
        """Newest journal position first — the order fallback walks."""
        return [
            s.model_copy(deep=True)
            for s in sorted(
                (
                    s for s in self._snapshots.values()
                    if s.organization_id == organization_id
                ),
                key=lambda s: (-s.journal_global_sequence, s.id),
            )
        ]

    def latest_compatible_snapshot(
        self,
        *,
        organization_id: str,
        manifest: ReproducibilityManifest | None,
    ) -> RuntimeSnapshot | None:
        """The newest snapshot that is both intact and compatible.

        Walks backwards through history rather than failing on the first
        corrupt one: an older valid checkpoint still saves most of the
        replay.
        """
        for snapshot in self.list_snapshots(organization_id):
            usable, _ = snapshot_is_usable(
                snapshot=snapshot, manifest=manifest,
            )
            if usable:
                return snapshot
        return None

    def rejected_snapshots(
        self,
        *,
        organization_id: str,
        manifest: ReproducibilityManifest | None,
    ) -> list[tuple[str, str]]:
        """Every snapshot that was skipped, with the reason.

        Silently ignoring a corrupt checkpoint would hide the corruption.
        """
        rejected: list[tuple[str, str]] = []
        for snapshot in self.list_snapshots(organization_id):
            usable, reason = snapshot_is_usable(
                snapshot=snapshot, manifest=manifest,
            )
            if usable:
                break
            rejected.append((snapshot.id, reason))
        return rejected

    def clear(self) -> None:
        with self._lock:
            self._snapshots = {}

    def _corrupt_for_testing(self, snapshot_id: str) -> None:
        """Simulate a damaged checkpoint on disk."""
        snapshot = self._snapshots[snapshot_id]
        self._snapshots[snapshot_id] = snapshot.model_copy(update={
            "state_fingerprints": {
                **snapshot.state_fingerprints, "tampered": "yes",
            },
        })

    def __len__(self) -> int:
        return len(self._snapshots)


def snapshot_state_fingerprints(snapshot: RuntimeSnapshot) -> dict[str, str]:
    return dict(snapshot.state_fingerprints)


def snapshots_are_compatible(
    *, snapshot: RuntimeSnapshot, manifest: ReproducibilityManifest | None,
) -> bool:
    return manifests_compatible(expected=manifest, actual=snapshot.manifest)
