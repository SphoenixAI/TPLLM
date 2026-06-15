"""Ontology action types — real business operations, not CRUD.

Palantir anti-pattern: "Action Sprawl" — many single-property actions
instead of cohesive business operations. TPM work is about meaningful
workflows, not field-level updates.

Each action bundles related changes into a domain-meaningful operation
that the agent or a human can execute.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from lltpm.ontology.core import SourceRef


class ActionType(str, Enum):
    TRIAGE_ISSUE = "triage_issue"
    ESCALATE_BLOCKER = "escalate_blocker"
    RESOLVE_DEPENDENCY = "resolve_dependency"
    CONVERT_RISK_TO_ISSUE = "convert_risk_to_issue"
    CONFIRM_DECISION = "confirm_decision"
    CREATE_SPRINT_PLAN = "create_sprint_plan"
    GENERATE_STATUS_REPORT = "generate_status_report"
    REQUEST_OWNER_UPDATE = "request_owner_update"
    MARK_DECISION_DISPUTED = "mark_decision_disputed"
    MERGE_DUPLICATE_WORK_ITEMS = "merge_duplicate_work_items"
    CREATE_FOLLOW_UP_FROM_MEETING = "create_follow_up_from_meeting"
    PROMOTE_SIGNAL_TO_CLAIM = "promote_signal_to_claim"
    RESOLVE_CONTRADICTION = "resolve_contradiction"


class OntologyAction(BaseModel):
    """Base for all ontology actions."""
    id: str = ""
    action_type: ActionType
    initiated_by: str = ""
    initiated_at: datetime = Field(default_factory=datetime.utcnow)
    program_id: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)
    completed: bool = False
    result_summary: str = ""


class TriageIssueAction(OntologyAction):
    """Triage a new issue: set severity, assign owner, link impacts."""
    action_type: ActionType = ActionType.TRIAGE_ISSUE
    issue_id: str = ""
    severity: str = ""
    owner_id: str = ""
    impacted_milestone_ids: list[str] = Field(default_factory=list)
    linked_evidence_claim_ids: list[str] = Field(default_factory=list)
    next_action_title: str = ""
    next_action_owner_id: str = ""
    next_action_due_date: date | None = None


class EscalateBlockerAction(OntologyAction):
    """Escalate a blocker to a higher authority."""
    action_type: ActionType = ActionType.ESCALATE_BLOCKER
    blocker_object_id: str = ""
    escalate_to_id: str = ""
    blocked_work_item_ids: list[str] = Field(default_factory=list)
    urgency: str = "high"
    context: str = ""


class ResolveDependencyAction(OntologyAction):
    """Mark a dependency as resolved with evidence."""
    action_type: ActionType = ActionType.RESOLVE_DEPENDENCY
    dependency_id: str = ""
    resolution: str = ""
    evidence_claim_ids: list[str] = Field(default_factory=list)
    unblocked_work_item_ids: list[str] = Field(default_factory=list)


class ConvertRiskToIssueAction(OntologyAction):
    """A risk has occurred — convert it to an active issue."""
    action_type: ActionType = ActionType.CONVERT_RISK_TO_ISSUE
    risk_id: str = ""
    new_issue_id: str = ""
    severity: str = ""
    owner_id: str = ""
    mitigation_work_item_ids: list[str] = Field(default_factory=list)


class ConfirmDecisionAction(OntologyAction):
    """Confirm a decision, moving it from candidate to decided."""
    action_type: ActionType = ActionType.CONFIRM_DECISION
    decision_id: str = ""
    decided_by_id: str = ""
    rationale: str = ""
    follow_up_action_ids: list[str] = Field(default_factory=list)


class CreateSprintPlanAction(OntologyAction):
    """Create a sprint plan by selecting work items from backlog."""
    action_type: ActionType = ActionType.CREATE_SPRINT_PLAN
    sprint_id: str = ""
    selected_work_item_ids: list[str] = Field(default_factory=list)
    total_committed_hours: float = 0.0
    capacity_hours: float = 0.0
    planning_rationale: str = ""


class GenerateStatusReportAction(OntologyAction):
    """Generate a status report from current ontology state."""
    action_type: ActionType = ActionType.GENERATE_STATUS_REPORT
    report_id: str = ""
    audience: str = ""
    format: str = "html"


class RequestOwnerUpdateAction(OntologyAction):
    """Request a status update from a work item owner."""
    action_type: ActionType = ActionType.REQUEST_OWNER_UPDATE
    target_person_id: str = ""
    work_item_ids: list[str] = Field(default_factory=list)
    channel: str = "slack"
    message: str = ""


class MarkDecisionDisputedAction(OntologyAction):
    """Flag a decision as disputed — requires re-review."""
    action_type: ActionType = ActionType.MARK_DECISION_DISPUTED
    decision_id: str = ""
    disputed_by_id: str = ""
    dispute_reason: str = ""
    contradicting_evidence_ids: list[str] = Field(default_factory=list)


class MergeDuplicateWorkItemsAction(OntologyAction):
    """Merge two or more duplicate work items into one canonical item."""
    action_type: ActionType = ActionType.MERGE_DUPLICATE_WORK_ITEMS
    canonical_work_item_id: str = ""
    duplicate_work_item_ids: list[str] = Field(default_factory=list)
    merge_strategy: str = "keep_canonical"


class CreateFollowUpFromMeetingAction(OntologyAction):
    """Create follow-up actions from a meeting's extracted commitments."""
    action_type: ActionType = ActionType.CREATE_FOLLOW_UP_FROM_MEETING
    meeting_id: str = ""
    created_action_item_ids: list[str] = Field(default_factory=list)
    created_work_item_ids: list[str] = Field(default_factory=list)
    created_risk_ids: list[str] = Field(default_factory=list)
    created_decision_ids: list[str] = Field(default_factory=list)


class PromoteSignalToClaimAction(OntologyAction):
    """Promote a weak signal to a confirmed evidence claim."""
    action_type: ActionType = ActionType.PROMOTE_SIGNAL_TO_CLAIM
    signal_id: str = ""
    new_claim_id: str = ""
    promoted_confidence: float = 0.0


class ResolveContradictionAction(OntologyAction):
    """Resolve a contradiction between conflicting claims."""
    action_type: ActionType = ActionType.RESOLVE_CONTRADICTION
    contradiction_id: str = ""
    winning_claim_id: str = ""
    losing_claim_ids: list[str] = Field(default_factory=list)
    resolution_rationale: str = ""
