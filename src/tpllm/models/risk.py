"""RAID (Risks, Assumptions, Issues, Dependencies) data models."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Probability(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RAIDItemStatus(str, Enum):
    OPEN = "OPEN"
    MITIGATING = "MITIGATING"
    RESOLVED = "RESOLVED"
    ACCEPTED = "ACCEPTED"


class Risk(BaseModel):
    id: str = ""
    title: str
    description: str = ""
    severity: Severity = Severity.MEDIUM
    probability: Probability = Probability.MEDIUM
    impact: str = ""
    mitigation: str = ""
    trigger_conditions: str = ""
    owner: str = ""
    status: RAIDItemStatus = RAIDItemStatus.OPEN
    due_date: Optional[date] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Assumption(BaseModel):
    id: str = ""
    title: str
    description: str = ""
    validation_method: str = ""
    owner: str = ""
    status: RAIDItemStatus = RAIDItemStatus.OPEN
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Issue(BaseModel):
    id: str = ""
    title: str
    description: str = ""
    severity: Severity = Severity.MEDIUM
    impact: str = ""
    resolution: str = ""
    owner: str = ""
    status: RAIDItemStatus = RAIDItemStatus.OPEN
    due_date: Optional[date] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Dependency(BaseModel):
    id: str = ""
    title: str
    description: str = ""
    upstream_team: str = ""
    downstream_team: str = ""
    owner: str = ""
    commitment_date: Optional[date] = None
    status: RAIDItemStatus = RAIDItemStatus.OPEN
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RAIDLog(BaseModel):
    """Aggregate RAID log for a program."""

    program_id: str = ""
    risks: list[Risk] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)

    @property
    def open_risks(self) -> list[Risk]:
        return [r for r in self.risks if r.status in (RAIDItemStatus.OPEN, RAIDItemStatus.MITIGATING)]

    @property
    def open_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.status == RAIDItemStatus.OPEN]

    @property
    def open_dependencies(self) -> list[Dependency]:
        return [d for d in self.dependencies if d.status == RAIDItemStatus.OPEN]

    def summary(self) -> str:
        lines = [
            "# RAID Log Summary",
            f"- Open risks: {len(self.open_risks)}",
            f"- Open issues: {len(self.open_issues)}",
            f"- Open dependencies: {len(self.open_dependencies)}",
            f"- Assumptions: {len(self.assumptions)}",
        ]
        critical = [r for r in self.open_risks if r.severity == Severity.CRITICAL]
        if critical:
            lines.append("\n## Critical Risks")
            for r in critical:
                lines.append(f"- **{r.title}**: {r.impact} — Owner: {r.owner}")
        return "\n".join(lines)
