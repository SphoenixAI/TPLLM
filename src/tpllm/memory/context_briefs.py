"""Audience-aware context compression.

The same underlying ontology produces different compression for an
engineer, a pod lead, a director, or an executive — emphasis and level
of detail change; the facts never do. The latest conclusive decision,
positions, and roles are identical across audiences.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from tpllm.coordination.decisions import get_latest_conclusive_decision
from tpllm.coordination.models import (
    ActionRequest,
    DecisionPosition,
    OrganizationalDecisionRecord,
    WorkstreamState,
)
from tpllm.intelligence.project_understanding import (
    ProjectUnderstandingSnapshot,
)


class OrganizationalContextBrief(BaseModel):
    id: str
    project_id: str
    workstream_id: str | None
    audience_role: str
    why_this_matters: str
    current_state: str
    latest_conclusive_decision: str | None
    current_team_positions: list[str] = Field(default_factory=list)
    request_for_action: str | None
    requester_role: str | None
    decision_authority_role: str | None
    last_relevant_meeting_at: datetime | None
    last_relevant_meeting_summary: str | None
    product_value: str | None
    consequences_of_delay: list[str] = Field(default_factory=list)
    relevant_kpi_summaries: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float


_TECHNICAL_AUDIENCES = {"engineer", "tech_lead", "staff_engineer"}


def build_organizational_context_brief(
    *,
    audience_role: str,
    workstream: WorkstreamState,
    decisions: list[OrganizationalDecisionRecord],
    positions: list[DecisionPosition],
    request: ActionRequest | None = None,
    understanding: ProjectUnderstandingSnapshot | None = None,
    kpi_summaries: list[str] | None = None,
    consequences_of_delay: list[str] | None = None,
    last_meeting_at: datetime | None = None,
    last_meeting_summary: str | None = None,
) -> OrganizationalContextBrief:
    """Compress the workstream's reality for one audience. Detail depth
    varies by role; the facts (decision, positions, roles) do not."""
    latest = get_latest_conclusive_decision(
        decisions=decisions,
        workstream_id=workstream.workstream_id,
        as_of=workstream.as_of,
    )

    # Facts — identical for every audience.
    latest_conclusion = latest.conclusion if latest else None
    position_lines = [
        f"{p.actor.team_name or p.actor.team_id or 'team unknown'} "
        f"({p.actor.role or 'role unknown'}): {p.statement}"
        for p in positions
    ]
    requester_role = request.requested_by.role if request else None
    authority_role = None
    if request is not None and request.preferred_recipient is not None:
        authority_role = request.preferred_recipient.role
    elif latest is not None and latest.decision_authority is not None:
        authority_role = latest.decision_authority.role

    # Emphasis — varies by audience without altering facts.
    base_state = (
        f"{workstream.current_health.value} — {workstream.key_status_signal}"
    )
    if audience_role.lower() in _TECHNICAL_AUDIENCES:
        technical_detail = ""
        if understanding is not None and (
            understanding.technical_mechanism.mechanism_summary
        ):
            technical_detail = (
                f" Mechanism: "
                f"{understanding.technical_mechanism.mechanism_summary}."
            )
        current_state = base_state + technical_detail
    else:
        current_state = base_state

    return OrganizationalContextBrief(
        id=f"brief-{workstream.workstream_id}-{audience_role}",
        project_id=workstream.project_id,
        workstream_id=workstream.workstream_id,
        audience_role=audience_role,
        why_this_matters=(
            workstream.product_value
            or workstream.objective.objective
        ),
        current_state=current_state,
        latest_conclusive_decision=latest_conclusion,
        current_team_positions=position_lines,
        request_for_action=request.requested_action if request else None,
        requester_role=requester_role,
        decision_authority_role=authority_role,
        last_relevant_meeting_at=last_meeting_at,
        last_relevant_meeting_summary=last_meeting_summary,
        product_value=workstream.product_value,
        consequences_of_delay=list(consequences_of_delay or []),
        relevant_kpi_summaries=list(kpi_summaries or []),
        evidence_ids=list(workstream.evidence_ids),
        confidence=workstream.health_confidence,
    )
