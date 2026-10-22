"""Persistence ports.

Every storage concern in LLTPM sits behind one of these. The reference
implementation is SQLite and the in-memory stores used by tests, but
nothing above this layer knows that — which is what keeps a local
open-source deployment (one file, no cloud account) and a future hosted
backend from being different codebases.

Note on naming: ``RuntimeStateStore`` here is the storage *port*.
``lltpm.runtime.RuntimeStateStore`` is the in-memory projection object
the runtime passes through an orchestration pass. Different layers,
different jobs; the runtime adapts between them at its own seam.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from lltpm.persistence.models import (
    DurableOutboundIntent,
    IntelligenceArtifactRecord,
    JournalEventType,
    JournalPosition,
    JournalRecord,
    PortfolioRuntimeStateRecord,
    ProjectRuntimeStateRecord,
    ReproducibilityManifest,
    RuntimeRevisionRecord,
    RuntimeSnapshot,
)


class PersistenceError(RuntimeError):
    """Base for durable-storage failures."""


class JournalConcurrencyError(PersistenceError):
    """Two writers raced on one stream and one of them must retry.

    Failing is the whole point: the loser re-reads and decides again,
    rather than overwriting a decision it never saw.
    """


class JournalIdempotencyError(PersistenceError):
    """An idempotency key was reused with different content."""


class UnsupportedJournalEventError(PersistenceError):
    """Older code met a journal event it does not understand.

    Failing loudly is mandatory. Skipping an unknown organizational
    mutation would reconstruct a different organization and call it the
    same one.
    """


class PersistenceSchemaError(PersistenceError):
    """The stored schema version is not one this code can safely open."""


class RevisionPromotionError(PersistenceError):
    """A revision promotion did not meet its precondition."""


class SnapshotIntegrityError(PersistenceError):
    """A snapshot failed verification and was discarded."""


@runtime_checkable
class EventJournalStore(Protocol):
    """Append-only durable history."""

    def append(
        self,
        *,
        record: JournalRecord,
        expected_stream_sequence: int | None = None,
    ) -> JournalRecord:
        """Commit one record atomically.

        Allocates the global and stream sequence, links the hash chain,
        and enforces idempotency. Either the whole record is durable or
        none of it is.
        """
        ...

    def get(self, record_id: str) -> JournalRecord | None:
        ...

    def read_from(
        self,
        *,
        global_sequence: int,
        limit: int | None = None,
    ) -> list[JournalRecord]:
        ...

    def read_stream(
        self,
        *,
        stream_id: str,
        after_sequence: int = 0,
    ) -> list[JournalRecord]:
        ...

    def find_by_idempotency_key(
        self, idempotency_key: str,
    ) -> JournalRecord | None:
        ...

    def latest_position(self) -> JournalPosition | None:
        ...


@runtime_checkable
class RuntimeStateStore(Protocol):
    """The rebuildable projection of current organizational state."""

    def get_current_revision(self, organization_id: str) -> str | None:
        ...

    def get_project_state(
        self, project_id: str,
    ) -> ProjectRuntimeStateRecord | None:
        ...

    def put_project_state(self, state: ProjectRuntimeStateRecord) -> None:
        ...

    def get_portfolio_state(
        self, organization_id: str,
    ) -> PortfolioRuntimeStateRecord | None:
        ...

    def put_portfolio_state(self, state: PortfolioRuntimeStateRecord) -> None:
        ...

    def put_revision(self, revision: RuntimeRevisionRecord) -> None:
        ...

    def get_revision(
        self, revision_id: str,
    ) -> RuntimeRevisionRecord | None:
        ...

    def promote_revision(
        self,
        *,
        organization_id: str,
        revision_id: str,
        expected_current_revision_id: str | None,
    ) -> None:
        """Move the current-revision pointer atomically.

        A crash before this leaves the previous revision authoritative; a
        crash after leaves the new one. There is no in-between state in
        which half a revision is current.
        """
        ...


@runtime_checkable
class IntelligenceArtifactStore(Protocol):
    """A cache of derived artifacts. Not the journal.

    Historically published agent output is kept here so that "what did
    LLTPM recommend on August 18?" stays answerable even after the
    engine improves.
    """

    def put(
        self,
        *,
        artifact_type: str,
        artifact_id: str,
        revision_id: str,
        payload: dict[str, Any],
        fingerprint: str,
        derivation_version: str | None = None,
        policy_fingerprint: str | None = None,
    ) -> None:
        ...

    def get(
        self, artifact_type: str, artifact_id: str,
    ) -> IntelligenceArtifactRecord | None:
        ...

    def get_revision_artifacts(
        self, revision_id: str,
    ) -> list[IntelligenceArtifactRecord]:
        ...


@runtime_checkable
class SnapshotStore(Protocol):
    def put_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        ...

    def latest_compatible_snapshot(
        self,
        *,
        organization_id: str,
        manifest: ReproducibilityManifest | None,
    ) -> RuntimeSnapshot | None:
        ...

    def list_snapshots(self, organization_id: str) -> list[RuntimeSnapshot]:
        ...


@runtime_checkable
class JournalPayloadMigrator(Protocol):
    """Upgrades old payloads on read.

    Migration happens as history is read, never by rewriting stored
    records during startup — a migration that edited history would
    destroy the thing it was migrating.
    """

    def migrate(
        self,
        *,
        event_type: JournalEventType,
        from_version: int,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        ...


@runtime_checkable
class OutboundPort(Protocol):
    """Anything that can touch the world outside LLTPM.

    Every implementation takes an execution context so a replay can be
    refused at the boundary rather than trusted not to happen.
    """

    def send(
        self,
        *,
        target_system: str,
        action_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        context: Any,
    ) -> DurableOutboundIntent:
        ...


@runtime_checkable
class RuntimeReconstructor(Protocol):
    """Applies journal records to rebuild runtime projections.

    Implemented in the runtime layer, injected here. Persistence knows
    how to read and verify history; it does not know how organizational
    intelligence is derived from it, and keeping that ignorance is what
    stops the dependency arrow from reversing.
    """

    def apply_records(
        self,
        *,
        records: list[JournalRecord],
        organization_id: str,
        context: Any,
        baseline: dict[str, Any] | None = None,
    ) -> Any:
        ...
