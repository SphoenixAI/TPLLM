"""Milestone and planning data models."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MilestoneStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    AT_RISK = "AT_RISK"
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"


class GateCriterion(BaseModel):
    """Entry or exit criterion for a milestone gate."""

    description: str
    met: bool = False
    owner: str = ""
    evidence: str = ""


class Milestone(BaseModel):
    id: str = ""
    name: str
    description: str = ""
    phase: str = ""
    target_date: Optional[date] = None
    actual_date: Optional[date] = None
    status: MilestoneStatus = MilestoneStatus.NOT_STARTED
    owner: str = ""
    entry_criteria: list[GateCriterion] = Field(default_factory=list)
    exit_criteria: list[GateCriterion] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)  # milestone IDs
    on_critical_path: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def days_until_due(self) -> Optional[int]:
        if self.target_date is None:
            return None
        return (self.target_date - date.today()).days

    @property
    def is_overdue(self) -> bool:
        d = self.days_until_due
        return d is not None and d < 0 and self.status not in (
            MilestoneStatus.COMPLETED,
        )


class MilestonePlan(BaseModel):
    """Ordered milestone plan for a program."""

    program_id: str = ""
    milestones: list[Milestone] = Field(default_factory=list)

    @property
    def critical_path(self) -> list[Milestone]:
        return [m for m in self.milestones if m.on_critical_path]

    @property
    def at_risk(self) -> list[Milestone]:
        return [m for m in self.milestones if m.status == MilestoneStatus.AT_RISK]

    @property
    def overdue(self) -> list[Milestone]:
        return [m for m in self.milestones if m.is_overdue]

    def summary(self) -> str:
        lines = ["# Milestone Plan"]
        for m in self.milestones:
            due = m.target_date.isoformat() if m.target_date else "TBD"
            cp = " [CRITICAL PATH]" if m.on_critical_path else ""
            lines.append(f"- [{m.status.value}] {m.name} — due {due}{cp}")
        if self.at_risk:
            lines.append(f"\n**At risk**: {len(self.at_risk)} milestones")
        if self.overdue:
            lines.append(f"**Overdue**: {len(self.overdue)} milestones")
        return "\n".join(lines)
