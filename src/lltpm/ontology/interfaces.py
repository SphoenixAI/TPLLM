"""Ontology interfaces — composable capabilities for entity types.

Palantir principle: use interfaces for reusable abstractions and favor
composition over deep inheritance hierarchies. Each interface represents
a single capability that entity types can mix in.

Object types implement interfaces:
    WorkItem → Trackable, Assignable, Schedulable, EvidenceBacked, SourceMapped, Reportable
    Issue    → Trackable, Assignable, Escalatable, EvidenceBacked, Reportable
    Decision → Trackable, EvidenceBacked, SourceMapped
    Meeting  → Schedulable, EvidenceBacked, SourceMapped
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from lltpm.ontology.core import EvidenceClaim, SourceRef


# ── Trackable ───────────────────────────────────────────────────────

class Trackable(BaseModel):
    """Entity with lifecycle state tracking."""
    status: str = ""
    lifecycle_state: str = "active"
    last_updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Assignable ──────────────────────────────────────────────────────

class Assignable(BaseModel):
    """Entity that can be owned by a person or team."""
    owner_id: str = ""
    accountable_team_id: str = ""
    backup_owner_id: str = ""


# ── Schedulable ─────────────────────────────────────────────────────

class Schedulable(BaseModel):
    """Entity with time boundaries."""
    start_date: date | None = None
    due_date: date | None = None
    target_milestone_id: str = ""

    @property
    def is_overdue(self) -> bool:
        return self.due_date is not None and self.due_date < date.today()

    @property
    def days_until_due(self) -> int | None:
        if self.due_date is None:
            return None
        return (self.due_date - date.today()).days


# ── EvidenceBacked ──────────────────────────────────────────────────

class EvidenceBacked(BaseModel):
    """Entity whose properties are grounded in extracted evidence."""
    evidence_claim_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_refs: list[SourceRef] = Field(default_factory=list)


# ── Escalatable ─────────────────────────────────────────────────────

class Escalatable(BaseModel):
    """Entity that can be escalated when severity warrants it."""
    severity: str = "medium"
    escalation_owner_id: str = ""
    escalation_status: str = ""
    escalated_at: datetime | None = None


# ── SourceMapped ────────────────────────────────────────────────────

class SourceMapped(BaseModel):
    """Entity linked back to one or more external source systems.

    The canonical ontology entity is the source of truth. These links
    record where the data originally came from and which system wins
    when values conflict.
    """
    linked_sources: list[SourceRef] = Field(default_factory=list)
    canonical_id: str = ""
    source_precedence: list[str] = Field(default_factory=list)


# ── Reportable ──────────────────────────────────────────────────────

class Reportable(BaseModel):
    """Entity that appears in status reports and dashboards."""
    reporting_summary: str = ""
    health: str = "green"
    trend: str = "stable"
