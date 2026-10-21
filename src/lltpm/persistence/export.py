"""Inspectable export of durable history (Commit 35).

Commit 31 deliberately deferred this and said why: durable payloads are
JSON rather than pickle precisely so that a user could one day export
their journal, decisions, recommendations and evidence provenance
without reverse-engineering a schema. This is that export.

Three things make it worth having rather than just a `SELECT *`.

**It is self-describing.** Every export carries the journal head, the
hash-chain verdict and the manifest that produced the artifacts. An
export whose integrity was never checked is a pile of JSON someone has
to trust; one that says "verified through sequence 4,312" is evidence.

**It is honest about gaps.** Evidence whose source has since been
deleted is exported with that fact attached, and content dropped by
retention policy is marked as intentionally absent rather than silently
missing. An export that quietly omitted both would misrepresent the
record.

**It is read-only.** Nothing here writes, migrates, or repairs.
Exporting history must not be able to change it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from lltpm.persistence.integrity import verify_journal_hash_chain
from lltpm.persistence.models import (
    DurableEvidenceRecord,
    JournalEventType,
    JournalRecord,
    ReproducibilityManifest,
)

# The record kinds a person is most likely to want out: what was
# decided, what was recommended, and what the evidence was.
DECISION_EVENTS = frozenset({
    JournalEventType.ORGANIZATIONAL_DECISION_RECORDED,
    JournalEventType.ORGANIZATIONAL_DECISION_REOPENED,
    JournalEventType.ORGANIZATIONAL_DECISION_SUPERSEDED,
    JournalEventType.AUTONOMOUS_DECISION_FINALIZED,
    JournalEventType.AUTONOMOUS_DECISION_BLOCKED,
})

RECOMMENDATION_EVENTS = frozenset({
    JournalEventType.AGENT_RECOMMENDATION_PUBLISHED,
    JournalEventType.AGENT_RECOMMENDATION_INVALIDATED,
    JournalEventType.HUMAN_RECOMMENDATION_VALIDATED,
    JournalEventType.HUMAN_RECOMMENDATION_MODIFIED,
    JournalEventType.HUMAN_RECOMMENDATION_REJECTED,
    JournalEventType.HUMAN_RECOMMENDATION_DEFERRED,
})

POLICY_EVENTS = frozenset({
    JournalEventType.AUTHORITY_POLICY_CHANGED,
    JournalEventType.AUTONOMY_KILL_SWITCH_CHANGED,
})


class ExportIntegrity(BaseModel):
    """Whether the history in this export verified.

    Carried with the data rather than reported separately, so an export
    cannot be passed around having lost the fact that its chain was
    broken.
    """
    journal_head_sequence: int = 0
    hash_chain_valid: bool = True
    verification_explanation: str = ""
    corrupted_at_sequence: int | None = None
    #: The head hash, so a recipient can anchor this export against a
    #: value they were given separately. An export that carried only
    #: "the chain verified" would be re-verifiable against itself and
    #: nothing else.
    journal_head_hash: str | None = None


class EvidenceProvenance(BaseModel):
    """Where one piece of evidence came from, and whether it still can.

    ``source_available`` going false does not invalidate the evidence —
    it was real and it was used. It means the original artifact can no
    longer be fetched, which a reader deserves to know.
    """
    evidence_id: str
    source_system: str
    source_type: str
    source_id: str
    source_pointer: str | None = None
    content_hash: str | None = None
    observed_at: datetime
    received_at: datetime
    project_ids: list[str] = Field(default_factory=list)
    retention_class: str = ""
    content_present: bool = True
    content_omitted_by_retention: bool = False
    source_available: bool = True
    source_state: str | None = None


class JournalExport(BaseModel):
    """A complete, self-describing export of durable history."""
    organization_id: str | None = None
    exported_at: datetime
    integrity: ExportIntegrity
    manifest: ReproducibilityManifest | None = None
    record_count: int = 0
    records: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    policy_history: list[dict[str, Any]] = Field(default_factory=list)
    evidence_provenance: list[EvidenceProvenance] = Field(
        default_factory=list
    )
    notes: list[str] = Field(default_factory=list)

    @property
    def trustworthy(self) -> bool:
        return self.integrity.hash_chain_valid


def _records_for(
    *, journal: Any, organization_id: str | None,
) -> list[JournalRecord]:
    records = journal.read_from(global_sequence=1)
    if organization_id is None:
        return records
    return [
        r for r in records
        if r.organization_id in (None, organization_id)
    ]


def _as_row(record: JournalRecord) -> dict[str, Any]:
    """One record as a flat, readable JSON object."""
    return {
        "global_sequence": record.position.global_sequence,
        "stream_id": record.position.stream_id,
        "stream_sequence": record.position.stream_sequence,
        "record_id": record.id,
        "category": record.category.value,
        "event_type": record.event_type.value,
        "organization_id": record.organization_id,
        "project_ids": list(record.project_ids),
        "occurred_at": record.occurred_at.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "causal_event_id": record.causal_event_id,
        "idempotency_key": record.idempotency_key,
        "payload_schema_version": record.payload_schema_version,
        "payload": record.payload,
        "evidence_ids": list(record.evidence_ids),
        "record_hash": record.record_hash,
        "previous_record_hash": record.previous_record_hash,
        "runtime_revision_id": record.runtime_revision_id,
    }


def source_availability(
    records: list[JournalRecord],
) -> dict[str, str]:
    """Latest known availability per evidence id.

    Later statements win, so an evidence item deleted and then restored
    reads as available.
    """
    latest: dict[str, tuple[datetime, str]] = {}
    for record in records:
        if record.event_type != JournalEventType.SOURCE_AVAILABILITY_CHANGED:
            continue
        evidence_id = str(record.payload.get("evidence_id"))
        availability = str(record.payload.get("availability", "available"))
        current = latest.get(evidence_id)
        if current is None or record.occurred_at >= current[0]:
            latest[evidence_id] = (record.occurred_at, availability)
    return {
        evidence_id: availability
        for evidence_id, (_, availability) in latest.items()
    }


def export_evidence_provenance(
    *,
    journal: Any,
    organization_id: str | None = None,
) -> list[EvidenceProvenance]:
    """Where every piece of evidence came from.

    The provenance trail Commit 31 promised: enough to audit what the
    organization concluded and from what, without the source systems
    being reachable.
    """
    records = _records_for(journal=journal, organization_id=organization_id)
    availability = source_availability(records)

    provenance: list[EvidenceProvenance] = []
    for record in records:
        if record.event_type != JournalEventType.EVIDENCE_ACCEPTED:
            continue
        try:
            evidence = DurableEvidenceRecord.model_validate(
                record.payload["evidence"]
            )
        except (KeyError, ValueError):
            # A corrupt or hand-edited payload. Exporting a damaged
            # journal is precisely a case this has to survive, and the
            # chain verdict already reports the damage — skipping the
            # unreadable row beats refusing to export anything.
            continue
        state = availability.get(evidence.evidence_id)
        provenance.append(
            EvidenceProvenance(
                evidence_id=evidence.evidence_id,
                source_system=evidence.source_system,
                source_type=evidence.source_type,
                source_id=evidence.source_id,
                source_pointer=evidence.source_pointer,
                content_hash=evidence.content_hash,
                observed_at=evidence.observed_at,
                received_at=evidence.received_at,
                project_ids=list(evidence.project_ids),
                retention_class=evidence.retention_class,
                content_present=evidence.normalized_content is not None,
                content_omitted_by_retention=(
                    evidence.content_omitted_by_retention
                ),
                source_available=state in (None, "available"),
                source_state=state,
            )
        )
    return provenance


def export_journal(
    *,
    journal: Any,
    organization_id: str | None = None,
    exported_at: datetime,
    manifest: ReproducibilityManifest | None = None,
    include_payloads: bool = True,
) -> JournalExport:
    """Export durable history, with its integrity verdict attached.

    Read-only. Verification runs over the whole chain rather than the
    filtered selection, because a break anywhere means the history this
    export came from is not sound.
    """
    all_records = journal.read_from(global_sequence=1)
    chain = verify_journal_hash_chain(all_records)
    head = journal.latest_position()

    records = _records_for(journal=journal, organization_id=organization_id)
    rows = [_as_row(r) for r in records]
    if not include_payloads:
        rows = [{**row, "payload": {}} for row in rows]

    notes: list[str] = []
    if not chain.valid:
        notes.append(
            "The journal failed hash-chain verification, so this export "
            "describes history that has been altered or truncated. It is "
            "exported as found and not repaired."
        )

    provenance = export_evidence_provenance(
        journal=journal, organization_id=organization_id,
    )
    unavailable = [p for p in provenance if not p.source_available]
    if unavailable:
        notes.append(
            f"{len(unavailable)} evidence item(s) were used at the time but "
            f"their original sources can no longer be retrieved."
        )
    withheld = [p for p in provenance if p.content_omitted_by_retention]
    if withheld:
        notes.append(
            f"{len(withheld)} evidence item(s) were retained without "
            f"content by policy; the omission is intentional, not loss."
        )

    return JournalExport(
        organization_id=organization_id,
        exported_at=exported_at,
        integrity=ExportIntegrity(
            journal_head_sequence=head.global_sequence if head else 0,
            hash_chain_valid=chain.valid,
            verification_explanation=chain.explanation,
            corrupted_at_sequence=(
                chain.corrupted_at_sequence or chain.broken_link_at_sequence
            ),
            journal_head_hash=chain.head_record_hash,
        ),
        manifest=manifest,
        record_count=len(rows),
        records=rows,
        decisions=[
            _as_row(r) for r in records if r.event_type in DECISION_EVENTS
        ],
        recommendations=[
            _as_row(r) for r in records
            if r.event_type in RECOMMENDATION_EVENTS
        ],
        policy_history=[
            _as_row(r) for r in records if r.event_type in POLICY_EVENTS
        ],
        evidence_provenance=provenance,
        notes=notes,
    )


def export_decision_history(
    *,
    journal: Any,
    decision_id: str,
    organization_id: str | None = None,
) -> list[dict[str, Any]]:
    """Everything that ever happened to one decision, in order.

    Recorded, reopened, superseded — the corrections are separate
    records, so the trail shows what happened rather than only where it
    ended up.
    """
    records = _records_for(journal=journal, organization_id=organization_id)
    matching = [
        r for r in records
        if r.event_type in DECISION_EVENTS
        and str(r.payload.get("decision_id")) == decision_id
    ]
    return [
        _as_row(r)
        for r in sorted(
            matching,
            key=lambda r: (r.occurred_at, r.position.global_sequence),
        )
    ]
