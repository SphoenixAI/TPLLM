"""Shared fixtures for temporal tests."""

from __future__ import annotations

from datetime import datetime, timedelta

from tpllm.temporal import (
    DecisionLineageNode,
    TemporalEvent,
    TemporalEventType,
)

PROJECT = "atlas"
T0 = datetime(2026, 9, 1, 9, 0, 0)


def at(hours: float = 0, days: float = 0) -> datetime:
    return T0 + timedelta(hours=hours, days=days)


def event(
    eid: str,
    event_type: TemporalEventType,
    subject: str,
    *,
    occurred_at: datetime | None = None,
    known_at: datetime | None = None,
    entity_ids: list[str] | None = None,
    project_id: str = PROJECT,
    summary: str = "",
) -> TemporalEvent:
    occurred = occurred_at or T0
    return TemporalEvent(
        id=eid,
        project_id=project_id,
        event_type=event_type,
        entity_ids=entity_ids or [],
        occurred_at=occurred,
        known_at=known_at or occurred,
        source_object_id=subject,
        evidence_ids=[f"ev-{eid}"],
        summary=summary or f"{event_type.value} for {subject}",
    )


def node(
    decision_id: str,
    *,
    status: str = "conclusive",
    superseded_by: str | None = None,
    reopened_at: datetime | None = None,
    effective_from: datetime | None = None,
    parent: str | None = None,
    origin: str = "human_decision",
    option: str | None = "opt-a",
) -> DecisionLineageNode:
    return DecisionLineageNode(
        decision_id=decision_id,
        parent_decision_id=parent,
        status=status,
        decision_origin=origin,
        selected_option_id=option,
        effective_from=effective_from or T0,
        superseded_by=superseded_by,
        reopened_at=reopened_at,
        evidence_ids=[f"ev-{decision_id}"],
    )
