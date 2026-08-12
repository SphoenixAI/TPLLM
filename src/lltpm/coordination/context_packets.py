"""Compact, reusable context packets — the compressed organizational
context that travels with an ask instead of a "hey, any update?" ping."""

from __future__ import annotations

from pydantic import BaseModel, Field

from lltpm.coordination.decisions import get_latest_conclusive_decision
from lltpm.coordination.meeting_intelligence import MaterialChange
from lltpm.coordination.models import (
    DecisionPosition,
    OrganizationalDecisionRecord,
    WorkstreamState,
)


class ContextPacket(BaseModel):
    id: str
    project_id: str
    topic: str
    objective_summary: str
    product_value_summary: str | None
    current_state_summary: str
    latest_conclusive_decision: str | None
    actor_positions: list[DecisionPosition]
    what_changed_since_last_decision: list[str]
    unresolved_questions: list[str]
    related_kpi_ids: list[str]
    related_workstream_ids: list[str]
    related_commitment_ids: list[str]
    evidence_ids: list[str]
    confidence: float


def build_context_packet(
    *,
    packet_id: str,
    topic: str,
    workstream: WorkstreamState,
    decisions: list[OrganizationalDecisionRecord],
    positions: list[DecisionPosition],
    material_changes: list[MaterialChange],
    unresolved_questions: list[str],
) -> ContextPacket:
    """Compress a workstream's current organizational reality into one
    packet: objective, product value, state, latest conclusive decision,
    positions with roles, and what changed since that decision."""
    latest = get_latest_conclusive_decision(
        decisions=decisions,
        workstream_id=workstream.workstream_id,
        as_of=workstream.as_of,
    )

    changed: list[str] = []
    for change in material_changes:
        if change.workstream_id != workstream.workstream_id:
            continue
        if latest is not None and latest.decided_at is not None:
            if change.observed_at <= latest.decided_at:
                continue
        changed.append(change.after_summary)

    evidence_ids: list[str] = list(workstream.evidence_ids)
    if latest is not None:
        for eid in latest.evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)
    for position in positions:
        for eid in position.evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    return ContextPacket(
        id=packet_id,
        project_id=workstream.project_id,
        topic=topic,
        objective_summary=workstream.objective.objective,
        product_value_summary=workstream.product_value,
        current_state_summary=(
            f"{workstream.current_health.value} — {workstream.key_status_signal}"
        ),
        latest_conclusive_decision=latest.conclusion if latest else None,
        actor_positions=list(positions),
        what_changed_since_last_decision=changed,
        unresolved_questions=list(unresolved_questions),
        related_kpi_ids=list(workstream.kpi_ids),
        related_workstream_ids=[workstream.workstream_id],
        related_commitment_ids=list(workstream.commitment_ids),
        evidence_ids=evidence_ids,
        confidence=workstream.health_confidence,
    )
