"""SQLite reference persistence.

The open-source default: one local file, standard-library `sqlite3`, no
cloud account, no broker, no daemon. A user backs LLTPM up by copying a
single database file.

Everything durable is stored as JSON text in TEXT columns rather than
normalized across a dozen tables. That is a deliberate trade: a person
investigating what their agent did should be able to open the file and
read it, and a future export tool should not need to reverse-engineer a
schema. Indexes cover the access paths that actually matter — global
sequence, stream position, idempotency key, event type, organization.

Atomicity is the one thing not traded away. An append validates the
stream sequence, checks idempotency, allocates the sequence, computes
the hash chain link, and inserts inside a single transaction. A crash
before commit leaves no record; a crash after leaves a durable one.
There is no half-record.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from typing import Any

from lltpm.persistence.integrity import (
    calculate_journal_record_hash,
    canonical_json_bytes,
    payloads_describe_same_fact,
)
from lltpm.persistence.models import (
    IntelligenceArtifactRecord,
    JournalPosition,
    JournalRecord,
    PortfolioRuntimeStateRecord,
    ProjectRuntimeStateRecord,
    ReproducibilityManifest,
    RuntimeRevisionRecord,
    RuntimeSnapshot,
    SQLitePersistenceConfig,
)
from lltpm.persistence.protocols import (
    JournalConcurrencyError,
    JournalIdempotencyError,
    RevisionPromotionError,
)
from lltpm.persistence.snapshots import snapshot_is_usable
from lltpm.persistence.versioning import (
    DATABASE_SCHEMA_VERSION,
    assert_supported_database_schema,
)

logger = logging.getLogger(__name__)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS persistence_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS journal_records (
        global_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id TEXT NOT NULL UNIQUE,
        stream_id TEXT NOT NULL,
        stream_sequence INTEGER NOT NULL,
        category TEXT NOT NULL,
        event_type TEXT NOT NULL,
        organization_id TEXT,
        project_ids TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        causal_event_id TEXT,
        idempotency_key TEXT NOT NULL UNIQUE,
        payload_schema_version INTEGER NOT NULL,
        payload TEXT NOT NULL,
        evidence_ids TEXT NOT NULL,
        previous_record_hash TEXT,
        record_hash TEXT NOT NULL,
        runtime_revision_id TEXT,
        UNIQUE (stream_id, stream_sequence)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS journal_stream_heads (
        stream_id TEXT PRIMARY KEY,
        stream_sequence INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_revisions (
        revision_id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL,
        complete INTEGER NOT NULL DEFAULT 0,
        is_current INTEGER NOT NULL DEFAULT 0,
        parent_revision_id TEXT,
        evidence_cutoff_at TEXT,
        created_at TEXT,
        state_fingerprint TEXT NOT NULL DEFAULT '',
        state_fingerprints TEXT NOT NULL DEFAULT '{}',
        payload TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project_runtime_state (
        project_id TEXT PRIMARY KEY,
        organization_id TEXT,
        revision_id TEXT,
        state_fingerprint TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_runtime_state (
        organization_id TEXT PRIMARY KEY,
        revision_id TEXT,
        state_fingerprint TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS intelligence_artifacts (
        artifact_type TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        revision_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        payload TEXT NOT NULL,
        derivation_version TEXT,
        policy_fingerprint TEXT,
        created_at TEXT,
        PRIMARY KEY (artifact_type, artifact_id, revision_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL,
        journal_global_sequence INTEGER NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_journal_stream "
    "ON journal_records (stream_id, stream_sequence)",
    "CREATE INDEX IF NOT EXISTS ix_journal_idempotency "
    "ON journal_records (idempotency_key)",
    "CREATE INDEX IF NOT EXISTS ix_journal_event_type "
    "ON journal_records (event_type)",
    "CREATE INDEX IF NOT EXISTS ix_journal_organization "
    "ON journal_records (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_journal_recorded_at "
    "ON journal_records (recorded_at)",
    "CREATE INDEX IF NOT EXISTS ix_revisions_org "
    "ON runtime_revisions (organization_id, is_current)",
    "CREATE INDEX IF NOT EXISTS ix_artifacts_revision "
    "ON intelligence_artifacts (revision_id)",
    "CREATE INDEX IF NOT EXISTS ix_snapshots_org "
    "ON runtime_snapshots (organization_id, journal_global_sequence)",
)


def _dumps(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SQLitePersistence:
    """One connection, one file, all four stores.

    Keeping them on one connection is what makes a revision promotion
    and its artifact writes share a transaction boundary — and what
    keeps the backup story to "copy this file".
    """

    def __init__(self, config: SQLitePersistenceConfig | None = None):
        self.config = config or SQLitePersistenceConfig()
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.config.path,
            timeout=self.config.timeout_seconds,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._configure()
        self._create_schema()

        self.journal = SQLiteEventJournalStore(self)
        self.state = SQLiteRuntimeStateStore(self)
        self.artifacts = SQLiteIntelligenceArtifactStore(self)
        self.snapshots = SQLiteSnapshotStore(self)

    # ── Connection setup ─────────────────────────────────────────────

    def _configure(self) -> None:
        cursor = self._connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if self.config.enable_wal and not self.config.is_memory:
            # WAL survives a crash and lets a reader run during a write.
            # It is unavailable for :memory:, which is fine — that path
            # is for tests.
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA synchronous={self.config.synchronous}")

    def _create_schema(self) -> None:
        with self._lock:
            cursor = self._connection.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                for statement in SCHEMA_STATEMENTS:
                    cursor.execute(statement)
                row = cursor.execute(
                    "SELECT value FROM persistence_metadata WHERE key = ?",
                    ("database_schema_version",),
                ).fetchone()
                if row is None:
                    cursor.execute(
                        "INSERT INTO persistence_metadata (key, value) "
                        "VALUES (?, ?)",
                        ("database_schema_version", str(DATABASE_SCHEMA_VERSION)),
                    )
                else:
                    assert_supported_database_schema(int(row["value"]))
                cursor.execute("COMMIT")
            except Exception:
                cursor.execute("ROLLBACK")
                raise

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def database_schema_version(self) -> int:
        row = self._connection.execute(
            "SELECT value FROM persistence_metadata WHERE key = ?",
            ("database_schema_version",),
        ).fetchone()
        return int(row["value"]) if row else 0

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SQLitePersistence":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def initialize_sqlite_persistence(
    config: SQLitePersistenceConfig | None = None,
) -> SQLitePersistence:
    """Open or create the database. Idempotent.

    Running it twice against the same file is a no-op, not damage — the
    common case is a process restarting.
    """
    return SQLitePersistence(config)


class SQLiteEventJournalStore:
    """Append-only journal on SQLite."""

    def __init__(self, persistence: SQLitePersistence):
        self._db = persistence

    def append(
        self,
        *,
        record: JournalRecord,
        expected_stream_sequence: int | None = None,
    ) -> JournalRecord:
        """One transaction: validate, allocate, hash, insert, commit."""
        with self._db.lock:
            cursor = self._db.connection.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                existing = cursor.execute(
                    "SELECT * FROM journal_records WHERE idempotency_key = ?",
                    (record.idempotency_key,),
                ).fetchone()
                if existing is not None:
                    committed = _row_to_record(existing)
                    if committed.event_type != record.event_type or not (
                        payloads_describe_same_fact(
                            committed.payload, record.payload,
                        )
                    ):
                        cursor.execute("ROLLBACK")
                        raise JournalIdempotencyError(
                            f"Idempotency key '{record.idempotency_key}' was "
                            f"reused with different content; two different "
                            f"facts cannot share one identity."
                        )
                    cursor.execute("COMMIT")
                    logger.debug(
                        "journal append deduplicated",
                        extra={
                            "journal_event_type": record.event_type.value,
                            "idempotency_key": record.idempotency_key,
                            "journal_sequence": (
                                committed.position.global_sequence
                            ),
                            "record_id": committed.id,
                        },
                    )
                    return committed

                stream_id = record.position.stream_id
                head_row = cursor.execute(
                    "SELECT stream_sequence FROM journal_stream_heads "
                    "WHERE stream_id = ?",
                    (stream_id,),
                ).fetchone()
                current_head = head_row["stream_sequence"] if head_row else 0
                if (
                    expected_stream_sequence is not None
                    and expected_stream_sequence != current_head
                ):
                    cursor.execute("ROLLBACK")
                    raise JournalConcurrencyError(
                        f"Stream '{stream_id}' is at sequence "
                        f"{current_head}, not {expected_stream_sequence}. "
                        f"Another writer committed first; re-read and decide "
                        f"again rather than overwriting a change you never "
                        f"saw."
                    )

                tail = cursor.execute(
                    "SELECT record_hash, global_sequence FROM journal_records "
                    "ORDER BY global_sequence DESC LIMIT 1"
                ).fetchone()
                previous_hash = tail["record_hash"] if tail else None
                next_global = (tail["global_sequence"] + 1) if tail else 1
                next_stream = current_head + 1

                positioned = record.model_copy(update={
                    "position": JournalPosition(
                        global_sequence=next_global,
                        stream_id=stream_id,
                        stream_sequence=next_stream,
                    ),
                    "previous_record_hash": previous_hash,
                })
                committed = positioned.model_copy(update={
                    "record_hash": calculate_journal_record_hash(
                        positioned, previous_record_hash=previous_hash,
                    ),
                })

                cursor.execute(
                    """
                    INSERT INTO journal_records (
                        global_sequence, record_id, stream_id,
                        stream_sequence, category, event_type,
                        organization_id, project_ids, occurred_at,
                        recorded_at, causal_event_id, idempotency_key,
                        payload_schema_version, payload, evidence_ids,
                        previous_record_hash, record_hash, runtime_revision_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        next_global,
                        committed.id,
                        stream_id,
                        next_stream,
                        committed.category.value,
                        committed.event_type.value,
                        committed.organization_id,
                        _dumps(committed.project_ids),
                        _iso(committed.occurred_at),
                        _iso(committed.recorded_at),
                        committed.causal_event_id,
                        committed.idempotency_key,
                        committed.payload_schema_version,
                        _dumps(committed.payload),
                        _dumps(committed.evidence_ids),
                        committed.previous_record_hash,
                        committed.record_hash,
                        committed.runtime_revision_id,
                    ),
                )
                cursor.execute(
                    "INSERT INTO journal_stream_heads (stream_id, "
                    "stream_sequence) VALUES (?, ?) "
                    "ON CONFLICT(stream_id) DO UPDATE SET stream_sequence = ?",
                    (stream_id, next_stream, next_stream),
                )
                cursor.execute("COMMIT")
                logger.debug(
                    "journal record appended",
                    extra={
                        "journal_event_type": committed.event_type.value,
                        "journal_sequence": next_global,
                        "journal_stream": stream_id,
                        "record_id": committed.id,
                        "organization_id": committed.organization_id,
                        "causal_event_id": committed.causal_event_id,
                        "revision_id": committed.runtime_revision_id,
                    },
                )
                return committed
            except (JournalConcurrencyError, JournalIdempotencyError):
                raise
            except Exception:
                cursor.execute("ROLLBACK")
                raise

    def get(self, record_id: str) -> JournalRecord | None:
        row = self._db.connection.execute(
            "SELECT * FROM journal_records WHERE record_id = ?", (record_id,),
        ).fetchone()
        return _row_to_record(row) if row else None

    def read_from(
        self, *, global_sequence: int, limit: int | None = None,
    ) -> list[JournalRecord]:
        sql = (
            "SELECT * FROM journal_records WHERE global_sequence >= ? "
            "ORDER BY global_sequence"
        )
        params: tuple[Any, ...] = (global_sequence,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (global_sequence, limit)
        return [
            _row_to_record(row)
            for row in self._db.connection.execute(sql, params)
        ]

    def read_range(
        self, *, start_sequence: int, end_sequence: int,
    ) -> list[JournalRecord]:
        return [
            _row_to_record(row)
            for row in self._db.connection.execute(
                "SELECT * FROM journal_records WHERE global_sequence "
                "BETWEEN ? AND ? ORDER BY global_sequence",
                (start_sequence, end_sequence),
            )
        ]

    def read_stream(
        self, *, stream_id: str, after_sequence: int = 0,
    ) -> list[JournalRecord]:
        return [
            _row_to_record(row)
            for row in self._db.connection.execute(
                "SELECT * FROM journal_records WHERE stream_id = ? "
                "AND stream_sequence > ? ORDER BY stream_sequence",
                (stream_id, after_sequence),
            )
        ]

    def find_by_idempotency_key(
        self, idempotency_key: str,
    ) -> JournalRecord | None:
        row = self._db.connection.execute(
            "SELECT * FROM journal_records WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return _row_to_record(row) if row else None

    def latest_position(self) -> JournalPosition | None:
        row = self._db.connection.execute(
            "SELECT stream_id, stream_sequence, global_sequence "
            "FROM journal_records ORDER BY global_sequence DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return JournalPosition(
            global_sequence=row["global_sequence"],
            stream_id=row["stream_id"],
            stream_sequence=row["stream_sequence"],
        )

    def stream_head(self, stream_id: str) -> int:
        row = self._db.connection.execute(
            "SELECT stream_sequence FROM journal_stream_heads "
            "WHERE stream_id = ?",
            (stream_id,),
        ).fetchone()
        return row["stream_sequence"] if row else 0

    def __len__(self) -> int:
        row = self._db.connection.execute(
            "SELECT COUNT(*) AS n FROM journal_records"
        ).fetchone()
        return row["n"]

    def _corrupt_payload_for_testing(
        self, *, global_sequence: int, payload: dict[str, Any],
    ) -> None:
        """Simulate a manual UPDATE against the database file."""
        with self._db.lock:
            self._db.connection.execute(
                "UPDATE journal_records SET payload = ? "
                "WHERE global_sequence = ?",
                (_dumps(payload), global_sequence),
            )

    def _truncate_for_testing(self, *, keep_through_sequence: int) -> None:
        with self._db.lock:
            self._db.connection.execute(
                "DELETE FROM journal_records WHERE global_sequence > ?",
                (keep_through_sequence,),
            )


def _row_to_record(row: sqlite3.Row) -> JournalRecord:
    from lltpm.persistence.models import JournalRecordCategory
    from lltpm.persistence.versioning import assert_supported_event_type

    # Resolved through the guard rather than the bare enum, so a record
    # written by newer code produces the explanation of why skipping an
    # unknown organizational mutation is unsafe.
    event_type = assert_supported_event_type(row["event_type"])

    return JournalRecord(
        id=row["record_id"],
        position=JournalPosition(
            global_sequence=row["global_sequence"],
            stream_id=row["stream_id"],
            stream_sequence=row["stream_sequence"],
        ),
        category=JournalRecordCategory(row["category"]),
        event_type=event_type,
        organization_id=row["organization_id"],
        project_ids=_loads(row["project_ids"], []),
        occurred_at=_parse(row["occurred_at"]),
        recorded_at=_parse(row["recorded_at"]),
        causal_event_id=row["causal_event_id"],
        idempotency_key=row["idempotency_key"],
        payload_schema_version=row["payload_schema_version"],
        payload=_loads(row["payload"], {}),
        evidence_ids=_loads(row["evidence_ids"], []),
        previous_record_hash=row["previous_record_hash"],
        record_hash=row["record_hash"],
        runtime_revision_id=row["runtime_revision_id"],
    )


class SQLiteRuntimeStateStore:
    """Rebuildable projections on SQLite, with atomic promotion."""

    def __init__(self, persistence: SQLitePersistence):
        self._db = persistence

    def get_current_revision(self, organization_id: str) -> str | None:
        row = self._db.connection.execute(
            "SELECT revision_id FROM runtime_revisions "
            "WHERE organization_id = ? AND is_current = 1",
            (organization_id,),
        ).fetchone()
        return row["revision_id"] if row else None

    def get_project_state(
        self, project_id: str,
    ) -> ProjectRuntimeStateRecord | None:
        row = self._db.connection.execute(
            "SELECT * FROM project_runtime_state WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        return ProjectRuntimeStateRecord(
            project_id=row["project_id"],
            organization_id=row["organization_id"],
            revision_id=row["revision_id"],
            state_fingerprint=row["state_fingerprint"],
            state=_loads(row["state"], {}),
            updated_at=_parse(row["updated_at"]),
        )

    def put_project_state(self, state: ProjectRuntimeStateRecord) -> None:
        with self._db.lock:
            self._db.connection.execute(
                """
                INSERT INTO project_runtime_state (
                    project_id, organization_id, revision_id,
                    state_fingerprint, state, updated_at
                ) VALUES (?,?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    revision_id = excluded.revision_id,
                    state_fingerprint = excluded.state_fingerprint,
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (
                    state.project_id, state.organization_id, state.revision_id,
                    state.state_fingerprint, _dumps(state.state),
                    _iso(state.updated_at),
                ),
            )

    def get_portfolio_state(
        self, organization_id: str,
    ) -> PortfolioRuntimeStateRecord | None:
        row = self._db.connection.execute(
            "SELECT * FROM portfolio_runtime_state WHERE organization_id = ?",
            (organization_id,),
        ).fetchone()
        if row is None:
            return None
        return PortfolioRuntimeStateRecord(
            organization_id=row["organization_id"],
            revision_id=row["revision_id"],
            state_fingerprint=row["state_fingerprint"],
            state=_loads(row["state"], {}),
            updated_at=_parse(row["updated_at"]),
        )

    def put_portfolio_state(
        self, state: PortfolioRuntimeStateRecord,
    ) -> None:
        with self._db.lock:
            self._db.connection.execute(
                """
                INSERT INTO portfolio_runtime_state (
                    organization_id, revision_id, state_fingerprint, state,
                    updated_at
                ) VALUES (?,?,?,?,?)
                ON CONFLICT(organization_id) DO UPDATE SET
                    revision_id = excluded.revision_id,
                    state_fingerprint = excluded.state_fingerprint,
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (
                    state.organization_id, state.revision_id,
                    state.state_fingerprint, _dumps(state.state),
                    _iso(state.updated_at),
                ),
            )

    def put_revision(self, revision: RuntimeRevisionRecord) -> None:
        with self._db.lock:
            self._db.connection.execute(
                """
                INSERT INTO runtime_revisions (
                    revision_id, organization_id, complete, is_current,
                    parent_revision_id, evidence_cutoff_at, created_at,
                    state_fingerprint, state_fingerprints, payload
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(revision_id) DO UPDATE SET
                    complete = excluded.complete,
                    parent_revision_id = excluded.parent_revision_id,
                    evidence_cutoff_at = excluded.evidence_cutoff_at,
                    created_at = excluded.created_at,
                    state_fingerprint = excluded.state_fingerprint,
                    state_fingerprints = excluded.state_fingerprints,
                    payload = excluded.payload
                """,
                (
                    revision.revision_id, revision.organization_id,
                    int(revision.complete), int(revision.is_current),
                    revision.parent_revision_id,
                    _iso(revision.evidence_cutoff_at),
                    _iso(revision.created_at), revision.state_fingerprint,
                    _dumps(revision.state_fingerprints),
                    _dumps(revision.payload),
                ),
            )

    def get_revision(
        self, revision_id: str,
    ) -> RuntimeRevisionRecord | None:
        row = self._db.connection.execute(
            "SELECT * FROM runtime_revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        return _row_to_revision(row) if row else None

    def list_revisions(
        self, organization_id: str,
    ) -> list[RuntimeRevisionRecord]:
        return [
            _row_to_revision(row)
            for row in self._db.connection.execute(
                "SELECT * FROM runtime_revisions WHERE organization_id = ? "
                "ORDER BY revision_id",
                (organization_id,),
            )
        ]

    def promote_revision(
        self,
        *,
        organization_id: str,
        revision_id: str,
        expected_current_revision_id: str | None,
    ) -> None:
        """The pointer move, in one transaction.

        A crash before COMMIT leaves the previous revision current; a
        crash after leaves the new one. Nothing observes a half-promoted
        state.
        """
        with self._db.lock:
            cursor = self._db.connection.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                row = cursor.execute(
                    "SELECT complete FROM runtime_revisions "
                    "WHERE revision_id = ?",
                    (revision_id,),
                ).fetchone()
                if row is None:
                    cursor.execute("ROLLBACK")
                    raise RevisionPromotionError(
                        f"Revision {revision_id} has not been persisted; "
                        f"there is nothing to promote."
                    )
                if not row["complete"]:
                    cursor.execute("ROLLBACK")
                    raise RevisionPromotionError(
                        f"Revision {revision_id} is not complete and must "
                        f"not become current; the previous revision keeps "
                        f"answering."
                    )
                current_row = cursor.execute(
                    "SELECT revision_id FROM runtime_revisions "
                    "WHERE organization_id = ? AND is_current = 1",
                    (organization_id,),
                ).fetchone()
                current = current_row["revision_id"] if current_row else None
                if current != expected_current_revision_id:
                    cursor.execute("ROLLBACK")
                    raise RevisionPromotionError(
                        f"Current revision for '{organization_id}' is "
                        f"{current!r}, not {expected_current_revision_id!r}; "
                        f"another writer promoted first."
                    )
                cursor.execute(
                    "UPDATE runtime_revisions SET is_current = 0 "
                    "WHERE organization_id = ?",
                    (organization_id,),
                )
                cursor.execute(
                    "UPDATE runtime_revisions SET is_current = 1 "
                    "WHERE revision_id = ?",
                    (revision_id,),
                )
                cursor.execute("COMMIT")
            except RevisionPromotionError:
                raise
            except Exception:
                cursor.execute("ROLLBACK")
                raise

    def clear_projections(self, organization_id: str) -> None:
        with self._db.lock:
            cursor = self._db.connection.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                cursor.execute(
                    "DELETE FROM runtime_revisions WHERE organization_id = ?",
                    (organization_id,),
                )
                cursor.execute(
                    "DELETE FROM portfolio_runtime_state "
                    "WHERE organization_id = ?",
                    (organization_id,),
                )
                cursor.execute(
                    "DELETE FROM project_runtime_state "
                    "WHERE organization_id = ? OR organization_id IS NULL",
                    (organization_id,),
                )
                cursor.execute("COMMIT")
            except Exception:
                cursor.execute("ROLLBACK")
                raise


def _row_to_revision(row: sqlite3.Row) -> RuntimeRevisionRecord:
    return RuntimeRevisionRecord(
        revision_id=row["revision_id"],
        organization_id=row["organization_id"],
        complete=bool(row["complete"]),
        is_current=bool(row["is_current"]),
        parent_revision_id=row["parent_revision_id"],
        evidence_cutoff_at=_parse(row["evidence_cutoff_at"]),
        created_at=_parse(row["created_at"]),
        state_fingerprint=row["state_fingerprint"],
        state_fingerprints=_loads(row["state_fingerprints"], {}),
        payload=_loads(row["payload"], {}),
    )


class SQLiteIntelligenceArtifactStore:
    """Derived artifact cache on SQLite, keyed per revision."""

    def __init__(self, persistence: SQLitePersistence):
        self._db = persistence

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
        created_at: datetime | None = None,
    ) -> None:
        with self._db.lock:
            self._db.connection.execute(
                """
                INSERT INTO intelligence_artifacts (
                    artifact_type, artifact_id, revision_id, fingerprint,
                    payload, derivation_version, policy_fingerprint,
                    created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(artifact_type, artifact_id, revision_id)
                DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    payload = excluded.payload,
                    derivation_version = excluded.derivation_version,
                    policy_fingerprint = excluded.policy_fingerprint,
                    created_at = excluded.created_at
                """,
                (
                    artifact_type, artifact_id, revision_id, fingerprint,
                    _dumps(payload), derivation_version, policy_fingerprint,
                    _iso(created_at),
                ),
            )

    def get(
        self, artifact_type: str, artifact_id: str,
    ) -> IntelligenceArtifactRecord | None:
        row = self._db.connection.execute(
            "SELECT * FROM intelligence_artifacts WHERE artifact_type = ? "
            "AND artifact_id = ? ORDER BY revision_id DESC LIMIT 1",
            (artifact_type, artifact_id),
        ).fetchone()
        return _row_to_artifact(row) if row else None

    def get_at_revision(
        self, artifact_type: str, artifact_id: str, revision_id: str,
    ) -> IntelligenceArtifactRecord | None:
        row = self._db.connection.execute(
            "SELECT * FROM intelligence_artifacts WHERE artifact_type = ? "
            "AND artifact_id = ? AND revision_id = ?",
            (artifact_type, artifact_id, revision_id),
        ).fetchone()
        return _row_to_artifact(row) if row else None

    def get_revision_artifacts(
        self, revision_id: str,
    ) -> list[IntelligenceArtifactRecord]:
        return [
            _row_to_artifact(row)
            for row in self._db.connection.execute(
                "SELECT * FROM intelligence_artifacts WHERE revision_id = ? "
                "ORDER BY artifact_type, artifact_id",
                (revision_id,),
            )
        ]

    def clear(self) -> None:
        with self._db.lock:
            self._db.connection.execute("DELETE FROM intelligence_artifacts")

    def __len__(self) -> int:
        row = self._db.connection.execute(
            "SELECT COUNT(*) AS n FROM intelligence_artifacts"
        ).fetchone()
        return row["n"]


def _row_to_artifact(row: sqlite3.Row) -> IntelligenceArtifactRecord:
    return IntelligenceArtifactRecord(
        artifact_type=row["artifact_type"],
        artifact_id=row["artifact_id"],
        revision_id=row["revision_id"],
        fingerprint=row["fingerprint"],
        payload=_loads(row["payload"], {}),
        derivation_version=row["derivation_version"],
        policy_fingerprint=row["policy_fingerprint"],
        created_at=_parse(row["created_at"]),
    )


class SQLiteSnapshotStore:
    """Snapshot checkpoints on SQLite."""

    def __init__(self, persistence: SQLitePersistence):
        self._db = persistence

    def put_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        with self._db.lock:
            self._db.connection.execute(
                """
                INSERT INTO runtime_snapshots (
                    snapshot_id, organization_id, journal_global_sequence,
                    payload, created_at
                ) VALUES (?,?,?,?,?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    journal_global_sequence =
                        excluded.journal_global_sequence,
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    snapshot.id, snapshot.organization_id,
                    snapshot.journal_global_sequence,
                    _dumps(snapshot.model_dump()), _iso(snapshot.created_at),
                ),
            )

    def get_snapshot(self, snapshot_id: str) -> RuntimeSnapshot | None:
        row = self._db.connection.execute(
            "SELECT payload FROM runtime_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return RuntimeSnapshot.model_validate(_loads(row["payload"], {}))

    def list_snapshots(self, organization_id: str) -> list[RuntimeSnapshot]:
        return [
            RuntimeSnapshot.model_validate(_loads(row["payload"], {}))
            for row in self._db.connection.execute(
                "SELECT payload FROM runtime_snapshots "
                "WHERE organization_id = ? "
                "ORDER BY journal_global_sequence DESC, snapshot_id",
                (organization_id,),
            )
        ]

    def latest_compatible_snapshot(
        self,
        *,
        organization_id: str,
        manifest: ReproducibilityManifest | None,
    ) -> RuntimeSnapshot | None:
        for snapshot in self.list_snapshots(organization_id):
            usable, _ = snapshot_is_usable(
                snapshot=snapshot, manifest=manifest,
            )
            if usable:
                return snapshot
        return None

    def clear(self) -> None:
        with self._db.lock:
            self._db.connection.execute("DELETE FROM runtime_snapshots")

    def _corrupt_for_testing(self, snapshot_id: str) -> None:
        snapshot = self.get_snapshot(snapshot_id)
        tampered = snapshot.model_copy(update={
            "state_fingerprints": {
                **snapshot.state_fingerprints, "tampered": "yes",
            },
        })
        with self._db.lock:
            self._db.connection.execute(
                "UPDATE runtime_snapshots SET payload = ? "
                "WHERE snapshot_id = ?",
                (_dumps(tampered.model_dump()), snapshot_id),
            )

    def __len__(self) -> int:
        row = self._db.connection.execute(
            "SELECT COUNT(*) AS n FROM runtime_snapshots"
        ).fetchone()
        return row["n"]
