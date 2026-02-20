"""Program / project-level data models."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ProgramHealth(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class Stakeholder(BaseModel):
    name: str
    role: str  # e.g. "Eng Lead", "PM", "Design Lead"
    team: str = ""
    contact: str = ""  # email or Slack handle


class SuccessMetric(BaseModel):
    name: str
    target: str
    current: Optional[str] = None
    measurement_method: str = ""


class Program(BaseModel):
    """A program or project being managed by the TPM agent."""

    id: str = Field(default_factory=lambda: "")
    name: str
    goal: str
    non_goals: list[str] = Field(default_factory=list)
    success_metrics: list[SuccessMetric] = Field(default_factory=list)
    stakeholders: list[Stakeholder] = Field(default_factory=list)
    start_date: Optional[date] = None
    target_date: Optional[date] = None
    health: ProgramHealth = ProgramHealth.GREEN
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def summary(self) -> str:
        lines = [
            f"# {self.name}",
            f"**Goal**: {self.goal}",
        ]
        if self.non_goals:
            lines.append(f"**Non-goals**: {', '.join(self.non_goals)}")
        if self.target_date:
            lines.append(f"**Target date**: {self.target_date.isoformat()}")
        lines.append(f"**Health**: {self.health.value}")
        if self.stakeholders:
            lines.append("\n**Stakeholders**:")
            for s in self.stakeholders:
                lines.append(f"- {s.name} ({s.role}, {s.team})")
        if self.success_metrics:
            lines.append("\n**Success Metrics**:")
            for m in self.success_metrics:
                cur = f" — current: {m.current}" if m.current else ""
                lines.append(f"- {m.name}: target={m.target}{cur}")
        return "\n".join(lines)
