"""Status update and action item data models."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class UpdateSeverity(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class ActionItemStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    BLOCKED = "BLOCKED"


class ActionItem(BaseModel):
    id: str = ""
    description: str
    owner: str
    due_date: Optional[date] = None
    status: ActionItemStatus = ActionItemStatus.OPEN
    source: str = ""  # e.g. "standup 2024-03-15", "decision D-042"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_overdue(self) -> bool:
        return (
            self.due_date is not None
            and self.due_date < date.today()
            and self.status not in (ActionItemStatus.DONE,)
        )


class StatusUpdate(BaseModel):
    """A structured status update for a program."""

    program_id: str = ""
    program_name: str = ""
    report_date: date = Field(default_factory=date.today)
    overall_health: UpdateSeverity = UpdateSeverity.GREEN
    progress: list[str] = Field(default_factory=list)
    top_risks: list[str] = Field(default_factory=list)
    asks: list[str] = Field(default_factory=list)
    upcoming: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def render(self) -> str:
        lines = [
            f"## {self.program_name} — Status {self.report_date.isoformat()}",
            f"**Overall: {self.overall_health.value}**",
            "",
            "### Progress",
        ]
        for p in self.progress:
            lines.append(f"- {p}")
        lines.append("")
        lines.append("### Top Risks")
        for i, r in enumerate(self.top_risks, 1):
            lines.append(f"{i}. {r}")
        lines.append("")
        lines.append("### Asks")
        for a in self.asks:
            lines.append(f"- {a}")
        lines.append("")
        lines.append("### Upcoming")
        for u in self.upcoming:
            lines.append(f"- {u}")
        return "\n".join(lines)
