"""Domain inputs for the live runtime, read from durable history
.

The orchestrator schedules recomputation; the engines underneath it need
actual organizational inputs — source records to extract claims from,
temporal events to reconstruct state from, capacity windows to detect
bottlenecks against. Until now those inputs existed only in test
fixtures, which is why ten packages were reachable from the test suite
and from nothing else.

This module supplies them from the journal, which is the right source
for a specific reason: the journal already *is* the durable record of
what the organization learned, so a data source reading it inherits
the journal's guarantees for free. Reconstruction needs no live Slack, no
reachable Jira, and no second copy of the evidence — and a projection
rebuilt from the journal reads exactly what the live pass read.

The genuinely new piece here is the evidence-to-temporal-event mapping.
The temporal layer defines bitemporal truth and persistence stores
normalized evidence, but nothing connected them: ``reconstruct_project_state``
wants ``TemporalEvent``s and the journal holds ``DurableEvidenceRecord``s.
That mapping is below, and it is careful about the one thing that
matters — ``occurred_at`` is reality time and ``known_at`` is knowledge
time, and they are never collapsed into each other.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from tpllm.integrations.envelopes import EvidenceEnvelope
from tpllm.persistence.models import (
    DurableEvidenceRecord,
    JournalEventType,
    JournalRecord,
)
from tpllm.runtime.durability import durable_record_to_envelope
from tpllm.temporal.models import TemporalEvent, TemporalEventType

# How an ingested event class becomes an observed temporal event. An
# event class with no entry here is still evidence — it just does not
# assert a specific state transition, so it lands as EVIDENCE_OBSERVED
# rather than being invented into something more specific.
EVENT_CLASS_TO_TEMPORAL_TYPE: dict[str, TemporalEventType] = {
    "kpi_observation": TemporalEventType.KPI_OBSERVED,
    "decision_proposed": TemporalEventType.DECISION_PROPOSED,
    "decision_conclusive": TemporalEventType.DECISION_CONCLUSIVE,
    "decision_reopened": TemporalEventType.DECISION_REOPENED,
    "blocker_opened": TemporalEventType.BLOCKER_OPENED,
    "blocker_resolved": TemporalEventType.BLOCKER_RESOLVED,
    "action_requested": TemporalEventType.ACTION_REQUESTED,
    "action_resolved": TemporalEventType.ACTION_RESOLVED,
    "outcome_observed": TemporalEventType.OUTCOME_OBSERVED,
    "architecture_evidence": TemporalEventType.CLAIM_SUPPORTED,
    "meeting_transcript": TemporalEventType.EVIDENCE_OBSERVED,
    "supporting_evidence": TemporalEventType.CLAIM_SUPPORTED,
    "chat_message": TemporalEventType.EVIDENCE_OBSERVED,
}

# Journal records that describe an organizational transition directly,
# rather than describing a piece of evidence about one.
_JOURNAL_TEMPORAL_TYPES: dict[JournalEventType, TemporalEventType] = {
    JournalEventType.ORGANIZATIONAL_DECISION_RECORDED: (
        TemporalEventType.DECISION_CONCLUSIVE
    ),
    JournalEventType.ORGANIZATIONAL_DECISION_REOPENED: (
        TemporalEventType.DECISION_REOPENED
    ),
    JournalEventType.ORGANIZATIONAL_DECISION_SUPERSEDED: (
        TemporalEventType.DECISION_SUPERSEDED
    ),
    JournalEventType.AUTONOMOUS_DECISION_FINALIZED: (
        TemporalEventType.DECISION_CONCLUSIVE
    ),
    JournalEventType.AGENT_RECOMMENDATION_PUBLISHED: (
        TemporalEventType.RECOMMENDATION_CREATED
    ),
    JournalEventType.AGENT_RECOMMENDATION_INVALIDATED: (
        TemporalEventType.RECOMMENDATION_EXPIRED
    ),
    JournalEventType.OUTCOME_RECORDED: TemporalEventType.OUTCOME_OBSERVED,
}


def _subject_entity_ids(metadata: dict[str, Any]) -> list[str]:
    """Whatever the event is about, for temporal supersession."""
    found: list[str] = []
    for key in (
        "subject_keys", "decision_ids", "kpi_ids", "workstream_ids",
        "canonical_entity_ids",
    ):
        value = metadata.get(key)
        if isinstance(value, (list, tuple)):
            found.extend(str(v) for v in value)
        elif value is not None:
            found.append(str(value))
    return found


def evidence_to_temporal_event(
    evidence: DurableEvidenceRecord,
    *,
    project_id: str,
) -> TemporalEvent | None:
    """Turn one durable evidence record into an observed temporal event.

    The bitemporal split is the whole point: ``occurred_at`` is when the
    thing was true and ``known_at`` is when the organization learned it.
    A 09:58 meeting delivered at 10:08 keeps both, which is what lets
    reconstruction recover what was known at 10:00 without leaking what
    arrived later.

    Returns ``None`` for evidence that belongs to another project, so a
    caller cannot accidentally fold one project's history into another's.
    """
    if project_id not in evidence.project_ids:
        return None

    event_type = EVENT_CLASS_TO_TEMPORAL_TYPE.get(
        evidence.event_type, TemporalEventType.EVIDENCE_OBSERVED,
    )
    # known_at can never precede occurred_at; a record whose clocks
    # disagree is clamped rather than rejected, because the evidence is
    # real even when a source system's timestamp is not.
    known_at = max(evidence.received_at, evidence.observed_at)

    return TemporalEvent(
        id=f"tev-{evidence.evidence_id}",
        project_id=project_id,
        event_type=event_type,
        entity_ids=_subject_entity_ids(evidence.metadata),
        occurred_at=evidence.observed_at,
        known_at=known_at,
        source_object_id=evidence.evidence_id,
        evidence_ids=[evidence.evidence_id],
        summary=(
            evidence.title
            or (evidence.normalized_content or "")[:200]
            or f"{evidence.event_type} from {evidence.source_system}"
        ),
    )


def journal_record_to_temporal_event(
    record: JournalRecord,
    *,
    project_id: str,
) -> TemporalEvent | None:
    """Turn a decision, recommendation, or outcome record into a
    temporal event.

    These are transitions the organization made, not evidence about
    transitions, so they map directly rather than through the evidence
    path.
    """
    event_type = _JOURNAL_TEMPORAL_TYPES.get(record.event_type)
    if event_type is None:
        return None
    if record.project_ids and project_id not in record.project_ids:
        return None

    payload = record.payload
    subject = (
        payload.get("decision_id")
        or payload.get("recommendation_id")
        or payload.get("outcome_id")
        or record.id
    )
    return TemporalEvent(
        id=f"tev-{record.id}",
        project_id=project_id,
        event_type=event_type,
        entity_ids=[str(subject)],
        occurred_at=record.occurred_at,
        known_at=max(record.recorded_at, record.occurred_at),
        source_object_id=record.id,
        evidence_ids=list(record.evidence_ids),
        summary=_summarize(record),
    )


def _summarize(record: JournalRecord) -> str:
    payload = record.payload
    if record.event_type in (
        JournalEventType.ORGANIZATIONAL_DECISION_RECORDED,
        JournalEventType.AUTONOMOUS_DECISION_FINALIZED,
    ):
        return (
            f"{payload.get('decision_id')} concluded "
            f"{payload.get('conclusion') or payload.get('recommendation_id')}"
        )
    if record.event_type == JournalEventType.ORGANIZATIONAL_DECISION_REOPENED:
        return (
            f"{payload.get('decision_id')} reopened: "
            f"{payload.get('reason', 'no reason recorded')}"
        )
    if record.event_type == JournalEventType.OUTCOME_RECORDED:
        return (
            f"{payload.get('outcome_id')} observed "
            f"{payload.get('observed_value')}"
        )
    if record.event_type == JournalEventType.AGENT_RECOMMENDATION_PUBLISHED:
        return (
            f"{payload.get('recommendation_id')} recommended "
            f"{payload.get('recommended_option_id')} at confidence "
            f"{payload.get('confidence')}"
        )
    return f"{record.event_type.value} on {record.id}"


# ── The port ─────────────────────────────────────────────────────────


@runtime_checkable
class OrganizationalDataSource(Protocol):
    """Supplies the domain inputs the engines need.

    A port rather than a concrete reader so a host can serve these from
    somewhere other than the journal — a cache, a warm projection, a
    different store — without the services layer changing.
    """

    def project_ids(self) -> list[str]:
        ...

    def source_records(
        self, *, project_id: str, as_of: datetime,
    ) -> list[Any]:
        ...

    def temporal_events(
        self, *, project_id: str, as_of: datetime,
    ) -> list[TemporalEvent]:
        ...

    def evidence_ids(self, *, project_id: str, as_of: datetime) -> list[str]:
        ...


class DomainInputs(BaseModel):
    """Inputs a caller can supply that the journal does not hold.

    Capacity windows, demands, and shared factors describe the
    organization's structure rather than its event history, so they are
    configured rather than derived. Keeping them here — instead of
    inventing them inside the services layer — means a bottleneck is
    only ever reported from a real declared constraint.
    """
    capacity_windows: list[Any] = Field(default_factory=list)
    capacity_demands: list[Any] = Field(default_factory=list)
    shared_factors: list[Any] = Field(default_factory=list)
    launch_definitions: dict[str, Any] = Field(default_factory=dict)
    approval_requirements: dict[str, list[str]] = Field(default_factory=dict)
    service_usage: dict[str, list[str]] = Field(default_factory=dict)
    project_goals: list[Any] = Field(default_factory=list)
    ontology_records: list[Any] = Field(default_factory=list)
    structural_artifacts: list[Any] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class JournalBackedDataSource:
    """Reads domain inputs out of the durable journal.

    Everything here derives from ``EVIDENCE_ACCEPTED`` and the
    transition records beside it, so the inputs a replay sees are
    identical to the inputs the live pass saw. That identity is what
    makes the replay equivalence claim meaningful rather than a
    comparison between two different pipelines.
    """

    def __init__(
        self,
        *,
        journal: Any,
        organization_id: str | None = None,
        inputs: DomainInputs | None = None,
    ):
        self._journal = journal
        self._organization_id = organization_id
        self.inputs = inputs or DomainInputs()

    # ── Reading history ──────────────────────────────────────────────

    def _records(self) -> list[JournalRecord]:
        records = self._journal.read_from(global_sequence=1)
        if self._organization_id is None:
            return records
        return [
            r for r in records
            if r.organization_id in (None, self._organization_id)
        ]

    def durable_evidence(
        self, *, project_id: str | None = None, as_of: datetime | None = None,
    ) -> list[DurableEvidenceRecord]:
        """Every piece of evidence the organization accepted.

        Filtered by knowledge time, never by arrival order: an event
        that happened before ``as_of`` but was only learned afterwards
        is correctly excluded, which is what stops a reconstruction from
        knowing things it could not have known.
        """
        found: list[DurableEvidenceRecord] = []
        for record in self._records():
            if record.event_type != JournalEventType.EVIDENCE_ACCEPTED:
                continue
            evidence = DurableEvidenceRecord.model_validate(
                record.payload["evidence"]
            )
            if project_id is not None and project_id not in (
                evidence.project_ids
            ):
                continue
            if as_of is not None and evidence.received_at > as_of:
                continue
            found.append(evidence)
        return found

    def envelopes(
        self, *, project_id: str | None = None, as_of: datetime | None = None,
    ) -> list[EvidenceEnvelope]:
        """Evidence back in envelope form.

        Reuses the journal's own reconstruction rather than a second decoder,
        so there is exactly one definition of what a durable record
        means.
        """
        return [
            durable_record_to_envelope(evidence)
            for evidence in self.durable_evidence(
                project_id=project_id, as_of=as_of,
            )
        ]

    # ── The port ─────────────────────────────────────────────────────

    def project_ids(self) -> list[str]:
        found: set[str] = set()
        for record in self._records():
            found.update(record.project_ids)
        return sorted(found)

    def source_records(
        self, *, project_id: str, as_of: datetime,
    ) -> list[Any]:
        """Records the understanding engine can extract claims from.

        Envelopes already present the duck-typed shape
        ``normalize_project_evidence`` reads, so no adapter is needed —
        and evidence whose content was dropped by retention policy is
        excluded rather than passed through as an empty claim.
        """
        return [
            envelope for envelope in self.envelopes(
                project_id=project_id, as_of=as_of,
            )
            if envelope.content
        ]

    def temporal_events(
        self, *, project_id: str, as_of: datetime,
    ) -> list[TemporalEvent]:
        """Observed history for one project, in both time dimensions.

        Evidence and transition records are both folded in, ordered by
        reality time. Ordering here is a convenience; reconstruction filters
        by knowledge time itself, so a late arrival cannot change what
        an earlier reconstruction knew.
        """
        events: list[TemporalEvent] = []
        for evidence in self.durable_evidence(
            project_id=project_id, as_of=as_of,
        ):
            event = evidence_to_temporal_event(
                evidence, project_id=project_id,
            )
            if event is not None:
                events.append(event)

        for record in self._records():
            if record.recorded_at > as_of:
                continue
            event = journal_record_to_temporal_event(
                record, project_id=project_id,
            )
            if event is not None:
                events.append(event)

        return sorted(events, key=lambda e: (e.occurred_at, e.known_at, e.id))

    def evidence_ids(self, *, project_id: str, as_of: datetime) -> list[str]:
        return [
            e.evidence_id for e in self.durable_evidence(
                project_id=project_id, as_of=as_of,
            )
        ]

    # ── Configured structure ─────────────────────────────────────────

    def capacity_windows(self) -> list[Any]:
        return list(self.inputs.capacity_windows)

    def capacity_demands(self) -> list[Any]:
        return list(self.inputs.capacity_demands)

    def shared_factors(self) -> list[Any]:
        return list(self.inputs.shared_factors)

    def launch_definition(self, project_id: str) -> Any | None:
        return self.inputs.launch_definitions.get(project_id)

    def unavailable_evidence_ids(self) -> list[str]:
        """Evidence whose source has since gone away.

        Reported rather than hidden: the evidence was real and was used,
        and a reader deserves to know the original is no longer
        retrievable.
        """
        unavailable: list[str] = []
        for record in self._records():
            if record.event_type != (
                JournalEventType.SOURCE_AVAILABILITY_CHANGED
            ):
                continue
            if record.payload.get("availability") != "available":
                unavailable.append(str(record.payload.get("evidence_id")))
        return unavailable
