"""Exception-driven meeting intelligence.

Meetings are for material change, contradiction, decisions, blockers,
cross-team dependencies, KPI breaches, and scope changes — never for
"everyone gives an update". No material change means no synchronous
discussion requirement.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from lltpm.coordination.models import (
    ActionRequest,
    OrganizationalDecisionRecord,
    OrganizationalDecisionStatus,
    WorkstreamHealth,
    WorkstreamState,
)
from lltpm.coordination.requests import is_authority_request, is_open_request
from lltpm.coordination.workstreams import health_rank


class DiscussionNeed(str, Enum):
    NO_DISCUSSION = "no_discussion"
    ASYNC_UPDATE = "async_update"
    DISCUSSION = "discussion"
    DECISION_REQUIRED = "decision_required"


class MaterialChangeType(str, Enum):
    STATUS_CHANGE = "status_change"
    NEW_BLOCKER = "new_blocker"
    BLOCKER_RESOLVED = "blocker_resolved"
    SCOPE_CHANGE = "scope_change"
    DECISION_CHANGE = "decision_change"
    KPI_CHANGE = "kpi_change"
    DEPENDENCY_CHANGE = "dependency_change"
    COMMITMENT_CHANGE = "commitment_change"
    OWNERSHIP_CHANGE = "ownership_change"
    NEW_CONTRADICTION = "new_contradiction"


class MaterialChange(BaseModel):
    id: str
    project_id: str
    workstream_id: str | None
    change_type: MaterialChangeType
    before_summary: str | None
    after_summary: str
    impact: str
    severity: str
    evidence_ids: list[str]
    observed_at: datetime


class DiscussionCandidate(BaseModel):
    id: str
    workstream_id: str
    need: DiscussionNeed
    topic: str
    why_now: str
    decision_question: str | None = None
    affected_objective_ids: list[str] = Field(default_factory=list)
    relevant_change_ids: list[str] = Field(default_factory=list)
    estimated_attention_value: float
    evidence_ids: list[str] = Field(default_factory=list)


class MeetingAgendaPlan(BaseModel):
    project_id: str
    meeting_id: str | None
    as_of: datetime
    no_discussion_workstream_ids: list[str]
    async_update_workstream_ids: list[str]
    discussion_candidates: list[DiscussionCandidate]
    decision_required_candidates: list[DiscussionCandidate]
    recommended_order: list[str]
    executive_summary: str
    evidence_ids: list[str]


# ── Material change detection ────────────────────────────────────────

def detect_material_changes(
    *,
    previous_workstreams: list[WorkstreamState],
    current_workstreams: list[WorkstreamState],
    previous_decisions: list[OrganizationalDecisionRecord],
    current_decisions: list[OrganizationalDecisionRecord],
    as_of: datetime,
) -> list[MaterialChange]:
    """Diff the organizational state: health transitions, blocker
    open/close, and decision lifecycle changes. Deterministic."""
    changes: list[MaterialChange] = []
    counter = 0

    def add(
        workstream_id: str | None,
        project_id: str,
        change_type: MaterialChangeType,
        before: str | None,
        after: str,
        impact: str,
        severity: str,
        evidence_ids: list[str],
    ) -> None:
        nonlocal counter
        counter += 1
        changes.append(
            MaterialChange(
                id=f"chg-{counter}",
                project_id=project_id,
                workstream_id=workstream_id,
                change_type=change_type,
                before_summary=before,
                after_summary=after,
                impact=impact,
                severity=severity,
                evidence_ids=evidence_ids,
                observed_at=as_of,
            )
        )

    previous_by_id = {w.workstream_id: w for w in previous_workstreams}
    for workstream in sorted(current_workstreams, key=lambda w: w.workstream_id):
        previous = previous_by_id.get(workstream.workstream_id)
        if previous is None:
            continue

        if previous.current_health != workstream.current_health:
            worsened = health_rank(workstream.current_health) > health_rank(
                previous.current_health
            )
            severity = (
                "high"
                if worsened
                and workstream.current_health
                in (WorkstreamHealth.AT_RISK, WorkstreamHealth.BLOCKED)
                else ("medium" if worsened else "low")
            )
            add(
                workstream.workstream_id,
                workstream.project_id,
                MaterialChangeType.STATUS_CHANGE,
                previous.current_health.value,
                workstream.current_health.value,
                workstream.key_status_signal,
                severity,
                workstream.evidence_ids,
            )

        previous_blockers = set(previous.blocker_ids)
        current_blockers = set(workstream.blocker_ids)
        for blocker_id in sorted(current_blockers - previous_blockers):
            add(
                workstream.workstream_id,
                workstream.project_id,
                MaterialChangeType.NEW_BLOCKER,
                None,
                f"New blocker: {blocker_id}",
                workstream.key_status_signal,
                "high",
                workstream.evidence_ids,
            )
        for blocker_id in sorted(previous_blockers - current_blockers):
            add(
                workstream.workstream_id,
                workstream.project_id,
                MaterialChangeType.BLOCKER_RESOLVED,
                f"Blocked: {blocker_id}",
                f"Blocker resolved: {blocker_id}",
                "Previously blocked work can proceed.",
                "low",
                workstream.evidence_ids,
            )

    previous_decisions_by_id = {d.id: d for d in previous_decisions}
    for decision in sorted(current_decisions, key=lambda d: d.id):
        previous = previous_decisions_by_id.get(decision.id)
        previous_status = previous.status if previous else None
        if previous_status == decision.status:
            continue
        if decision.status == OrganizationalDecisionStatus.CONCLUSIVE:
            add(
                decision.workstream_id,
                decision.project_id,
                MaterialChangeType.DECISION_CHANGE,
                previous_status.value if previous_status else None,
                f"Decided: {decision.conclusion}",
                decision.consequence_summary or decision.topic,
                "medium",
                decision.evidence_ids,
            )
        elif decision.status == OrganizationalDecisionStatus.REOPENED:
            add(
                decision.workstream_id,
                decision.project_id,
                MaterialChangeType.DECISION_CHANGE,
                previous_status.value if previous_status else None,
                f"Reopened: {decision.topic}",
                decision.consequence_summary or "The question is open again.",
                "high",
                decision.evidence_ids,
            )

    return changes


# ── Discussion need ──────────────────────────────────────────────────

_DISCUSSION_CHANGE_TYPES = {
    MaterialChangeType.NEW_BLOCKER,
    MaterialChangeType.NEW_CONTRADICTION,
    MaterialChangeType.SCOPE_CHANGE,
    MaterialChangeType.DEPENDENCY_CHANGE,
}


def determine_discussion_need(
    *,
    workstream: WorkstreamState,
    material_changes: list[MaterialChange],
    open_requests: list[ActionRequest],
    unresolved_decisions: list[OrganizationalDecisionRecord],
) -> DiscussionNeed:
    """No material change + no decision → NO_DISCUSSION. Information-only
    change → ASYNC_UPDATE. Multi-party ambiguity → DISCUSSION. A specific
    blocking authority decision → DECISION_REQUIRED."""
    relevant_changes = [
        c for c in material_changes if c.workstream_id == workstream.workstream_id
    ]
    relevant_requests = [
        r
        for r in open_requests
        if is_open_request(r) and r.workstream_id == workstream.workstream_id
    ]

    authority_pending = [
        r for r in relevant_requests if is_authority_request(r)
    ]
    if authority_pending:
        return DiscussionNeed.DECISION_REQUIRED

    if unresolved_decisions and (
        workstream.current_health
        in (WorkstreamHealth.AT_RISK, WorkstreamHealth.BLOCKED)
        or any(c.severity == "high" for c in relevant_changes)
    ):
        return DiscussionNeed.DECISION_REQUIRED

    if any(
        c.change_type in _DISCUSSION_CHANGE_TYPES or c.severity == "high"
        for c in relevant_changes
    ) or (unresolved_decisions and relevant_changes):
        return DiscussionNeed.DISCUSSION

    if relevant_changes:
        return DiscussionNeed.ASYNC_UPDATE

    if unresolved_decisions:
        return DiscussionNeed.DISCUSSION

    return DiscussionNeed.NO_DISCUSSION


# ── Agenda construction ──────────────────────────────────────────────

def _attention_value(
    need: DiscussionNeed,
    changes: list[MaterialChange],
    requests: list[ActionRequest],
    workstream: WorkstreamState,
) -> float:
    base = {
        DiscussionNeed.DECISION_REQUIRED: 3.0,
        DiscussionNeed.DISCUSSION: 2.0,
        DiscussionNeed.ASYNC_UPDATE: 0.5,
        DiscussionNeed.NO_DISCUSSION: 0.0,
    }[need]
    severity_bonus = sum(
        {"high": 0.6, "medium": 0.3, "low": 0.1}.get(c.severity, 0.0)
        for c in changes
    )
    authority_bonus = 0.5 * sum(1 for r in requests if is_authority_request(r))
    health_bonus = 0.2 * health_rank(workstream.current_health)
    hard_dates = 0.3 * sum(1 for d in workstream.key_dates if d.hard_commitment)
    return base + severity_bonus + authority_bonus + health_bonus + hard_dates


def build_exception_driven_meeting_agenda(
    *,
    project_id: str,
    workstreams: list[WorkstreamState],
    material_changes: list[MaterialChange],
    open_requests: list[ActionRequest],
    decisions: list[OrganizationalDecisionRecord],
    as_of: datetime,
    meeting_id: str | None = None,
) -> MeetingAgendaPlan:
    """Agenda sorted by decision criticality and impact — never by team
    alphabetical order, and never a round-robin update prompt."""
    from lltpm.coordination.decisions import (
        decision_touches_workstream,
        is_decision_unresolved,
    )

    no_discussion: list[str] = []
    async_updates: list[str] = []
    discussion: list[DiscussionCandidate] = []
    decision_required: list[DiscussionCandidate] = []
    evidence_ids: list[str] = []

    for workstream in sorted(workstreams, key=lambda w: w.workstream_id):
        ws_changes = [
            c for c in material_changes
            if c.workstream_id == workstream.workstream_id
        ]
        ws_requests = [
            r for r in open_requests
            if is_open_request(r)
            and r.workstream_id == workstream.workstream_id
        ]
        ws_unresolved = [
            d for d in decisions
            if decision_touches_workstream(d, workstream.workstream_id)
            and is_decision_unresolved(d)
        ]
        need = determine_discussion_need(
            workstream=workstream,
            material_changes=ws_changes,
            open_requests=ws_requests,
            unresolved_decisions=ws_unresolved,
        )

        if need == DiscussionNeed.NO_DISCUSSION:
            no_discussion.append(workstream.workstream_id)
            continue
        if need == DiscussionNeed.ASYNC_UPDATE:
            async_updates.append(workstream.workstream_id)
            continue

        why_now = (
            ws_changes[0].after_summary
            if ws_changes
            else (
                ws_requests[0].why_needed
                if ws_requests
                else workstream.key_status_signal
            )
        )
        question = None
        if ws_requests:
            question = ws_requests[0].requested_action
        elif ws_unresolved:
            question = ws_unresolved[0].question

        candidate = DiscussionCandidate(
            id=f"cand-{workstream.workstream_id}",
            workstream_id=workstream.workstream_id,
            need=need,
            topic=workstream.feature_or_scope,
            why_now=why_now,
            decision_question=question,
            affected_objective_ids=[workstream.objective.id],
            relevant_change_ids=[c.id for c in ws_changes],
            estimated_attention_value=_attention_value(
                need, ws_changes, ws_requests, workstream
            ),
            evidence_ids=workstream.evidence_ids,
        )
        if need == DiscussionNeed.DECISION_REQUIRED:
            decision_required.append(candidate)
        else:
            discussion.append(candidate)
        for eid in workstream.evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    ordered_candidates = sorted(
        decision_required, key=lambda c: (-c.estimated_attention_value, c.id)
    ) + sorted(discussion, key=lambda c: (-c.estimated_attention_value, c.id))
    recommended_order = [c.id for c in ordered_candidates]

    lines: list[str] = []
    for candidate in ordered_candidates:
        label = (
            "DECISION REQUIRED"
            if candidate.need == DiscussionNeed.DECISION_REQUIRED
            else "DISCUSSION"
        )
        lines.append(f"{label} — {candidate.topic}: {candidate.why_now}")
    if async_updates:
        lines.append(
            f"ASYNC — {len(async_updates)} workstream(s) changed without "
            f"needing synchronous time."
        )
    if no_discussion:
        lines.append(
            f"NO DISCUSSION — {len(no_discussion)} workstream(s) with no "
            f"material change."
        )
    if not lines:
        lines.append("No material change anywhere — no meeting required.")

    return MeetingAgendaPlan(
        project_id=project_id,
        meeting_id=meeting_id,
        as_of=as_of,
        no_discussion_workstream_ids=no_discussion,
        async_update_workstream_ids=async_updates,
        discussion_candidates=sorted(
            discussion, key=lambda c: (-c.estimated_attention_value, c.id)
        ),
        decision_required_candidates=sorted(
            decision_required,
            key=lambda c: (-c.estimated_attention_value, c.id),
        ),
        recommended_order=recommended_order,
        executive_summary="\n".join(lines),
        evidence_ids=evidence_ids,
    )
