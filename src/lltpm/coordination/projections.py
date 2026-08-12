"""Executive brief projection — wins and risks separated, every line
traceable to evidence, no invented prose."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from lltpm.coordination.decisions import is_decision_unresolved
from lltpm.coordination.models import (
    ActionRequest,
    ActionRequestStatus,
    OrganizationalDecisionRecord,
    WorkstreamHealth,
    WorkstreamState,
)
from lltpm.coordination.requests import is_authority_request, is_open_request


class ProjectExecutiveBrief(BaseModel):
    project_id: str
    as_of: datetime
    executive_summary: str
    wins: list[str]
    high_risk_workstream_ids: list[str]
    medium_risk_workstream_ids: list[str]
    low_risk_workstream_ids: list[str]
    decisions_required: list[str]
    critical_action_request_ids: list[str]
    major_changes_since_previous: list[str]
    evidence_ids: list[str]


_HIGH = {WorkstreamHealth.AT_RISK, WorkstreamHealth.BLOCKED}
_MEDIUM = {WorkstreamHealth.WATCH, WorkstreamHealth.UNKNOWN}

_CRITICAL_ASK_WINDOW_DAYS = 7


def build_project_executive_brief(
    *,
    workstreams: list[WorkstreamState],
    previous_workstreams: list[WorkstreamState],
    decisions: list[OrganizationalDecisionRecord],
    requests: list[ActionRequest],
    as_of: datetime,
) -> ProjectExecutiveBrief:
    project_id = workstreams[0].project_id if workstreams else "unknown"
    ordered = sorted(workstreams, key=lambda w: w.workstream_id)

    high = [w.workstream_id for w in ordered if w.current_health in _HIGH]
    medium = [w.workstream_id for w in ordered if w.current_health in _MEDIUM]
    low = [
        w.workstream_id
        for w in ordered
        if w.current_health == WorkstreamHealth.ON_TRACK
    ]

    wins: list[str] = []
    for workstream in ordered:
        for win in workstream.wins:
            if win not in wins:
                wins.append(win)

    decisions_required = [
        f"{d.topic}: {d.question}"
        for d in sorted(decisions, key=lambda d: d.id)
        if is_decision_unresolved(d)
    ]

    critical_asks = []
    for request in sorted(requests, key=lambda r: r.id):
        if not is_open_request(request):
            continue
        due_soon = (
            request.due_at is not None
            and request.due_at <= as_of + timedelta(days=_CRITICAL_ASK_WINDOW_DAYS)
        )
        if request.status == ActionRequestStatus.BLOCKED or (
            is_authority_request(request) and due_soon
        ):
            critical_asks.append(request.id)

    previous_by_id = {w.workstream_id: w for w in previous_workstreams}
    changes: list[str] = []
    for workstream in ordered:
        previous = previous_by_id.get(workstream.workstream_id)
        if previous is None:
            continue
        if previous.current_health != workstream.current_health:
            changes.append(
                f"{workstream.feature_or_scope}: "
                f"{previous.current_health.value} → "
                f"{workstream.current_health.value} "
                f"({workstream.key_status_signal})"
            )

    evidence_ids: list[str] = []
    for workstream in ordered:
        for eid in workstream.evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    summary = (
        f"{len(ordered)} workstream(s): {len(low)} on track, "
        f"{len(medium)} on watch, {len(high)} at risk or blocked. "
        f"{len(decisions_required)} decision(s) required; "
        f"{len(critical_asks)} critical ask(s) open; "
        f"{len(changes)} material health change(s) since previous brief."
    )

    return ProjectExecutiveBrief(
        project_id=project_id,
        as_of=as_of,
        executive_summary=summary,
        wins=wins,
        high_risk_workstream_ids=high,
        medium_risk_workstream_ids=medium,
        low_risk_workstream_ids=low,
        decisions_required=decisions_required,
        critical_action_request_ids=critical_asks,
        major_changes_since_previous=changes,
        evidence_ids=evidence_ids,
    )
