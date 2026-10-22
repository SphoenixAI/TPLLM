"""Models for Scope Lock — change control that prevents scope creep.

Every scope change must go through a formal process:
1. Request with justification
2. Impact analysis (timeline, resources, dependencies)
3. Approval from designated authority
4. Documentation of the change
"""

from __future__ import annotations

from datetime import datetime, date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ScopeChangeStatus(str, Enum):
    PROPOSED = "PROPOSED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class ScopeChangeType(str, Enum):
    ADD_FEATURE = "ADD_FEATURE"
    REMOVE_FEATURE = "REMOVE_FEATURE"
    MODIFY_REQUIREMENT = "MODIFY_REQUIREMENT"
    TIMELINE_CHANGE = "TIMELINE_CHANGE"
    RESOURCE_CHANGE = "RESOURCE_CHANGE"
    DEPENDENCY_CHANGE = "DEPENDENCY_CHANGE"
    OWNERSHIP_CHANGE = "OWNERSHIP_CHANGE"


class ImpactLevel(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ScopeItem(BaseModel):
    """A single item in the program scope baseline."""

    id: str = ""
    title: str
    description: str = ""
    owner: str = ""
    target_date: Optional[date] = None
    is_locked: bool = False       # Locked items require change request
    locked_by: str = ""
    locked_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ImpactAnalysis(BaseModel):
    """Impact analysis for a proposed scope change."""

    timeline_impact: ImpactLevel = ImpactLevel.NONE
    timeline_detail: str = ""
    resource_impact: ImpactLevel = ImpactLevel.NONE
    resource_detail: str = ""
    dependency_impact: ImpactLevel = ImpactLevel.NONE
    dependency_detail: str = ""
    risk_impact: ImpactLevel = ImpactLevel.NONE
    risk_detail: str = ""
    overall_impact: ImpactLevel = ImpactLevel.NONE

    def compute_overall(self) -> None:
        """Set overall to the highest individual impact."""
        levels = [self.timeline_impact, self.resource_impact,
                  self.dependency_impact, self.risk_impact]
        order = [ImpactLevel.NONE, ImpactLevel.LOW, ImpactLevel.MEDIUM,
                 ImpactLevel.HIGH, ImpactLevel.CRITICAL]
        self.overall_impact = max(levels, key=lambda x: order.index(x))


class ScopeChangeRequest(BaseModel):
    """A formal request to change program scope."""

    id: str = ""
    program_id: str = ""
    title: str
    description: str = ""
    change_type: ScopeChangeType = ScopeChangeType.MODIFY_REQUIREMENT
    status: ScopeChangeStatus = ScopeChangeStatus.PROPOSED

    # Who and why
    requested_by: str = ""
    justification: str = ""       # Why this change is needed
    business_value: str = ""      # What value does this add

    # What changes
    affected_items: list[str] = Field(default_factory=list)  # Scope item IDs
    new_timeline: Optional[date] = None
    new_owner: str = ""

    # Impact
    impact: ImpactAnalysis = Field(default_factory=ImpactAnalysis)

    # Approval
    approver: str = ""            # Who needs to approve
    approved_by: str = ""
    approval_rationale: str = ""
    rejection_reason: str = ""

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None

    def summary(self) -> str:
        lines = [
            f"**SCR-{self.id}**: {self.title}",
            f"Type: {self.change_type.value} | Status: {self.status.value}",
            f"Requested by: {self.requested_by}",
            f"Justification: {self.justification}",
            f"Impact: {self.impact.overall_impact.value}",
        ]
        if self.status == ScopeChangeStatus.REJECTED:
            lines.append(f"Rejected: {self.rejection_reason}")
        return "\n".join(lines)


class ScopeBaseline(BaseModel):
    """The locked scope baseline for a program."""

    program_id: str = ""
    items: list[ScopeItem] = Field(default_factory=list)
    change_requests: list[ScopeChangeRequest] = Field(default_factory=list)
    frozen: bool = False          # When True, ALL changes require SCR
    frozen_at: Optional[datetime] = None
    frozen_by: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def pending_changes(self) -> list[ScopeChangeRequest]:
        return [cr for cr in self.change_requests
                if cr.status in (ScopeChangeStatus.PROPOSED, ScopeChangeStatus.UNDER_REVIEW)]

    @property
    def approved_changes(self) -> list[ScopeChangeRequest]:
        return [cr for cr in self.change_requests if cr.status == ScopeChangeStatus.APPROVED]

    def summary(self) -> str:
        locked = len([i for i in self.items if i.is_locked])
        lines = [
            f"# Scope Baseline — {self.program_id}",
            f"Items: {len(self.items)} ({locked} locked) | "
            f"Frozen: {'YES' if self.frozen else 'no'}",
            f"Pending changes: {len(self.pending_changes)} | "
            f"Approved changes: {len(self.approved_changes)}",
        ]
        if self.pending_changes:
            lines.append("\n## Pending Scope Changes")
            for cr in self.pending_changes:
                lines.append(f"- [{cr.change_type.value}] {cr.title} — by {cr.requested_by} — Impact: {cr.impact.overall_impact.value}")
        return "\n".join(lines)
