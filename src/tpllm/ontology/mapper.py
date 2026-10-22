"""Ontology mapper — maps integration/source objects into canonical entities.

This is the critical bridge layer. Raw source objects (Linear issues, Jira
tickets, GitHub PRs, Slack threads, Zoom transcripts, task board cards)
are mapped into canonical ontology entities.

Palantir principle: "Make your integrations look like your ontology."
The user sees WorkItem. The agent sees WorkItem + provenance.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from tpllm.ontology.core import (
    AIExtractedValue,
    ClaimType,
    EvidenceClaim,
    SourceRef,
    SourceSystem,
)
from tpllm.ontology.entities import (
    ActionItem,
    Artifact,
    Decision,
    DecisionStatus,
    Dependency,
    DependencyState,
    Issue,
    IssueState,
    Meeting,
    Priority,
    Risk,
    RiskState,
    Signal,
    SignalType,
    WorkItem,
    WorkItemStatus,
)


def _uid() -> str:
    return str(uuid.uuid4())[:12]


# ── Linear → Ontology ──────────────────────────────────────────────

_LINEAR_PRIORITY_MAP = {
    0: Priority.MEDIUM.value,
    1: Priority.CRITICAL.value,
    2: Priority.HIGH.value,
    3: Priority.MEDIUM.value,
    4: Priority.LOW.value,
}

_LINEAR_STATUS_MAP = {
    "Backlog": WorkItemStatus.PLANNED.value,
    "Todo": WorkItemStatus.PLANNED.value,
    "In Progress": WorkItemStatus.IN_PROGRESS.value,
    "In Review": WorkItemStatus.IN_REVIEW.value,
    "Done": WorkItemStatus.DONE.value,
    "Canceled": WorkItemStatus.DROPPED.value,
    "Cancelled": WorkItemStatus.DROPPED.value,
    "Triage": WorkItemStatus.PROPOSED.value,
}


def map_linear_issue(
    issue_id: str,
    title: str,
    state: str = "",
    assignee: str = "",
    priority: int = 0,
    labels: list[str] | None = None,
    url: str = "",
) -> WorkItem:
    """Map a Linear issue into a canonical WorkItem."""
    source = SourceRef(
        system=SourceSystem.LINEAR,
        external_id=issue_id,
        url=url,
    )
    return WorkItem(
        id=f"wi-lin-{issue_id}",
        title=title,
        status=_LINEAR_STATUS_MAP.get(state, WorkItemStatus.PROPOSED.value),
        priority=_LINEAR_PRIORITY_MAP.get(priority, Priority.MEDIUM.value),
        owner_id=assignee,
        tags=labels or [],
        linked_sources=[source],
        source_refs=[source],
    )


# ── Jira → Ontology ────────────────────────────────────────────────

_JIRA_STATUS_MAP = {
    "To Do": WorkItemStatus.PLANNED.value,
    "In Progress": WorkItemStatus.IN_PROGRESS.value,
    "In Review": WorkItemStatus.IN_REVIEW.value,
    "Done": WorkItemStatus.DONE.value,
    "Closed": WorkItemStatus.DONE.value,
    "Blocked": WorkItemStatus.BLOCKED.value,
    "Backlog": WorkItemStatus.PROPOSED.value,
}

_JIRA_PRIORITY_MAP = {
    "Highest": Priority.CRITICAL.value,
    "High": Priority.HIGH.value,
    "Medium": Priority.MEDIUM.value,
    "Low": Priority.LOW.value,
    "Lowest": Priority.LOW.value,
}


def map_jira_ticket(
    key: str,
    summary: str,
    status: str = "",
    assignee: str = "",
    priority: str = "",
    labels: list[str] | None = None,
    url: str = "",
) -> WorkItem:
    """Map a Jira ticket into a canonical WorkItem."""
    source = SourceRef(
        system=SourceSystem.JIRA,
        external_id=key,
        url=url,
    )
    return WorkItem(
        id=f"wi-jira-{key}",
        title=summary,
        status=_JIRA_STATUS_MAP.get(status, WorkItemStatus.PROPOSED.value),
        priority=_JIRA_PRIORITY_MAP.get(priority, Priority.MEDIUM.value),
        owner_id=assignee,
        tags=labels or [],
        linked_sources=[source],
        source_refs=[source],
    )


# ── GitHub → Ontology ──────────────────────────────────────────────

def map_github_pr(
    number: int,
    title: str,
    author: str = "",
    state: str = "",
    labels: list[str] | None = None,
    url: str = "",
) -> Artifact:
    """Map a GitHub PR into a canonical Artifact (code change)."""
    source = SourceRef(
        system=SourceSystem.GITHUB,
        external_id=str(number),
        url=url,
        author_id=author,
    )
    return Artifact(
        id=f"art-gh-pr-{number}",
        title=title,
        artifact_type="pull_request",
        url=url,
        author_id=author,
        linked_sources=[source],
        source_refs=[source],
    )


def map_github_pr_to_signal(
    number: int,
    title: str,
    state: str,
    author: str = "",
    url: str = "",
) -> Signal:
    """Map a GitHub PR state change to a progress Signal."""
    source = SourceRef(
        system=SourceSystem.GITHUB,
        external_id=str(number),
        url=url,
        author_id=author,
    )
    if state == "merged":
        signal_type = SignalType.PROGRESS_UPDATE.value
        detail = f"PR #{number} merged: {title}"
    elif state == "closed":
        signal_type = SignalType.PROGRESS_UPDATE.value
        detail = f"PR #{number} closed without merge: {title}"
    else:
        signal_type = SignalType.STATUS_HINT.value
        detail = f"PR #{number} ({state}): {title}"

    return Signal(
        id=f"sig-gh-pr-{number}",
        signal_type=signal_type,
        title=f"GitHub PR #{number}",
        detail=detail,
        source_system=SourceSystem.GITHUB.value,
        source_refs=[source],
    )


# ── Slack → Ontology ──────────────────────────────────────────────

def map_slack_message_to_signal(
    channel: str,
    thread_ts: str,
    text: str,
    author: str = "",
    signal_type: str = SignalType.STATUS_HINT.value,
) -> Signal:
    """Map a Slack message into a Signal for the agent to evaluate."""
    source = SourceRef(
        system=SourceSystem.SLACK,
        external_id=f"{channel}:{thread_ts}",
        author_id=author,
    )
    return Signal(
        id=f"sig-slack-{_uid()}",
        signal_type=signal_type,
        title=f"Slack: {text[:80]}",
        detail=text,
        source_system=SourceSystem.SLACK.value,
        source_refs=[source],
    )


def map_slack_message_to_claim(
    channel: str,
    thread_ts: str,
    text: str,
    author: str = "",
    claim_type: ClaimType = ClaimType.STATUS_UPDATE,
    confidence: float = 0.5,
) -> EvidenceClaim:
    """Map a Slack message into an EvidenceClaim when confidence warrants it."""
    source = SourceRef(
        system=SourceSystem.SLACK,
        external_id=f"{channel}:{thread_ts}",
        author_id=author,
    )
    return EvidenceClaim(
        id=f"ec-slack-{_uid()}",
        claim_text=text,
        claim_type=claim_type,
        source_refs=[source],
        confidence=confidence,
    )


# ── Zoom → Ontology ───────────────────────────────────────────────

def map_zoom_meeting(
    meeting_id: str,
    title: str,
    host: str = "",
    attendees: list[str] | None = None,
    duration_minutes: int = 0,
    summary: str = "",
    program_id: str = "",
    recording_url: str = "",
) -> Meeting:
    """Map a Zoom meeting into a canonical Meeting."""
    source = SourceRef(
        system=SourceSystem.ZOOM,
        external_id=meeting_id,
    )
    exec_summary = None
    if summary:
        exec_summary = AIExtractedValue(
            value=summary,
            confidence=0.8,
            source_refs=[source],
            extractor="zoom_ai",
        )
    return Meeting(
        id=f"mtg-zoom-{meeting_id}",
        title=title,
        program_id=program_id,
        attendee_ids=attendees or [],
        duration_minutes=duration_minutes,
        executive_summary=exec_summary,
        recording_url=recording_url,
        linked_sources=[source],
        source_refs=[source],
    )


def map_zoom_action_item(
    meeting_id: str,
    owner: str,
    description: str,
    due_date: str = "",
) -> ActionItem:
    """Map a Zoom-extracted action item to a canonical ActionItem."""
    source = SourceRef(
        system=SourceSystem.ZOOM,
        external_id=meeting_id,
        author_id=owner,
    )
    dd = None
    if due_date:
        try:
            dd = date.fromisoformat(due_date)
        except ValueError:
            pass
    return ActionItem(
        id=f"ai-zoom-{_uid()}",
        title=description,
        owner_id=owner,
        due_date=dd,
        source_meeting_id=f"mtg-zoom-{meeting_id}",
        source_refs=[source],
        evidence_claim_ids=[],
        confidence=0.7,
    )


# ── Notion → Ontology ──────────────────────────────────────────────

def map_notion_page(
    page_id: str,
    title: str,
    url: str = "",
    author: str = "",
    page_type: str = "document",
) -> Artifact:
    """Map a Notion page into a canonical Artifact."""
    source = SourceRef(
        system=SourceSystem.NOTION,
        external_id=page_id,
        url=url,
        author_id=author,
    )
    return Artifact(
        id=f"art-notion-{page_id}",
        title=title,
        artifact_type=page_type,
        url=url,
        author_id=author,
        linked_sources=[source],
        source_refs=[source],
    )


# ── Calendar → Ontology ────────────────────────────────────────────

def map_calendar_event(
    event_id: str,
    title: str,
    start: datetime | None = None,
    end: datetime | None = None,
    attendees: list[str] | None = None,
    source_system: SourceSystem = SourceSystem.GOOGLE_CALENDAR,
    program_id: str = "",
) -> Meeting:
    """Map a Google/Microsoft Calendar event into a canonical Meeting."""
    source = SourceRef(
        system=source_system,
        external_id=event_id,
    )
    duration = 0
    start_d = None
    if start and end:
        duration = int((end - start).total_seconds() / 60)
    if start:
        start_d = start.date()
    return Meeting(
        id=f"mtg-cal-{event_id[:12]}",
        title=title,
        program_id=program_id,
        start_date=start_d,
        attendee_ids=attendees or [],
        duration_minutes=duration,
        linked_sources=[source],
        source_refs=[source],
    )


# ── Internal Task Board → Ontology ─────────────────────────────────

_BOARD_STATUS_MAP = {
    "BACKLOG": WorkItemStatus.PROPOSED.value,
    "TODO": WorkItemStatus.PLANNED.value,
    "IN_PROGRESS": WorkItemStatus.IN_PROGRESS.value,
    "IN_REVIEW": WorkItemStatus.IN_REVIEW.value,
    "BLOCKED": WorkItemStatus.BLOCKED.value,
    "DONE": WorkItemStatus.DONE.value,
    "CANCELLED": WorkItemStatus.DROPPED.value,
}

_BOARD_PRIORITY_MAP = {
    "CRITICAL": Priority.CRITICAL.value,
    "HIGH": Priority.HIGH.value,
    "MEDIUM": Priority.MEDIUM.value,
    "LOW": Priority.LOW.value,
}


def map_board_task(
    task_id: str,
    title: str,
    status: str = "BACKLOG",
    priority: str = "MEDIUM",
    assignee: str = "",
    due_date: str = "",
    effort_hours: float = 0.0,
    sprint_id: str = "",
    source_id: str = "",
    tags: list[str] | None = None,
    blocks: list[str] | None = None,
    blocked_by: list[str] | None = None,
) -> WorkItem:
    """Map an internal BoardTask into a canonical WorkItem."""
    source = SourceRef(
        system=SourceSystem.TASK_BOARD,
        external_id=task_id,
    )
    dd = None
    if due_date:
        try:
            dd = date.fromisoformat(due_date)
        except ValueError:
            pass
    return WorkItem(
        id=f"wi-board-{task_id}",
        title=title,
        status=_BOARD_STATUS_MAP.get(status, WorkItemStatus.PROPOSED.value),
        priority=_BOARD_PRIORITY_MAP.get(priority, Priority.MEDIUM.value),
        owner_id=assignee,
        due_date=dd,
        effort_hours=effort_hours,
        sprint_id=sprint_id,
        tags=tags or [],
        blocks=[f"wi-board-{b}" for b in (blocks or [])],
        blocked_by=[f"wi-board-{b}" for b in (blocked_by or [])],
        linked_sources=[source],
        source_refs=[source],
        confidence=1.0,
    )


# ── RAID Log → Ontology ────────────────────────────────────────────

_RAID_STATUS_MAP = {
    "OPEN": RiskState.IDENTIFIED.value,
    "MITIGATING": RiskState.MITIGATING.value,
    "RESOLVED": RiskState.RESOLVED.value,
    "ACCEPTED": RiskState.ACCEPTED.value,
}

_ISSUE_STATUS_MAP = {
    "OPEN": IssueState.OPEN.value,
    "MITIGATING": IssueState.IN_PROGRESS.value,
    "RESOLVED": IssueState.RESOLVED.value,
    "ACCEPTED": IssueState.CLOSED.value,
}


def map_raid_risk(
    risk_id: str,
    title: str,
    description: str = "",
    severity: str = "medium",
    probability: str = "medium",
    owner: str = "",
    status: str = "OPEN",
    mitigation: str = "",
) -> Risk:
    """Map a RAID log risk into a canonical Risk."""
    source = SourceRef(
        system=SourceSystem.TASK_BOARD,
        external_id=risk_id,
    )
    return Risk(
        id=f"risk-{risk_id}",
        title=title,
        description=description,
        status=_RAID_STATUS_MAP.get(status, RiskState.IDENTIFIED.value),
        severity=severity.lower(),
        probability=probability.lower(),
        owner_id=owner,
        mitigation=mitigation,
        source_refs=[source],
    )


def map_raid_issue(
    issue_id: str,
    title: str,
    description: str = "",
    severity: str = "medium",
    owner: str = "",
    status: str = "OPEN",
    resolution: str = "",
) -> Issue:
    """Map a RAID log issue into a canonical Issue."""
    source = SourceRef(
        system=SourceSystem.TASK_BOARD,
        external_id=issue_id,
    )
    return Issue(
        id=f"issue-{issue_id}",
        title=title,
        description=description,
        status=_ISSUE_STATUS_MAP.get(status, IssueState.OPEN.value),
        severity=severity.lower(),
        owner_id=owner,
        resolution=resolution,
        source_refs=[source],
    )


def map_raid_dependency(
    dep_id: str,
    title: str,
    description: str = "",
    upstream_team: str = "",
    downstream_team: str = "",
    owner: str = "",
    status: str = "OPEN",
    commitment_date: str = "",
) -> Dependency:
    """Map a RAID log dependency into a canonical Dependency."""
    source = SourceRef(
        system=SourceSystem.TASK_BOARD,
        external_id=dep_id,
    )
    cd = None
    if commitment_date:
        try:
            cd = date.fromisoformat(commitment_date)
        except ValueError:
            pass
    return Dependency(
        id=f"dep-{dep_id}",
        title=title,
        description=description,
        status=DependencyState.TRACKING.value,
        upstream_team_id=upstream_team,
        downstream_team_id=downstream_team,
        owner_id=owner,
        commitment_date=cd,
        source_refs=[source],
    )


# ── Decision Log → Ontology ────────────────────────────────────────

_DECISION_STATUS_MAP = {
    "PROPOSED": DecisionStatus.PROPOSED.value,
    "DECIDED": DecisionStatus.DECIDED.value,
    "REVISITED": DecisionStatus.DISPUTED.value,
}


def map_decision(
    decision_id: str,
    title: str,
    decision_text: str = "",
    status: str = "PROPOSED",
    decided_by: str = "",
    rationale: str = "",
    follow_ups: list[str] | None = None,
) -> Decision:
    """Map a decision log entry into a canonical Decision."""
    source = SourceRef(
        system=SourceSystem.TASK_BOARD,
        external_id=decision_id,
    )
    return Decision(
        id=f"dec-{decision_id}",
        title=title,
        decision_text=decision_text,
        status=_DECISION_STATUS_MAP.get(status, DecisionStatus.PROPOSED.value),
        decision_owner_id=decided_by,
        rationale=rationale,
        follow_up_action_ids=follow_ups or [],
        linked_sources=[source],
        source_refs=[source],
    )
