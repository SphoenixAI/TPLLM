"""Runtime ingest: normalize, deduplicate, order (Commit 30).

Real integrations do not deliver events in the order things happened. A
meeting webhook can land ten minutes after the approval it preceded, and
a system that trusted arrival order would helpfully move an approved
decision back to pending.

The rule here is one sentence: **arrival order is not temporal
authority**. Ordering uses Commit 23's bitemporal semantics — reality
time first, knowledge time only to break ties — and a late event is
never dismissed as irrelevant merely for being late. It may still change
what the organization understands about *why* something happened, even
when it cannot change what is true now.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from lltpm.integrations.envelopes import EvidenceEnvelope
from lltpm.runtime.models import RuntimeEvidenceEvent

# Events landing inside this window of each other are treated as
# simultaneous for conflict purposes; processing order must not decide
# between them.
DEFAULT_CONTEST_WINDOW = timedelta(minutes=5)

_LIST_METADATA_FIELDS = {
    "subject_keys": "subject_keys",
    "canonical_entity_ids": "canonical_entity_ids",
    "shared_resource_ids": "shared_resource_ids",
    "kpi_ids": "kpi_ids",
    "workstream_ids": "workstream_ids",
    "decision_ids": "decision_ids",
    "root_evidence_ids": "root_evidence_ids",
}


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def envelope_to_runtime_event(
    envelope: EvidenceEnvelope,
    *,
    causal_event_id: str | None = None,
) -> RuntimeEvidenceEvent:
    """Wrap a Commit 18 envelope for the orchestration path.

    The envelope is not replaced — it remains the record of what a tool
    said. This adds only what scheduling needs: what the event is about,
    which projects it touches, and which root cause it belongs to.
    """
    metadata = envelope.metadata or {}
    fields = {
        field: _string_list(metadata.get(key))
        for field, key in _LIST_METADATA_FIELDS.items()
    }
    if not fields["root_evidence_ids"]:
        fields["root_evidence_ids"] = [envelope.id]

    material_hint = metadata.get("material_hint")
    if material_hint is not None:
        material_hint = bool(material_hint)

    return RuntimeEvidenceEvent(
        envelope_id=envelope.id,
        project_ids=list(envelope.project_ids),
        occurred_at=envelope.observed_at,
        received_at=envelope.received_at,
        event_class=envelope.event_type,
        material_hint=material_hint,
        causal_event_id=(
            causal_event_id
            or str(metadata.get("causal_event_id") or envelope.id)
        ),
        asserted_state=(
            str(metadata["asserted_state"])
            if metadata.get("asserted_state") is not None else None
        ),
        idempotency_key=envelope.idempotency_key,
        **fields,
    )


def deduplicate_events(
    events: list[RuntimeEvidenceEvent],
    *,
    known_keys: set[str] | None = None,
) -> tuple[list[RuntimeEvidenceEvent], list[RuntimeEvidenceEvent]]:
    """Split events into fresh and already-seen.

    Deduplication is by the envelope's idempotency key, so the same
    webhook delivered twice is the same event — replaying it produces no
    second decision, no second scenario, and no second notification.
    """
    seen = set(known_keys or set())
    fresh: list[RuntimeEvidenceEvent] = []
    duplicates: list[RuntimeEvidenceEvent] = []
    for event in events:
        key = event.idempotency_key or event.envelope_id
        if key in seen:
            duplicates.append(event)
            continue
        seen.add(key)
        fresh.append(event)
    return fresh, duplicates


def order_runtime_events(
    events: list[RuntimeEvidenceEvent],
) -> list[RuntimeEvidenceEvent]:
    """Order by when things happened, not by when we heard.

    Reality time leads; knowledge time and the envelope id only break
    ties, so the ordering is total and deterministic.
    """
    return sorted(
        events,
        key=lambda e: (e.occurred_at, e.received_at, e.envelope_id),
    )


def assert_no_future_events(
    events: list[RuntimeEvidenceEvent], *, now: datetime,
) -> None:
    """Nothing that has not happened yet may enter observed state.

    A scheduled freeze is a commitment, not a temporal fact; it becomes
    one on Friday, when evidence says it did.
    """
    for event in events:
        if event.occurred_at > now:
            raise ValueError(
                f"Event '{event.envelope_id}' claims to have occurred at "
                f"{event.occurred_at.isoformat()}, after now "
                f"({now.isoformat()}). History is not prediction."
            )


def partition_planned_events(
    events: list[RuntimeEvidenceEvent],
) -> tuple[list[RuntimeEvidenceEvent], list[RuntimeEvidenceEvent]]:
    """Separate observations from plans.

    Plans belong to milestones, commitments, and scenario candidates.
    Letting one become an observed temporal event would put a prediction
    into the historical record.
    """
    observed = [e for e in events if not e.is_planned]
    planned = [e for e in events if e.is_planned]
    return observed, planned


def is_historical_insertion(
    event: RuntimeEvidenceEvent, *, latest_known_occurred_at: datetime | None,
) -> bool:
    """Did this event happen before the newest thing we already knew?"""
    if latest_known_occurred_at is None:
        return False
    return event.occurred_at < latest_known_occurred_at


def superseding_subject_keys(
    event: RuntimeEvidenceEvent,
    *,
    subject_latest_occurred_at: dict[str, datetime],
) -> list[str]:
    """Subjects where something newer has already been said.

    A 09:58 transcript recording that the approval was still pending
    arrives after the 10:03 approval. The approval is newer on the same
    subject, so the transcript enriches lineage and does not reopen the
    decision.
    """
    superseded: list[str] = []
    for key in sorted(set(event.subject_keys)):
        latest = subject_latest_occurred_at.get(key)
        if latest is not None and latest > event.occurred_at:
            superseded.append(key)
    return superseded


def classify_historical_effect(
    event: RuntimeEvidenceEvent,
    *,
    latest_known_occurred_at: datetime | None,
    subject_latest_occurred_at: dict[str, datetime],
) -> str:
    """One of ``current``, ``lineage_only``, or ``historical_current``.

    ``lineage_only`` is the interesting case: the event is genuinely old
    news for present purposes, but it still belongs in the record and can
    still explain a decision made later.
    """
    if not is_historical_insertion(
        event, latest_known_occurred_at=latest_known_occurred_at,
    ):
        return "current"
    if not event.subject_keys:
        # Nothing later contradicts it, but nothing identifies what it is
        # about either — treat it as historical enrichment.
        return "lineage_only"
    superseded = superseding_subject_keys(
        event, subject_latest_occurred_at=subject_latest_occurred_at,
    )
    if len(superseded) == len(set(event.subject_keys)):
        return "lineage_only"
    return "historical_current"


def detect_contested_subjects(
    events: list[RuntimeEvidenceEvent],
    *,
    window: timedelta = DEFAULT_CONTEST_WINDOW,
) -> list[str]:
    """Subjects where near-simultaneous evidence disagrees.

    One team reports the approval complete; another reports it pending,
    minutes apart. Neither arrival order nor processing order is evidence
    about which is right, so the subject stays contested until authority
    or later evidence resolves it.
    """
    by_subject: dict[str, list[RuntimeEvidenceEvent]] = {}
    for event in events:
        if event.asserted_state is None:
            continue
        for key in event.subject_keys:
            by_subject.setdefault(key, []).append(event)

    contested: list[str] = []
    for key in sorted(by_subject):
        ordered = order_runtime_events(by_subject[key])
        for index, event in enumerate(ordered):
            for other in ordered[index + 1:]:
                if other.occurred_at - event.occurred_at > window:
                    break
                if other.asserted_state != event.asserted_state:
                    contested.append(key)
                    break
            if key in contested:
                break
    return contested


def latest_occurred_at(
    events: list[RuntimeEvidenceEvent],
) -> datetime | None:
    return max((e.occurred_at for e in events), default=None)


def subject_latest_map(
    events: list[RuntimeEvidenceEvent],
) -> dict[str, datetime]:
    """Newest reality time seen per subject."""
    latest: dict[str, datetime] = {}
    for event in events:
        for key in event.subject_keys:
            current = latest.get(key)
            if current is None or event.occurred_at > current:
                latest[key] = event.occurred_at
    return latest
