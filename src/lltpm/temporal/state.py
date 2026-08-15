"""Historical state reconstruction (Commit 23).

Rebuilds what the organization knew at an arbitrary ``as_of``, excluding
everything learned afterward. Every scenario forecast roots here, so a
leak in this module would silently corrupt every downstream probability.
"""

from __future__ import annotations

from datetime import datetime

from lltpm.temporal.models import (
    ProjectTemporalState,
    TemporalEvent,
    TemporalEventType,
    build_state_fingerprint,
)

# Events that open a state, paired with the events that close it.
_OPEN_CLOSE_PAIRS: dict[TemporalEventType, TemporalEventType] = {
    TemporalEventType.BLOCKER_OPENED: TemporalEventType.BLOCKER_RESOLVED,
    TemporalEventType.ACTION_REQUESTED: TemporalEventType.ACTION_RESOLVED,
}

_CLAIM_OPEN = TemporalEventType.CLAIM_SUPPORTED
_CLAIM_CONTESTED = TemporalEventType.CLAIM_CONTESTED
_CLAIM_STALE = TemporalEventType.CLAIM_STALE

_DECISION_CONCLUSIVE = TemporalEventType.DECISION_CONCLUSIVE
_DECISION_SUPERSEDED = TemporalEventType.DECISION_SUPERSEDED
_DECISION_REOPENED = TemporalEventType.DECISION_REOPENED


def events_known_at(
    events: list[TemporalEvent], as_of: datetime
) -> list[TemporalEvent]:
    """Knowledge-time filter: only what had been learned by ``as_of``.

    An event that *occurred* before ``as_of`` but became *known* after it
    is excluded — that is the whole point.
    """
    known = [e for e in events if e.known_at <= as_of]
    return sorted(known, key=lambda e: (e.known_at, e.occurred_at, e.id))


def events_true_at(
    events: list[TemporalEvent], as_of: datetime
) -> list[TemporalEvent]:
    """Reality-time filter: what had actually happened by ``as_of``,
    regardless of whether anyone knew it yet."""
    occurred = [e for e in events if e.occurred_at <= as_of]
    return sorted(occurred, key=lambda e: (e.occurred_at, e.id))


def _subject_of(event: TemporalEvent) -> str:
    """The object an event is about — its source object, falling back to
    its first entity."""
    if event.source_object_id:
        return event.source_object_id
    return event.entity_ids[0] if event.entity_ids else event.id


def reconstruct_project_state(
    *,
    project_id: str,
    temporal_events: list[TemporalEvent],
    domain_objects: object = None,
    as_of: datetime,
) -> ProjectTemporalState:
    """Fold knowledge-time events into the state as of ``as_of``.

    Supersession, reopening, staleness, and contest are all respected;
    unknowns are preserved rather than resolved optimistically.
    """
    known = [
        e for e in events_known_at(temporal_events, as_of)
        if e.project_id == project_id
    ]

    supported: dict[str, bool] = {}
    contested: set[str] = set()
    stale: set[str] = set()
    conclusive: dict[str, bool] = {}
    superseded: set[str] = set()
    reopened: set[str] = set()
    recommendations: dict[str, bool] = {}
    open_blockers: dict[str, bool] = {}
    open_actions: dict[str, bool] = {}
    kpi_states: dict[str, str] = {}
    unknowns: set[str] = set()
    understanding_snapshot_id: str | None = None

    for event in known:
        subject = _subject_of(event)
        etype = event.event_type

        if etype == _CLAIM_OPEN:
            supported[subject] = True
            stale.discard(subject)
            contested.discard(subject)
        elif etype == _CLAIM_CONTESTED:
            contested.add(subject)
            supported.setdefault(subject, True)
        elif etype == _CLAIM_STALE:
            stale.add(subject)
            supported[subject] = False

        elif etype == TemporalEventType.DECISION_PROPOSED:
            conclusive.setdefault(subject, False)
        elif etype == _DECISION_CONCLUSIVE:
            conclusive[subject] = True
            reopened.discard(subject)
        elif etype == _DECISION_SUPERSEDED:
            superseded.add(subject)
        elif etype == _DECISION_REOPENED:
            reopened.add(subject)
            conclusive[subject] = False

        elif etype == TemporalEventType.RECOMMENDATION_CREATED:
            recommendations[subject] = True
        elif etype == TemporalEventType.RECOMMENDATION_EXPIRED:
            recommendations[subject] = False

        elif etype == TemporalEventType.BLOCKER_OPENED:
            open_blockers[subject] = True
        elif etype == TemporalEventType.BLOCKER_RESOLVED:
            open_blockers[subject] = False
        elif etype == TemporalEventType.ACTION_REQUESTED:
            open_actions[subject] = True
        elif etype == TemporalEventType.ACTION_RESOLVED:
            open_actions[subject] = False

        elif etype == TemporalEventType.KPI_OBSERVED:
            # Latest observation known by as_of wins.
            kpi_states[subject] = event.id

        elif etype == TemporalEventType.EVIDENCE_OBSERVED:
            if event.source_object_id.startswith("pus-"):
                understanding_snapshot_id = event.source_object_id

        for entity in event.entity_ids:
            if entity.startswith("unk-"):
                unknowns.add(entity)

    active_claims = sorted(
        cid for cid, is_supported in supported.items()
        if is_supported and cid not in stale
    )
    # A decision is active when conclusive, not superseded (unless its
    # superseder was reopened — resolved in lineage), and not reopened.
    active_decisions = sorted(
        did for did, is_conclusive in conclusive.items()
        if is_conclusive and did not in superseded and did not in reopened
    )

    parts = {
        "project": project_id,
        "as_of": as_of.isoformat(),
        "claims": active_claims,
        "contested": sorted(contested),
        "stale": sorted(stale),
        "decisions": active_decisions,
        "recommendations": sorted(
            r for r, live in recommendations.items() if live
        ),
        "blockers": sorted(b for b, o in open_blockers.items() if o),
        "actions": sorted(a for a, o in open_actions.items() if o),
        "kpis": sorted(kpi_states.values()),
        "unknowns": sorted(unknowns),
    }

    return ProjectTemporalState(
        project_id=project_id,
        as_of=as_of,
        understanding_snapshot_id=understanding_snapshot_id,
        active_claim_ids=active_claims,
        contested_claim_ids=sorted(contested),
        stale_claim_ids=sorted(stale),
        active_decision_ids=active_decisions,
        current_recommendation_ids=sorted(
            r for r, live in recommendations.items() if live
        ),
        open_action_request_ids=sorted(
            a for a, is_open in open_actions.items() if is_open
        ),
        current_kpi_state_ids=sorted(kpi_states.values()),
        active_blocker_ids=sorted(
            b for b, is_open in open_blockers.items() if is_open
        ),
        unresolved_unknown_ids=sorted(unknowns),
        evidence_cutoff_at=as_of,
        state_fingerprint=build_state_fingerprint(parts),
    )
