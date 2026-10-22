"""Journal and snapshot integrity.

The hash chain here is not a blockchain and does not pretend to be. It
solves a much more ordinary problem: a durable history stored in a file
someone can open with `sqlite3` will eventually be opened with
`sqlite3`. Accidental mutation, a truncated copy, a well-meaning manual
UPDATE, a partially written backup — these are the realistic failures,
and each of them silently changes what the organization believes its own
history was.

Chaining each record to its predecessor makes those failures loud. It
does not make them impossible, and it is not a defence against a
determined attacker with write access.

Everything rests on canonical serialization: if the same record can
serialize two ways, the hash proves nothing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel

from lltpm.persistence.models import JournalRecord, RuntimeSnapshot


class JournalIntegrityError(RuntimeError):
    """Raised when durable history fails verification."""


class JournalCorruptionReport(BaseModel):
    valid: bool = True
    corrupted_at_sequence: int | None = None
    corrupted_record_id: str | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None
    broken_link_at_sequence: int | None = None
    truncated_after_sequence: int | None = None
    missing_sequences: list[int] = []
    #: The hash of the last record walked. This is the anchor: retained
    #: outside the database, it is what distinguishes an edited history
    #: from a rewritten one.
    head_record_hash: str | None = None
    head_sequence: int = 0
    anchor_matched: bool | None = None
    explanation: str = ""


def _normalize(value: Any) -> Any:
    """Reduce a value to something JSON can represent one way only."""
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        # Naive datetimes are treated as UTC so that a value written by a
        # naive process and one written by an aware process cannot
        # produce two different hashes for the same instant.
        moment = (
            value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
        return moment.isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(k): _normalize(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (set, frozenset)):
        return sorted((_normalize(v) for v in value), key=_sort_key)
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, float):
        # Round-trippable and stable across platforms.
        return float(repr(value))
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    # Anything else would smuggle a repr — and reprs carry memory
    # addresses, which would make the hash different on every run.
    raise TypeError(
        f"Cannot canonically serialize {type(value).__name__}; durable "
        f"payloads must be JSON-compatible."
    )


def _sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def canonical_json_bytes(value: object) -> bytes:
    """Deterministic bytes for any JSON-compatible value.

    Stable key order, UTC-normalized timestamps, enums by value, sets
    sorted, no Python repr anywhere. Two structures that mean the same
    thing serialize identically; two that differ, differ.
    """
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def content_fingerprint(content: str | None) -> str | None:
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── Journal hash chain ───────────────────────────────────────────────

_HASH_EXCLUDED_FIELDS = frozenset({"record_hash"})


def calculate_journal_record_hash(
    record: JournalRecord | dict[str, Any],
    *,
    previous_record_hash: str | None = None,
) -> str:
    """Hash a record together with its predecessor.

    ``record_hash`` itself is excluded, and the link to the previous hash
    is what makes an edit anywhere in history detectable from the head.
    """
    payload = (
        record.model_dump() if isinstance(record, JournalRecord)
        else dict(record)
    )
    previous = (
        previous_record_hash
        if previous_record_hash is not None
        else payload.get("previous_record_hash")
    )
    body = {
        key: value for key, value in payload.items()
        if key not in _HASH_EXCLUDED_FIELDS
    }
    body["previous_record_hash"] = previous
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def stamp_record_hash(
    record: JournalRecord, *, previous_record_hash: str | None,
) -> JournalRecord:
    """Return the record with its chain links filled in."""
    linked = record.model_copy(update={
        "previous_record_hash": previous_record_hash,
    })
    return linked.model_copy(update={
        "record_hash": calculate_journal_record_hash(
            linked, previous_record_hash=previous_record_hash,
        ),
    })


def verify_journal_hash_chain(
    records: list[JournalRecord],
    *,
    expected_head_sequence: int | None = None,
    starting_previous_hash: str | None = None,
    expected_head_hash: str | None = None,
) -> JournalCorruptionReport:
    """Walk the chain and report the first place it breaks.

    Reports rather than repairs. Silently fixing history would destroy
    the only evidence that something went wrong with it.

    **What the chain alone can and cannot detect.** Hash chaining in the
    Haber–Stornetta sense makes it impossible to insert, delete or edit a
    record *in place*: the link to the predecessor breaks, or the
    record's own hash stops matching. That catches a bad restore, a
    partial write, a manual UPDATE — the realistic failures. It does not
    catch an actor who can rewrite the whole file, because they can
    recompute every link after the one they changed. Their original
    answer to this was to publish the head hash where it could not be
    retracted.

    ``expected_head_hash`` is that mechanism, scaled to a single local
    file: an operator retains the head hash out of band and passes it
    back. A rewritten history is internally consistent and will not
    match it. Without an anchor this function is honest about what it
    checked, which is internal consistency.
    """
    if not records:
        if expected_head_sequence or expected_head_hash:
            return JournalCorruptionReport(
                valid=False,
                truncated_after_sequence=0,
                anchor_matched=False if expected_head_hash else None,
                explanation=(
                    f"Journal is empty but sequence "
                    f"{expected_head_sequence or '?'} was expected; history "
                    f"is missing rather than absent."
                ),
            )
        return JournalCorruptionReport(valid=True, explanation="Empty journal.")

    ordered = sorted(records, key=lambda r: r.position.global_sequence)
    previous_hash = starting_previous_hash
    previous_sequence = ordered[0].position.global_sequence - 1
    missing: list[int] = []

    for record in ordered:
        sequence = record.position.global_sequence
        if sequence != previous_sequence + 1:
            missing.extend(range(previous_sequence + 1, sequence))

        if record.previous_record_hash != previous_hash and not missing:
            return JournalCorruptionReport(
                valid=False,
                corrupted_at_sequence=sequence,
                corrupted_record_id=record.id,
                broken_link_at_sequence=sequence,
                expected_hash=previous_hash,
                actual_hash=record.previous_record_hash,
                missing_sequences=missing,
                explanation=(
                    f"Record at sequence {sequence} does not link to its "
                    f"predecessor; history was altered, reordered, or a "
                    f"record was removed."
                ),
            )

        recalculated = calculate_journal_record_hash(
            record, previous_record_hash=record.previous_record_hash,
        )
        if recalculated != record.record_hash:
            return JournalCorruptionReport(
                valid=False,
                corrupted_at_sequence=sequence,
                corrupted_record_id=record.id,
                expected_hash=record.record_hash,
                actual_hash=recalculated,
                missing_sequences=missing,
                explanation=(
                    f"Record {record.id} at sequence {sequence} does not "
                    f"match its own hash; its stored content was modified "
                    f"after it was committed."
                ),
            )

        previous_hash = record.record_hash
        previous_sequence = sequence

    head = ordered[-1].position.global_sequence
    head_hash = ordered[-1].record_hash
    if expected_head_sequence is not None and head < expected_head_sequence:
        return JournalCorruptionReport(
            valid=False,
            truncated_after_sequence=head,
            missing_sequences=missing,
            head_record_hash=head_hash,
            head_sequence=head,
            explanation=(
                f"Journal ends at sequence {head} but {expected_head_sequence}"
                f" was expected; history is incomplete. The missing records "
                f"are not invented."
            ),
        )
    if missing:
        return JournalCorruptionReport(
            valid=False,
            missing_sequences=missing,
            broken_link_at_sequence=missing[0],
            head_record_hash=head_hash,
            head_sequence=head,
            explanation=(
                f"Sequence gap at {missing}; records were removed from the "
                f"middle of history."
            ),
        )

    if expected_head_hash is not None and expected_head_hash != head_hash:
        # The chain is internally consistent and still wrong: every link
        # recomputes, which is exactly what a wholesale rewrite produces.
        return JournalCorruptionReport(
            valid=False,
            head_record_hash=head_hash,
            head_sequence=head,
            anchor_matched=False,
            expected_hash=expected_head_hash,
            actual_hash=head_hash,
            explanation=(
                f"The chain is internally consistent through sequence "
                f"{head} but its head does not match the retained anchor. "
                f"An in-place edit breaks a link; a rewritten history does "
                f"not. This is the case an anchor exists to catch."
            ),
        )

    return JournalCorruptionReport(
        valid=True,
        head_record_hash=head_hash,
        head_sequence=head,
        anchor_matched=(
            True if expected_head_hash is not None else None
        ),
        explanation=(
            f"Hash chain verified through sequence {head}"
            + (
                " and matches the retained anchor."
                if expected_head_hash is not None
                else ". Internal consistency only: no anchor was supplied, "
                     "so a wholesale rewrite would not be detected."
            )
        ),
    )


def assert_journal_hash_chain(records: list[JournalRecord]) -> None:
    report = verify_journal_hash_chain(records)
    if not report.valid:
        raise JournalIntegrityError(report.explanation)


# ── Snapshot integrity ───────────────────────────────────────────────

_SNAPSHOT_EXCLUDED_FIELDS = frozenset({"snapshot_hash"})


def calculate_snapshot_hash(
    snapshot: RuntimeSnapshot | dict[str, Any],
) -> str:
    payload = (
        snapshot.model_dump() if isinstance(snapshot, RuntimeSnapshot)
        else dict(snapshot)
    )
    body = {
        key: value for key, value in payload.items()
        if key not in _SNAPSHOT_EXCLUDED_FIELDS
    }
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def stamp_snapshot_hash(snapshot: RuntimeSnapshot) -> RuntimeSnapshot:
    return snapshot.model_copy(update={
        "snapshot_hash": calculate_snapshot_hash(snapshot),
    })


def verify_snapshot_integrity(snapshot: RuntimeSnapshot) -> bool:
    """A snapshot that fails this is discarded, not repaired.

    It is only ever an accelerator; the journal can always rebuild what
    it claimed to hold.
    """
    if not snapshot.snapshot_hash:
        return False
    return calculate_snapshot_hash(snapshot) == snapshot.snapshot_hash


# ── Comparing two deliveries of one fact ─────────────────────────────

# Paths that record *when we were told*, not *what we were told*. Two
# deliveries of the same webhook legitimately differ here, so comparing
# them would make redelivery — the exact case idempotency exists for —
# look like a conflict.
DELIVERY_METADATA_PATHS = (
    ("evidence", "received_at"),
)


def _without(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Return the payload with one nested path removed."""
    if not path:
        return payload
    if not isinstance(payload, dict) or path[0] not in payload:
        return payload
    copied = dict(payload)
    if len(path) == 1:
        copied.pop(path[0], None)
        return copied
    copied[path[0]] = _without(copied[path[0]], path[1:])
    return copied


def comparable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The part of a payload that says what happened.

    Delivery metadata is stripped so the same fact arriving twice
    compares equal. The stored record keeps the first delivery's
    timestamps — we did learn it then — and only the comparison ignores
    them.
    """
    stripped: Any = payload
    for path in DELIVERY_METADATA_PATHS:
        stripped = _without(stripped, path)
    return stripped


def payloads_describe_same_fact(
    first: dict[str, Any], second: dict[str, Any],
) -> bool:
    """Whether two payloads are the same fact, redelivery aside."""
    return canonical_json_bytes(
        comparable_payload(first)
    ) == canonical_json_bytes(comparable_payload(second))
