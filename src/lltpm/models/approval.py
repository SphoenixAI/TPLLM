"""Approval workflow models — formal sign-off chains with tracking."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"


class ApprovalType(str, Enum):
    """Types of items that require approval."""

    SCOPE_CHANGE = "SCOPE_CHANGE"
    LAUNCH_READINESS = "LAUNCH_READINESS"
    BUDGET_REQUEST = "BUDGET_REQUEST"
    TIMELINE_CHANGE = "TIMELINE_CHANGE"
    RESOURCE_REQUEST = "RESOURCE_REQUEST"
    RISK_ACCEPTANCE = "RISK_ACCEPTANCE"
    EXCEPTION_REQUEST = "EXCEPTION_REQUEST"


class ApprovalStep(BaseModel):
    """A single step in an approval chain."""

    order: int = 0
    approver: str  # person or role who must approve
    role: str = ""  # their role context
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: Optional[datetime] = None
    comment: str = ""
    delegated_to: str = ""  # if the approver delegated to someone else

    @property
    def is_complete(self) -> bool:
        return self.status in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.WITHDRAWN,
        )


class ApprovalRequest(BaseModel):
    """A formal request requiring one or more approvals."""

    id: str = ""
    program_id: str = ""
    title: str
    approval_type: ApprovalType = ApprovalType.SCOPE_CHANGE
    description: str = ""
    justification: str = ""
    impact_summary: str = ""  # what happens if approved / rejected
    evidence_refs: list[str] = Field(default_factory=list)  # links to supporting docs

    # Workflow
    requester: str = ""
    steps: list[ApprovalStep] = Field(default_factory=list)
    status: ApprovalStatus = ApprovalStatus.PENDING
    current_step: int = 0

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    deadline: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    escalated_at: Optional[datetime] = None

    # Result
    final_decision: str = ""
    final_comment: str = ""

    @property
    def is_complete(self) -> bool:
        return self.status in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.WITHDRAWN,
            ApprovalStatus.EXPIRED,
        )

    @property
    def next_approver(self) -> str:
        if self.current_step < len(self.steps):
            step = self.steps[self.current_step]
            return step.delegated_to or step.approver
        return ""

    @property
    def approval_progress(self) -> str:
        approved = sum(1 for s in self.steps if s.status == ApprovalStatus.APPROVED)
        return f"{approved}/{len(self.steps)}"

    def summary(self) -> str:
        lines = [
            f"**{self.id}**: {self.title}",
            f"Type: {self.approval_type.value} | Status: {self.status.value}",
            f"Progress: {self.approval_progress}",
            f"Requester: {self.requester}",
        ]
        if self.next_approver:
            lines.append(f"Next approver: {self.next_approver}")
        if self.deadline:
            lines.append(f"Deadline: {self.deadline.isoformat()}")
        for step in self.steps:
            icon = {"APPROVED": "+", "REJECTED": "x", "PENDING": "?"}.get(
                step.status.value, "-"
            )
            lines.append(f"  [{icon}] {step.approver} ({step.role}): {step.status.value}")
            if step.comment:
                lines.append(f"      {step.comment}")
        return "\n".join(lines)


class ApprovalChain(BaseModel):
    """Collection of approval requests for a program."""

    program_id: str = ""
    requests: list[ApprovalRequest] = Field(default_factory=list)

    @property
    def pending(self) -> list[ApprovalRequest]:
        return [r for r in self.requests if r.status == ApprovalStatus.PENDING]

    @property
    def stale(self) -> list[ApprovalRequest]:
        """Requests pending for more than 48 hours."""
        cutoff = datetime.utcnow()
        return [
            r for r in self.requests
            if r.status == ApprovalStatus.PENDING
            and (cutoff - r.created_at).total_seconds() > 48 * 3600
        ]

    def summary(self) -> str:
        lines = [
            f"# Approval Chain — {self.program_id}",
            f"Total: {len(self.requests)} | Pending: {len(self.pending)} | "
            f"Stale: {len(self.stale)}",
        ]
        if self.pending:
            lines.append("\n## Pending Approvals")
            for r in self.pending:
                lines.append(f"\n{r.summary()}")
        return "\n".join(lines)
