"""Models for the delegation engine — intelligent work routing and assignment."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DelegationPriority(str, Enum):
    P0 = "P0"  # Drop everything
    P1 = "P1"  # Today
    P2 = "P2"  # This week
    P3 = "P3"  # This sprint
    P4 = "P4"  # Backlog


class DelegationCategory(str, Enum):
    """The type of work being delegated."""

    DECISION = "DECISION"
    INVESTIGATION = "INVESTIGATION"
    EXECUTION = "EXECUTION"
    REVIEW = "REVIEW"
    COMMUNICATION = "COMMUNICATION"
    BLOCKER_RESOLUTION = "BLOCKER_RESOLUTION"
    RISK_MITIGATION = "RISK_MITIGATION"
    DEPENDENCY_CHASE = "DEPENDENCY_CHASE"
    DOCUMENTATION = "DOCUMENTATION"
    ESCALATION = "ESCALATION"


class DelegationStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    REASSIGNED = "REASSIGNED"


class TeamMemberProfile(BaseModel):
    """Profile of a team member for intelligent routing."""

    name: str
    role: str = ""
    team: str = ""
    expertise: list[str] = Field(default_factory=list)
    current_load: int = 0  # number of active delegations
    max_load: int = 5
    availability: str = "available"  # available, busy, ooo

    @property
    def has_capacity(self) -> bool:
        return self.current_load < self.max_load and self.availability == "available"

    @property
    def load_factor(self) -> float:
        return self.current_load / self.max_load if self.max_load > 0 else 1.0


class DelegationItem(BaseModel):
    """A single delegated work item with full context."""

    id: str = ""
    title: str
    description: str = ""
    category: DelegationCategory = DelegationCategory.EXECUTION
    priority: DelegationPriority = DelegationPriority.P2
    status: DelegationStatus = DelegationStatus.PROPOSED

    # Assignment
    assigned_to: str = ""
    assigned_by: str = "TPLLM"
    assignment_rationale: str = ""  # why this person

    # Context
    program_id: str = ""
    source: str = ""  # where this came from (meeting, risk scan, etc.)
    source_id: str = ""  # reference to the source item
    context: str = ""  # full context for the assignee
    success_criteria: str = ""  # what "done" looks like

    # Timing
    due_date: Optional[date] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    # Tracking
    follow_up_count: int = 0
    escalated: bool = False
    blockers: list[str] = Field(default_factory=list)

    @property
    def is_overdue(self) -> bool:
        return (
            self.due_date is not None
            and self.due_date < date.today()
            and self.status not in (DelegationStatus.COMPLETED,)
        )

    @property
    def days_until_due(self) -> int | None:
        if self.due_date is None:
            return None
        return (self.due_date - date.today()).days


class DelegationPlan(BaseModel):
    """A complete delegation plan — who does what, by when, and why."""

    program_id: str = ""
    items: list[DelegationItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def open_items(self) -> list[DelegationItem]:
        return [i for i in self.items if i.status not in (DelegationStatus.COMPLETED,)]

    @property
    def overdue_items(self) -> list[DelegationItem]:
        return [i for i in self.items if i.is_overdue]

    @property
    def blocked_items(self) -> list[DelegationItem]:
        return [i for i in self.items if i.status == DelegationStatus.BLOCKED]

    @property
    def by_assignee(self) -> dict[str, list[DelegationItem]]:
        result: dict[str, list[DelegationItem]] = {}
        for item in self.open_items:
            result.setdefault(item.assigned_to or "UNASSIGNED", []).append(item)
        return result

    def summary(self) -> str:
        lines = [
            "# Delegation Plan",
            f"Open: {len(self.open_items)} | Overdue: {len(self.overdue_items)} | "
            f"Blocked: {len(self.blocked_items)}",
        ]

        if self.overdue_items:
            lines.append("\n## Overdue")
            for item in self.overdue_items:
                lines.append(
                    f"- [{item.priority.value}] **{item.title}** — "
                    f"Owner: {item.assigned_to} — Due: {item.due_date}"
                )

        if self.blocked_items:
            lines.append("\n## Blocked")
            for item in self.blocked_items:
                blockers = ", ".join(item.blockers) if item.blockers else "Unknown"
                lines.append(
                    f"- [{item.priority.value}] **{item.title}** — "
                    f"Owner: {item.assigned_to} — Blockers: {blockers}"
                )

        for assignee, items in self.by_assignee.items():
            active = [i for i in items if not i.is_overdue and i.status != DelegationStatus.BLOCKED]
            if active:
                lines.append(f"\n## {assignee} ({len(active)} active)")
                for item in sorted(active, key=lambda x: x.priority.value):
                    due = item.due_date.isoformat() if item.due_date else "TBD"
                    lines.append(
                        f"- [{item.priority.value}] {item.title} ({item.category.value}) — Due: {due}"
                    )

        return "\n".join(lines)
