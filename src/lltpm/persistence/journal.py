"""The append-only event journal.

Small on purpose. The journal needs to append, read, deduplicate,
verify, and nothing else — an organization's durable history does not
need a stream processor, a query language, or a consumer framework to be
trustworthy. It needs to be ordered, immutable, and checkable.

Three rules do most of the work:

- **Journal first.** A durable change is appended before the runtime
  treats it as real. Orchestrating first and crashing means the event
  never happened, which is the one failure recovery cannot fix.
- **Idempotent.** The same webhook delivered four times is one record.
- **Immutable.** Corrections are new records — a reopen, a supersession
  — never an edit. Editing history destroys the reason for keeping it.

This module holds the in-memory reference implementation and the record
builder both backends share.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from lltpm.persistence.integrity import (
    calculate_journal_record_hash,
    payloads_describe_same_fact,
    verify_journal_hash_chain,
)
from lltpm.persistence.models import (
    EVENT_TYPE_CATEGORY,
    JournalAnchor,
    JournalEventType,
    JournalPosition,
    JournalRecord,
    JournalRecordCategory,
    build_journal_idempotency_key,
)
from lltpm.persistence.protocols import (
    JournalConcurrencyError,
    JournalIdempotencyError,
)
from lltpm.persistence.versioning import CURRENT_PAYLOAD_SCHEMA_VERSION

# Standard library only, and no handler: configuring logging is an
# application's job, and `persistence` imports no first-party package at
# all so that a storage backend can never drag a domain along with it.
logger = logging.getLogger(__name__)


def build_journal_record(
    *,
    event_type: JournalEventType,
    stream_id: str,
    occurred_at: datetime,
    recorded_at: datetime,
    payload: dict[str, Any],
    organization_id: str | None = None,
    project_ids: list[str] | None = None,
    causal_event_id: str | None = None,
    idempotency_key: str | None = None,
    source_key: str | None = None,
    evidence_ids: list[str] | None = None,
    runtime_revision_id: str | None = None,
    payload_schema_version: int = CURRENT_PAYLOAD_SCHEMA_VERSION,
    category: JournalRecordCategory | None = None,
) -> JournalRecord:
    """Assemble an unsequenced record.

    Position and hash are left provisional; the store fills them in when
    it commits, because only the store knows what comes next.
    """
    if idempotency_key is None:
        if source_key is None:
            raise ValueError(
                "A journal record needs either an explicit idempotency key "
                "or a source key to derive one from."
            )
        idempotency_key = build_journal_idempotency_key(
            event_type=event_type, source_key=source_key,
        )
    return JournalRecord(
        id=f"jr-{idempotency_key}",
        position=JournalPosition(
            global_sequence=1, stream_id=stream_id, stream_sequence=1,
        ),
        category=category or EVENT_TYPE_CATEGORY[event_type],
        event_type=event_type,
        organization_id=organization_id,
        project_ids=list(project_ids or []),
        occurred_at=occurred_at,
        recorded_at=recorded_at,
        causal_event_id=causal_event_id,
        idempotency_key=idempotency_key,
        payload_schema_version=payload_schema_version,
        payload=payload,
        evidence_ids=list(evidence_ids or []),
        runtime_revision_id=runtime_revision_id,
    )


class InMemoryEventJournalStore:
    """Reference journal. Same semantics as SQLite, no file.

    Used by tests and by anything that wants a throwaway organization.
    The locking is real: the concurrency and idempotency guarantees are
    part of the contract, not an artifact of the backend.
    """

    def __init__(self) -> None:
        self._records: list[JournalRecord] = []
        self._by_id: dict[str, JournalRecord] = {}
        self._by_idempotency_key: dict[str, JournalRecord] = {}
        self._stream_heads: dict[str, int] = {}
        self._lock = threading.Lock()

    # ── Writing ──────────────────────────────────────────────────────

    def append(
        self,
        *,
        record: JournalRecord,
        expected_stream_sequence: int | None = None,
    ) -> JournalRecord:
        """Commit atomically, or not at all.

        A returning duplicate is not an error — a redelivered webhook is
        an ordinary event, and the caller gets back the record that
        already exists.
        """
        with self._lock:
            existing = self._by_idempotency_key.get(record.idempotency_key)
            if existing is not None:
                self._assert_same_content(existing, record)
                logger.debug(
                    "journal append deduplicated",
                    extra={
                        "journal_event_type": record.event_type.value,
                        "idempotency_key": record.idempotency_key,
                        "journal_sequence": existing.position.global_sequence,
                        "record_id": existing.id,
                    },
                )
                return existing

            stream_id = record.position.stream_id
            current_head = self._stream_heads.get(stream_id, 0)
            if (
                expected_stream_sequence is not None
                and expected_stream_sequence != current_head
            ):
                raise JournalConcurrencyError(
                    f"Stream '{stream_id}' is at sequence {current_head}, "
                    f"not {expected_stream_sequence}. Another writer "
                    f"committed first; re-read and decide again rather than "
                    f"overwriting a change you never saw."
                )

            previous_hash = (
                self._records[-1].record_hash if self._records else None
            )
            committed = self._seal(
                record=record,
                global_sequence=len(self._records) + 1,
                stream_sequence=current_head + 1,
                previous_hash=previous_hash,
            )

            self._records.append(committed)
            self._by_id[committed.id] = committed
            self._by_idempotency_key[committed.idempotency_key] = committed
            self._stream_heads[stream_id] = committed.position.stream_sequence
            logger.debug(
                "journal record appended",
                extra={
                    "journal_event_type": committed.event_type.value,
                    "journal_sequence": committed.position.global_sequence,
                    "journal_stream": stream_id,
                    "record_id": committed.id,
                    "organization_id": committed.organization_id,
                    "causal_event_id": committed.causal_event_id,
                    "revision_id": committed.runtime_revision_id,
                },
            )
            return committed

    @staticmethod
    def _seal(
        *,
        record: JournalRecord,
        global_sequence: int,
        stream_sequence: int,
        previous_hash: str | None,
    ) -> JournalRecord:
        positioned = record.model_copy(update={
            "position": JournalPosition(
                global_sequence=global_sequence,
                stream_id=record.position.stream_id,
                stream_sequence=stream_sequence,
            ),
            "previous_record_hash": previous_hash,
        })
        return positioned.model_copy(update={
            "record_hash": calculate_journal_record_hash(
                positioned, previous_record_hash=previous_hash,
            ),
        })

    @staticmethod
    def _assert_same_content(
        existing: JournalRecord, incoming: JournalRecord,
    ) -> None:
        if existing.event_type != incoming.event_type or not (
            payloads_describe_same_fact(existing.payload, incoming.payload)
        ):
            raise JournalIdempotencyError(
                f"Idempotency key '{existing.idempotency_key}' was reused "
                f"with different content; two different facts cannot share "
                f"one identity."
            )

    # ── Reading ──────────────────────────────────────────────────────

    def get(self, record_id: str) -> JournalRecord | None:
        return self._by_id.get(record_id)

    def read_from(
        self,
        *,
        global_sequence: int,
        limit: int | None = None,
    ) -> list[JournalRecord]:
        found = [
            r for r in self._records
            if r.position.global_sequence >= global_sequence
        ]
        return found[:limit] if limit is not None else found

    def read_range(
        self, *, start_sequence: int, end_sequence: int,
    ) -> list[JournalRecord]:
        return [
            r for r in self._records
            if start_sequence <= r.position.global_sequence <= end_sequence
        ]

    def read_stream(
        self, *, stream_id: str, after_sequence: int = 0,
    ) -> list[JournalRecord]:
        return [
            r for r in self._records
            if r.position.stream_id == stream_id
            and r.position.stream_sequence > after_sequence
        ]

    def find_by_idempotency_key(
        self, idempotency_key: str,
    ) -> JournalRecord | None:
        return self._by_idempotency_key.get(idempotency_key)

    def latest_position(self) -> JournalPosition | None:
        return self._records[-1].position if self._records else None

    def stream_head(self, stream_id: str) -> int:
        return self._stream_heads.get(stream_id, 0)

    def __len__(self) -> int:
        return len(self._records)

    # ── Testing seam ─────────────────────────────────────────────────

    def _corrupt_payload_for_testing(
        self, *, global_sequence: int, payload: dict[str, Any],
    ) -> None:
        """Simulate an out-of-band edit — a manual UPDATE, a bad backup.

        Bypasses the append path deliberately; that is the failure being
        tested for.
        """
        index = global_sequence - 1
        record = self._records[index]
        tampered = record.model_copy(update={"payload": payload})
        self._records[index] = tampered
        self._by_id[tampered.id] = tampered
        self._by_idempotency_key[tampered.idempotency_key] = tampered

    def _truncate_for_testing(self, *, keep_through_sequence: int) -> None:
        self._records = [
            r for r in self._records
            if r.position.global_sequence <= keep_through_sequence
        ]
        self._by_id = {r.id: r for r in self._records}
        self._by_idempotency_key = {
            r.idempotency_key: r for r in self._records
        }


# ── Read-only inspection ─────────────────────────────────────────────


def inspect_journal_range(
    *,
    journal: Any,
    start_sequence: int,
    end_sequence: int,
    event_types: list[JournalEventType] | None = None,
    organization_id: str | None = None,
) -> list[JournalRecord]:
    """Read a window of history. Never mutates anything.

    The seam future debugging and export tooling will use.
    """
    if end_sequence < start_sequence:
        raise ValueError("end_sequence precedes start_sequence.")
    records = [
        r for r in journal.read_from(global_sequence=start_sequence)
        if r.position.global_sequence <= end_sequence
    ]
    if event_types is not None:
        wanted = set(event_types)
        records = [r for r in records if r.event_type in wanted]
    if organization_id is not None:
        records = [r for r in records if r.organization_id == organization_id]
    return records


def verify_journal(
    *,
    journal: Any,
    expected_head_sequence: int | None = None,
    anchor: JournalAnchor | None = None,
) -> Any:
    """Verify the whole chain from the beginning.

    With an ``anchor`` the check also covers wholesale rewriting, which
    the chain alone cannot detect — see ``verify_journal_hash_chain``.
    """
    return verify_journal_hash_chain(
        journal.read_from(global_sequence=1),
        expected_head_sequence=(
            expected_head_sequence
            if anchor is None else anchor.global_sequence
        ),
        expected_head_hash=anchor.record_hash if anchor else None,
    )


def build_journal_anchor(
    *,
    journal: Any,
    created_at: datetime,
    organization_id: str = "",
    lltpm_version: str = "",
) -> JournalAnchor | None:
    """The current head, as something to retain out of band.

    ``None`` for an empty journal: there is nothing yet to anchor, and
    an anchor claiming sequence zero would be a statement about history
    that does not exist.
    """
    head = journal.latest_position()
    if head is None:
        return None
    records = journal.read_from(
        global_sequence=head.global_sequence, limit=1,
    )
    if not records:
        return None
    return JournalAnchor(
        global_sequence=head.global_sequence,
        record_hash=records[-1].record_hash,
        created_at=created_at,
        organization_id=organization_id,
        lltpm_version=lltpm_version,
    )


def records_by_event_type(
    records: list[JournalRecord], event_type: JournalEventType,
) -> list[JournalRecord]:
    return [r for r in records if r.event_type == event_type]
