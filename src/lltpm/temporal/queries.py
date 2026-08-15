"""Temporal queries (Commit 23).

``what_was_true_at`` and ``what_was_known_at`` deliberately return
different results — that divergence is the feature, not a bug.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from lltpm.temporal.lineage import trace_decision_lineage
from lltpm.temporal.models import (
    DecisionLineageNode,
    ProjectTemporalState,
    TemporalEvent,
)
from lltpm.temporal.state import (
    events_known_at,
    events_true_at,
    reconstruct_project_state,
)


class TemporalDelta(BaseModel):
    """What materially changed between two points in knowledge time."""
    project_id: str
    from_as_of: datetime
    to_as_of: datetime
    newly_known_event_ids: list[str] = Field(default_factory=list)
    claims_added: list[str] = Field(default_factory=list)
    claims_removed: list[str] = Field(default_factory=list)
    claims_newly_contested: list[str] = Field(default_factory=list)
    decisions_added: list[str] = Field(default_factory=list)
    decisions_removed: list[str] = Field(default_factory=list)
    blockers_opened: list[str] = Field(default_factory=list)
    blockers_resolved: list[str] = Field(default_factory=list)
    material: bool = False
    explanation: str = ""


def what_was_true_at(
    *,
    events: list[TemporalEvent],
    as_of: datetime,
    project_id: str | None = None,
) -> list[TemporalEvent]:
    """Reality time: everything that had actually happened by ``as_of``,
    including things nobody had noticed yet."""
    scoped = (
        [e for e in events if e.project_id == project_id]
        if project_id
        else events
    )
    return events_true_at(scoped, as_of)


def what_was_known_at(
    *,
    events: list[TemporalEvent],
    as_of: datetime,
    project_id: str | None = None,
) -> list[TemporalEvent]:
    """Knowledge time: only what the organization had learned by
    ``as_of``. This is what a forecast may use."""
    scoped = (
        [e for e in events if e.project_id == project_id]
        if project_id
        else events
    )
    return events_known_at(scoped, as_of)


def what_was_authoritative_at(
    *,
    nodes: list[DecisionLineageNode],
    as_of: datetime,
) -> str | None:
    return trace_decision_lineage(
        nodes=nodes, as_of=as_of,
    ).current_authoritative_decision_id


def what_changed_between(
    *,
    project_id: str,
    temporal_events: list[TemporalEvent],
    from_as_of: datetime,
    to_as_of: datetime,
) -> TemporalDelta:
    """Explain material change between two reconstructed states."""
    before = reconstruct_project_state(
        project_id=project_id, temporal_events=temporal_events,
        as_of=from_as_of,
    )
    after = reconstruct_project_state(
        project_id=project_id, temporal_events=temporal_events,
        as_of=to_as_of,
    )

    newly_known = [
        e.id for e in temporal_events
        if e.project_id == project_id
        and from_as_of < e.known_at <= to_as_of
    ]

    claims_added = sorted(
        set(after.active_claim_ids) - set(before.active_claim_ids)
    )
    claims_removed = sorted(
        set(before.active_claim_ids) - set(after.active_claim_ids)
    )
    newly_contested = sorted(
        set(after.contested_claim_ids) - set(before.contested_claim_ids)
    )
    decisions_added = sorted(
        set(after.active_decision_ids) - set(before.active_decision_ids)
    )
    decisions_removed = sorted(
        set(before.active_decision_ids) - set(after.active_decision_ids)
    )
    blockers_opened = sorted(
        set(after.active_blocker_ids) - set(before.active_blocker_ids)
    )
    blockers_resolved = sorted(
        set(before.active_blocker_ids) - set(after.active_blocker_ids)
    )

    changes: list[str] = []
    if decisions_added:
        changes.append(f"{len(decisions_added)} decision(s) became authoritative")
    if decisions_removed:
        changes.append(
            f"{len(decisions_removed)} decision(s) lost authority"
        )
    if newly_contested:
        changes.append(f"{len(newly_contested)} claim(s) became contested")
    if blockers_opened:
        changes.append(f"{len(blockers_opened)} blocker(s) opened")
    if blockers_resolved:
        changes.append(f"{len(blockers_resolved)} blocker(s) resolved")
    if claims_added:
        changes.append(f"{len(claims_added)} claim(s) became supported")
    if claims_removed:
        changes.append(f"{len(claims_removed)} claim(s) stopped being supported")

    material = bool(
        claims_added or claims_removed or newly_contested
        or decisions_added or decisions_removed
        or blockers_opened or blockers_resolved
    )

    explanation = (
        "; ".join(changes) + "."
        if changes
        else (
            f"{len(newly_known)} new event(s) became known but nothing "
            f"material changed."
        )
    )

    return TemporalDelta(
        project_id=project_id,
        from_as_of=from_as_of,
        to_as_of=to_as_of,
        newly_known_event_ids=newly_known,
        claims_added=claims_added,
        claims_removed=claims_removed,
        claims_newly_contested=newly_contested,
        decisions_added=decisions_added,
        decisions_removed=decisions_removed,
        blockers_opened=blockers_opened,
        blockers_resolved=blockers_resolved,
        material=material,
        explanation=explanation,
    )


def assert_no_future_leakage(
    *,
    state: ProjectTemporalState,
    events: list[TemporalEvent],
) -> None:
    """Guard: nothing known after the cutoff may appear in the state."""
    future_ids = {
        e.source_object_id for e in events
        if e.known_at > state.evidence_cutoff_at
    }
    referenced = set(
        state.active_claim_ids
        + state.contested_claim_ids
        + state.active_decision_ids
        + state.active_blocker_ids
        + state.open_action_request_ids
        + state.current_recommendation_ids
    )
    leaked = future_ids & referenced
    if leaked:
        raise ValueError(
            f"Future leakage: {sorted(leaked)} became known after "
            f"{state.evidence_cutoff_at.isoformat()}."
        )
