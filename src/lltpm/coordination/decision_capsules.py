"""Decision capsules — a small decision should not need a large document
or a meeting to reconstruct three weeks of context.

A capsule answers: what is being requested, why, by whom, who can
decide, why they are the right authority, what was last decided, what
changed since, what each party currently believes, the options, the
impact of deciding versus delaying, and the KPI/milestone links — all
with evidence.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from lltpm.coordination.decisions import get_latest_conclusive_decision
from lltpm.coordination.models import (
    ActionRequest,
    ActorRelationType,
    DecisionPosition,
    OrganizationalActorRef,
    OrganizationalDecisionRecord,
    PositionType,
    WorkstreamState,
)
from lltpm.intelligence.launch_readiness import LaunchReadinessSnapshot
from lltpm.intelligence.project_understanding import (
    ProjectEvidence,
    ProjectUnderstandingSnapshot,
)


class DecisionOption(BaseModel):
    id: str
    label: str
    description: str
    advantages: list[str]
    risks: list[str]
    evidence_ids: list[str]


class DecisionCapsule(BaseModel):
    id: str
    project_id: str
    workstream_id: str | None
    decision_question: str
    requested_by: OrganizationalActorRef
    decision_authority: OrganizationalActorRef | None
    why_this_person_or_role: str
    current_state: str
    latest_conclusive_decision: str | None
    last_relevant_discussion_at: datetime | None
    last_relevant_discussion_summary: str | None
    actor_positions: list[DecisionPosition]
    what_changed_since_last_discussion: list[str]
    options: list[DecisionOption]
    impact_if_decided: str
    impact_if_delayed: str
    affected_milestones: list[str]
    affected_kpi_ids: list[str]
    open_disagreements: list[str]
    evidence_ids: list[str]
    confidence: float


_AUTHORITY_REASONS = {
    ActorRelationType.OWNS_SYSTEM: "owns the affected system",
    ActorRelationType.MAY_APPROVE: "holds approval authority for this scope",
    ActorRelationType.MAY_DECIDE: "holds decision authority for this scope",
    ActorRelationType.HAS_EXPERTISE: "holds relevant expertise",
    ActorRelationType.HAS_HISTORICAL_CONTEXT: "carries the decision history",
}

_DISAGREEMENT_POSITIONS = {
    PositionType.OPPOSE,
    PositionType.CONCERN,
    PositionType.CONDITIONAL,
}


def _authority_reason(actor: OrganizationalActorRef | None) -> str:
    if actor is None:
        return (
            "No decision authority identified — this is an explicit gap, "
            "not an assumption."
        )
    reasons = [
        _AUTHORITY_REASONS[r]
        for r in actor.relation_types
        if r in _AUTHORITY_REASONS
    ]
    role = f" ({actor.role})" if actor.role else ""
    if reasons:
        return f"{actor.display_name or actor.actor_id}{role} " + \
            "; ".join(reasons) + "."
    return (
        f"{actor.display_name or actor.actor_id}{role} was named as the "
        f"decision authority in the decision record."
    )


def build_decision_capsule(
    *,
    request: ActionRequest,
    workstream: WorkstreamState,
    decisions: list[OrganizationalDecisionRecord],
    positions: list[DecisionPosition],
    understanding: ProjectUnderstandingSnapshot,
    readiness: LaunchReadinessSnapshot | None,
    evidence: list[ProjectEvidence],
    as_of: datetime,
) -> DecisionCapsule:
    latest = get_latest_conclusive_decision(
        decisions=decisions,
        workstream_id=workstream.workstream_id,
        as_of=as_of,
    )

    authority = request.preferred_recipient
    if authority is None and latest is not None:
        authority = latest.decision_authority

    # What changed since the last conclusive decision: evidence observed
    # after it, summarized — never invented.
    changed: list[str] = []
    cutoff = latest.decided_at if latest and latest.decided_at else None
    for item in sorted(evidence, key=lambda e: (e.source.observed_at, e.id)):
        if cutoff is not None and item.source.observed_at <= cutoff:
            continue
        changed.append(item.content[:160])

    disagreements = [
        f"{p.actor.display_name or p.actor.actor_id} "
        f"({p.actor.role or 'role unknown'}): {p.statement}"
        for p in positions
        if p.position_type in _DISAGREEMENT_POSITIONS
    ]

    if readiness is not None:
        delay_impact = (
            f"On-time probability is "
            f"{readiness.forecast.on_time_probability:.2f}; delaying this "
            f"decision keeps the blocking risk in place and the launch "
            f"forecast degrades."
        )
    else:
        delay_impact = (
            "Progress stays blocked and dependent work cannot proceed."
        )

    options = [
        DecisionOption(
            id=f"opt-approve-{request.id}",
            label="Decide as requested",
            description=request.requested_action,
            advantages=[request.why_needed],
            risks=disagreements or ["None recorded."],
            evidence_ids=request.evidence_ids,
        ),
        DecisionOption(
            id=f"opt-defer-{request.id}",
            label="Defer",
            description="Keep the current state until further evidence.",
            advantages=["No new commitment made."],
            risks=[delay_impact],
            evidence_ids=request.evidence_ids,
        ),
    ]

    evidence_ids: list[str] = list(request.evidence_ids)
    for source in (
        workstream.evidence_ids,
        latest.evidence_ids if latest else [],
        *[p.evidence_ids for p in positions],
    ):
        for eid in source:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    affected_milestones = [
        d.name for d in workstream.key_dates if d.hard_commitment
    ] or [d.name for d in workstream.key_dates]

    kpi_ids: list[str] = list(workstream.kpi_ids)
    for kid in request.related_kpi_ids:
        if kid not in kpi_ids:
            kpi_ids.append(kid)

    return DecisionCapsule(
        id=f"capsule-{request.id}",
        project_id=request.project_id,
        workstream_id=workstream.workstream_id,
        decision_question=request.requested_action,
        requested_by=request.requested_by,
        decision_authority=authority,
        why_this_person_or_role=_authority_reason(authority),
        current_state=(
            f"{workstream.current_health.value} — "
            f"{workstream.key_status_signal}"
        ),
        latest_conclusive_decision=latest.conclusion if latest else None,
        last_relevant_discussion_at=latest.decided_at if latest else None,
        last_relevant_discussion_summary=(
            f"{latest.topic}: {latest.conclusion}" if latest else None
        ),
        actor_positions=list(positions),
        what_changed_since_last_discussion=changed,
        options=options,
        impact_if_decided=request.why_needed,
        impact_if_delayed=delay_impact,
        affected_milestones=affected_milestones,
        affected_kpi_ids=kpi_ids,
        open_disagreements=disagreements,
        evidence_ids=evidence_ids,
        confidence=min(
            workstream.health_confidence,
            latest.confidence if latest and latest.confidence else 1.0,
        ),
    )
