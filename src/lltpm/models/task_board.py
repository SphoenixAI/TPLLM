"""Task board models — AI-native project tracking that replaces external tools.

This is LLTPM's built-in task board. Unlike Asana/Jira, it:
- Auto-extracts tasks from Zoom AI meeting notes
- Reasons about priority based on risks, dependencies, and deadlines
- Balances workload across team members
- Links tasks to meetings, decisions, and RAID items
- Supports sprint/iteration planning with capacity awareness
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    BACKLOG = "BACKLOG"
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class TaskPriority(str, Enum):
    CRITICAL = "CRITICAL"  # Blocks release / affects other teams
    HIGH = "HIGH"          # Must be done this sprint
    MEDIUM = "MEDIUM"      # Should be done this sprint
    LOW = "LOW"            # Nice to have


class TaskSource(str, Enum):
    """Where the task originated — key for traceability."""
    ZOOM_AI_NOTES = "ZOOM_AI_NOTES"      # Auto-extracted from meeting notes
    MEETING_ACTION = "MEETING_ACTION"     # Explicit action item from meeting
    RISK_MITIGATION = "RISK_MITIGATION"   # Generated from RAID risk
    DEPENDENCY = "DEPENDENCY"             # Tracking an external dependency
    SCOPE_CHANGE = "SCOPE_CHANGE"         # From approved scope change
    MANUAL = "MANUAL"                     # Created directly by user/agent
    BUG = "BUG"                           # From bug triage
    ESCALATION = "ESCALATION"             # Escalated from nudge/delegation


class BoardTask(BaseModel):
    """A single task on the board — the atomic unit of work.

    Unlike raw delegation items, tasks carry reasoning context:
    why they were created, what they block, and how confident
    the system is in the priority assignment.
    """

    id: str = ""
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.BACKLOG
    priority: TaskPriority = TaskPriority.MEDIUM

    # Assignment
    assignee: str = ""
    reviewer: str = ""

    # Effort & sizing
    effort_hours: float = 0.0  # estimated hours
    actual_hours: float = 0.0  # tracked hours

    # Traceability
    source: TaskSource = TaskSource.MANUAL
    source_id: str = ""  # meeting ID, risk ID, etc.
    source_context: str = ""  # extracted quote or context

    # Relationships
    sprint_id: str = ""
    parent_task_id: str = ""  # for subtask hierarchy
    blocks: list[str] = Field(default_factory=list)  # task IDs this blocks
    blocked_by: list[str] = Field(default_factory=list)  # task IDs blocking this
    related_risks: list[str] = Field(default_factory=list)  # RAID risk IDs
    tags: list[str] = Field(default_factory=list)

    # AI reasoning
    priority_rationale: str = ""  # why the AI assigned this priority
    confidence: float = 1.0  # 0-1, how confident the extraction is

    # Timing
    due_date: Optional[date] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    @property
    def is_overdue(self) -> bool:
        return (
            self.due_date is not None
            and self.due_date < date.today()
            and self.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)
        )

    @property
    def is_open(self) -> bool:
        return self.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)

    @property
    def days_until_due(self) -> int | None:
        if self.due_date is None:
            return None
        return (self.due_date - date.today()).days


class Sprint(BaseModel):
    """A time-boxed iteration for grouping tasks."""

    id: str = ""
    name: str
    goal: str = ""
    start_date: date
    end_date: date

    # Capacity
    team_capacity_hours: float = 0.0  # total hours available
    committed_hours: float = 0.0  # sum of task effort_hours

    # Status
    is_active: bool = False
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def days_remaining(self) -> int:
        return max(0, (self.end_date - date.today()).days)

    @property
    def is_over_capacity(self) -> bool:
        return self.committed_hours > self.team_capacity_hours > 0

    @property
    def utilization(self) -> float:
        if self.team_capacity_hours <= 0:
            return 0.0
        return self.committed_hours / self.team_capacity_hours


class TaskBoard(BaseModel):
    """The full task board for a program — AI-managed work tracking."""

    program_id: str = ""
    tasks: list[BoardTask] = Field(default_factory=list)
    sprints: list[Sprint] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Query helpers ──────────────────────────────────────────

    @property
    def backlog(self) -> list[BoardTask]:
        return [t for t in self.tasks if t.status == TaskStatus.BACKLOG]

    @property
    def active_sprint(self) -> Sprint | None:
        for s in self.sprints:
            if s.is_active:
                return s
        return None

    @property
    def open_tasks(self) -> list[BoardTask]:
        return [t for t in self.tasks if t.is_open]

    @property
    def overdue_tasks(self) -> list[BoardTask]:
        return [t for t in self.tasks if t.is_overdue]

    @property
    def blocked_tasks(self) -> list[BoardTask]:
        return [t for t in self.tasks if t.status == TaskStatus.BLOCKED]

    def tasks_by_status(self) -> dict[str, list[BoardTask]]:
        result: dict[str, list[BoardTask]] = {}
        for t in self.tasks:
            result.setdefault(t.status.value, []).append(t)
        return result

    def tasks_by_assignee(self) -> dict[str, list[BoardTask]]:
        result: dict[str, list[BoardTask]] = {}
        for t in self.open_tasks:
            result.setdefault(t.assignee or "UNASSIGNED", []).append(t)
        return result

    def sprint_tasks(self, sprint_id: str) -> list[BoardTask]:
        return [t for t in self.tasks if t.sprint_id == sprint_id]

    def tasks_from_source(self, source: TaskSource) -> list[BoardTask]:
        return [t for t in self.tasks if t.source == source]

    def get_task(self, task_id: str) -> BoardTask | None:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def summary(self) -> str:
        by_status = self.tasks_by_status()
        sprint = self.active_sprint

        lines = ["# Task Board"]

        if sprint:
            lines.append(
                f"\n**Active Sprint:** {sprint.name} | "
                f"{sprint.days_remaining}d remaining | "
                f"{sprint.utilization:.0%} capacity"
            )

        lines.append(f"\nTotal: {len(self.tasks)} | Open: {len(self.open_tasks)} | "
                      f"Overdue: {len(self.overdue_tasks)} | Blocked: {len(self.blocked_tasks)}")

        # Board columns
        for status in TaskStatus:
            tasks = by_status.get(status.value, [])
            if tasks:
                lines.append(f"\n## {status.value} ({len(tasks)})")
                for t in sorted(tasks, key=lambda x: x.priority.value):
                    assignee = f" → {t.assignee}" if t.assignee else ""
                    due = f" (due {t.due_date})" if t.due_date else ""
                    source_tag = f" [{t.source.value}]" if t.source != TaskSource.MANUAL else ""
                    lines.append(
                        f"- [{t.priority.value}] {t.title}{assignee}{due}{source_tag}"
                    )

        return "\n".join(lines)
