"""Decision log data models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DecisionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    DECIDED = "DECIDED"
    REVISITED = "REVISITED"


class DecisionOption(BaseModel):
    label: str
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    chosen: bool = False


class Decision(BaseModel):
    id: str = ""
    title: str
    context: str = ""
    options: list[DecisionOption] = Field(default_factory=list)
    decision: str = ""
    rationale: str = ""
    decided_by: str = ""
    status: DecisionStatus = DecisionStatus.PROPOSED
    follow_ups: list[str] = Field(default_factory=list)
    decided_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def summary(self) -> str:
        status = self.status.value
        decider = f" by {self.decided_by}" if self.decided_by else ""
        lines = [f"**[{status}]** {self.title}{decider}"]
        if self.decision:
            lines.append(f"  Decision: {self.decision}")
        if self.follow_ups:
            lines.append("  Follow-ups: " + "; ".join(self.follow_ups))
        return "\n".join(lines)


class DecisionLog(BaseModel):
    """Append-only log of program decisions."""

    program_id: str = ""
    decisions: list[Decision] = Field(default_factory=list)

    @property
    def pending(self) -> list[Decision]:
        return [d for d in self.decisions if d.status == DecisionStatus.PROPOSED]

    def summary(self) -> str:
        lines = [
            "# Decision Log",
            f"Total: {len(self.decisions)} | Pending: {len(self.pending)}",
        ]
        if self.pending:
            lines.append("\n## Pending Decisions")
            for d in self.pending:
                lines.append(f"- {d.title}")
        recent = sorted(
            [d for d in self.decisions if d.status == DecisionStatus.DECIDED],
            key=lambda d: d.decided_at or d.created_at,
            reverse=True,
        )[:5]
        if recent:
            lines.append("\n## Recent Decisions")
            for d in recent:
                lines.append(d.summary())
        return "\n".join(lines)
