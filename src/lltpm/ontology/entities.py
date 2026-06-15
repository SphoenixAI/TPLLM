"""Canonical ontology entities — the real-world TPM domain model.

These entities model the actual project reality, not source system shapes.
Integrations become evidence sources that feed into these canonical types.

Palantir principle: "Build one canonical project-world model, then map
every tool into that model." A Linear issue, Jira ticket, GitHub PR,
Slack thread, and Zoom transcript all resolve into these entities.

Entity → Interface mapping:
    WorkItem          → Trackable, Assignable, Schedulable, EvidenceBacked, SourceMapped, Reportable
    ActionItem        → Trackable, Assignable, Schedulable, EvidenceBacked
    Issue             → Trackable, Assignable, Escalatable, EvidenceBacked, Reportable
    Risk              → Trackable, Assignable, Escalatable, EvidenceBacked
    Dependency        → Trackable, Assignable, Schedulable, EvidenceBacked
    Decision          → Trackable, EvidenceBacked, SourceMapped
    Meeting           → Schedulable, EvidenceBacked, SourceMapped
    Artifact          → SourceMapped, EvidenceBacked
    AgentRecommendation → EvidenceBacked, Trackable
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from lltpm.ontology.core import AIExtractedValue, SourceRef, SourceSystem
from lltpm.ontology.interfaces import (
    Assignable,
    EvidenceBacked,
    Escalatable,
    Reportable,
    Schedulable,
    SourceMapped,
    Trackable,
)


# ── Status Enums ────────────────────────────────────────────────────

class WorkItemStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    IN_REVIEW = "in_review"
    DONE = "done"
    DROPPED = "dropped"
    SUPERSEDED = "superseded"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DecisionStatus(str, Enum):
    CANDIDATE = "candidate"
    PROPOSED = "proposed"
    PENDING_APPROVAL = "pending_approval"
    DECIDED = "decided"
    COMMUNICATED = "communicated"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    REVERSED = "reversed"


class RiskState(str, Enum):
    IDENTIFIED = "identified"
    ASSESSING = "assessing"
    MITIGATING = "mitigating"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    OCCURRED = "occurred"


class IssueState(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    CLOSED = "closed"


class DependencyState(str, Enum):
    IDENTIFIED = "identified"
    TRACKING = "tracking"
    AT_RISK = "at_risk"
    BLOCKED = "blocked"
    RESOLVED = "resolved"


class SignalType(str, Enum):
    STATUS_HINT = "status_hint"
    BLOCKER_HINT = "blocker_hint"
    RISK_HINT = "risk_hint"
    SENTIMENT_SHIFT = "sentiment_shift"
    TIMELINE_SHIFT = "timeline_shift"
    OWNERSHIP_CHANGE = "ownership_change"
    SCOPE_DRIFT = "scope_drift"
    PROGRESS_UPDATE = "progress_update"


# ── People & Teams ──────────────────────────────────────────────────

class Person(BaseModel):
    """A real person in the program ecosystem."""
    id: str = ""
    name: str = ""
    email: str = ""
    role: str = ""
    team_id: str = ""
    expertise: list[str] = Field(default_factory=list)
    linked_sources: list[SourceRef] = Field(default_factory=list)


class Team(BaseModel):
    """A team or organizational unit."""
    id: str = ""
    name: str = ""
    lead_id: str = ""
    member_ids: list[str] = Field(default_factory=list)
    domain: str = ""
    linked_sources: list[SourceRef] = Field(default_factory=list)


# ── Program Hierarchy ───────────────────────────────────────────────

class Program(Trackable, Reportable, SourceMapped):
    """Top-level program — the highest unit of organized work."""
    id: str = ""
    name: str = ""
    goal: str = ""
    non_goals: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    stakeholder_ids: list[str] = Field(default_factory=list)
    target_date: date | None = None


class Project(Trackable, Assignable, Schedulable, Reportable, SourceMapped):
    """A project within a program — a scoped body of work."""
    id: str = ""
    name: str = ""
    program_id: str = ""
    workstream_ids: list[str] = Field(default_factory=list)


class Workstream(Trackable, Assignable, Reportable):
    """A workstream within a project — a thematic slice of work."""
    id: str = ""
    name: str = ""
    project_id: str = ""


# ── Core Work Entities ──────────────────────────────────────────────

class WorkItem(Trackable, Assignable, Schedulable, EvidenceBacked, SourceMapped, Reportable):
    """Planned work — the canonical unit of trackable effort.

    Linear issues, Jira tickets, GitHub issues, task board cards,
    and Zoom-extracted action items all resolve into WorkItems.
    """
    id: str = ""
    title: str = ""
    description: str = ""
    summary: AIExtractedValue | None = None
    status: str = WorkItemStatus.PROPOSED.value
    priority: str = Priority.MEDIUM.value
    effort_hours: float = 0.0
    milestone_ids: list[str] = Field(default_factory=list)
    dependency_ids: list[str] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sprint_id: str = ""
    parent_id: str = ""


class ActionItem(Trackable, Assignable, Schedulable, EvidenceBacked):
    """An explicit follow-up someone must do — narrower than WorkItem.

    Extracted from meetings, decisions, or standups. Always has an owner
    and a clear deliverable.
    """
    id: str = ""
    title: str = ""
    description: str = ""
    source_meeting_id: str = ""
    source_decision_id: str = ""
    completed_at: datetime | None = None


class Issue(Trackable, Assignable, Escalatable, EvidenceBacked, Reportable):
    """A current problem that needs resolution — not a future risk."""
    id: str = ""
    title: str = ""
    description: str = ""
    status: str = IssueState.OPEN.value
    impact: str = ""
    resolution: str = ""
    related_work_item_ids: list[str] = Field(default_factory=list)
    resolved_at: datetime | None = None


class Risk(Trackable, Assignable, Escalatable, EvidenceBacked):
    """A future possible problem — not yet occurred."""
    id: str = ""
    title: str = ""
    description: str = ""
    status: str = RiskState.IDENTIFIED.value
    probability: str = "medium"
    impact: str = ""
    mitigation: str = ""
    trigger_conditions: list[str] = Field(default_factory=list)
    affected_milestone_ids: list[str] = Field(default_factory=list)


class Dependency(Trackable, Assignable, Schedulable, EvidenceBacked):
    """Something one entity waits on — cross-team or external."""
    id: str = ""
    title: str = ""
    description: str = ""
    status: str = DependencyState.IDENTIFIED.value
    upstream_team_id: str = ""
    downstream_team_id: str = ""
    commitment_date: date | None = None
    dependent_work_item_ids: list[str] = Field(default_factory=list)


class Decision(Trackable, EvidenceBacked, SourceMapped):
    """A commitment that changes direction — explicit or inferred."""
    id: str = ""
    title: str = ""
    decision_text: str = ""
    status: str = DecisionStatus.CANDIDATE.value
    decision_owner_id: str = ""
    decided_at: datetime | None = None
    options_considered: list[str] = Field(default_factory=list)
    rationale: str = ""
    affected_object_ids: list[str] = Field(default_factory=list)
    supersedes_decision_ids: list[str] = Field(default_factory=list)
    follow_up_action_ids: list[str] = Field(default_factory=list)


class Requirement(Trackable, EvidenceBacked, SourceMapped):
    """A stated need or constraint on the program."""
    id: str = ""
    title: str = ""
    description: str = ""
    priority: str = Priority.MEDIUM.value
    acceptance_criteria: list[str] = Field(default_factory=list)
    fulfilled_by_work_item_ids: list[str] = Field(default_factory=list)


class Question(Trackable, Assignable, EvidenceBacked):
    """An unknown that needs an answer before work can proceed."""
    id: str = ""
    question_text: str = ""
    context: str = ""
    answer: str = ""
    answered_by_id: str = ""
    answered_at: datetime | None = None
    blocks_work_item_ids: list[str] = Field(default_factory=list)


# ── Milestone & Sprint ──────────────────────────────────────────────

class Milestone(Trackable, Schedulable, Reportable, EvidenceBacked):
    """A significant checkpoint in the program timeline."""
    id: str = ""
    name: str = ""
    description: str = ""
    program_id: str = ""
    on_critical_path: bool = False
    dependency_milestone_ids: list[str] = Field(default_factory=list)
    gate_criteria: list[str] = Field(default_factory=list)
    deliverable_ids: list[str] = Field(default_factory=list)


class Deliverable(Trackable, Assignable, Schedulable, EvidenceBacked):
    """A concrete output tied to a milestone."""
    id: str = ""
    title: str = ""
    description: str = ""
    milestone_id: str = ""
    artifact_ids: list[str] = Field(default_factory=list)


class Sprint(Trackable, Reportable):
    """A time-boxed iteration for planning and execution."""
    id: str = ""
    name: str = ""
    goal: str = ""
    program_id: str = ""
    start_date: date | None = None
    end_date: date | None = None
    team_capacity_hours: float = 0.0
    committed_hours: float = 0.0
    is_active: bool = False
    work_item_ids: list[str] = Field(default_factory=list)


# ── Meetings & Artifacts ────────────────────────────────────────────

class Meeting(Schedulable, EvidenceBacked, SourceMapped):
    """A real-world meeting — the source of many evidence claims."""
    id: str = ""
    title: str = ""
    program_id: str = ""
    attendee_ids: list[str] = Field(default_factory=list)
    duration_minutes: int = 0
    executive_summary: AIExtractedValue | None = None
    extracted_action_ids: list[str] = Field(default_factory=list)
    extracted_decision_ids: list[str] = Field(default_factory=list)
    extracted_risk_ids: list[str] = Field(default_factory=list)
    recording_url: str = ""
    transcript_url: str = ""


class Artifact(SourceMapped, EvidenceBacked):
    """A document, design, spec, or other produced artifact."""
    id: str = ""
    title: str = ""
    artifact_type: str = ""
    url: str = ""
    author_id: str = ""
    related_object_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Signals & Intelligence ──────────────────────────────────────────

class Signal(EvidenceBacked):
    """A weak clue from chat, meeting, or activity — not yet a fact.

    Signals are the raw material the agent uses to detect changes,
    risks, and contradictions. They may or may not become evidence claims.
    """
    id: str = ""
    signal_type: str = SignalType.STATUS_HINT.value
    title: str = ""
    detail: str = ""
    source_system: str = ""
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    related_object_ids: list[str] = Field(default_factory=list)
    promoted_to_claim_id: str = ""


class AgentRecommendation(EvidenceBacked, Trackable):
    """An AI-generated recommendation for human review."""
    id: str = ""
    title: str = ""
    recommendation_text: str = ""
    rationale: str = ""
    impact: str = ""
    urgency: str = Priority.MEDIUM.value
    target_object_ids: list[str] = Field(default_factory=list)
    accepted: bool | None = None
    accepted_by_id: str = ""
    accepted_at: datetime | None = None


# ── Reports & Notifications ────────────────────────────────────────

class StatusReport(Trackable, Reportable, EvidenceBacked):
    """A generated status report for a program."""
    id: str = ""
    program_id: str = ""
    report_date: date | None = None
    highlights: list[str] = Field(default_factory=list)
    risks_summary: list[str] = Field(default_factory=list)
    blockers_summary: list[str] = Field(default_factory=list)
    asks: list[str] = Field(default_factory=list)
    upcoming: list[str] = Field(default_factory=list)


class OntologyNotification(Trackable):
    """A notification generated by the ontology/agent layer."""
    id: str = ""
    recipient_id: str = ""
    title: str = ""
    body: str = ""
    category: str = ""
    priority: str = Priority.MEDIUM.value
    related_object_ids: list[str] = Field(default_factory=list)
    sent_at: datetime | None = None
    read_at: datetime | None = None


class OntologyChangeProposal(Trackable, EvidenceBacked):
    """A proposed change to the ontology itself — for human review.

    When the agent detects that the ontology structure needs updating
    (new entity type, new relationship, schema change), it creates
    a proposal rather than silently modifying the model.
    """
    id: str = ""
    title: str = ""
    description: str = ""
    change_type: str = ""
    proposed_by: str = "agent"
    approved: bool | None = None
    approved_by_id: str = ""
