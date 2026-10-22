"""Core ontology primitives — source refs, AI-extracted values, evidence claims.

These are the atomic building blocks of the ontology. Every canonical entity
references back to sources through SourceRef, carries AI-generated values
with confidence metadata through AIExtractedValue, and builds a provenance
chain through EvidenceClaim.

Design principle (Palantir-aligned): store each fact once, track where it
came from, how confident the extraction is, and whether a human confirmed it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


# ── Source Systems ───────────────────────────────────────────────────

class SourceSystem(str, Enum):
    GITHUB = "github"
    JIRA = "jira"
    LINEAR = "linear"
    SLACK = "slack"
    ZOOM = "zoom"
    GOOGLE_CALENDAR = "google_calendar"
    MICROSOFT_CALENDAR = "microsoft_calendar"
    NOTION = "notion"
    SENDGRID = "sendgrid"
    TASK_BOARD = "task_board"
    AGENT = "agent"
    MANUAL = "manual"


class SourceRef(BaseModel):
    """Pointer back to a raw source record.

    Every fact in the ontology can trace back to one or more source refs,
    giving full provenance for agent reasoning and human audit.
    """
    system: SourceSystem
    external_id: str = ""
    url: str = ""
    observed_at: datetime = Field(default_factory=datetime.utcnow)
    author_id: str = ""
    raw_object_id: str = ""


# ── AI-Extracted Values ─────────────────────────────────────────────

T = TypeVar("T")


class AIExtractedValue(BaseModel, Generic[T]):
    """Wraps any AI-generated field with extraction metadata.

    LLM outputs need the value itself plus: source references, confidence,
    who/what extracted it, and whether a human has reviewed it. This struct
    replaces scattered *_confidence, *_source, *_generated_at fields.
    """
    value: Any  # Generic[T] — Pydantic serializes via Any
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_refs: list[SourceRef] = Field(default_factory=list)
    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    extractor: str = ""
    reasoning_summary: str = ""
    human_reviewed: bool = False
    reviewer_id: str = ""


# ── Evidence Claims ─────────────────────────────────────────────────

class ClaimType(str, Enum):
    STATUS_UPDATE = "status_update"
    OWNERSHIP = "ownership"
    BLOCKER = "blocker"
    RISK = "risk"
    DECISION = "decision"
    REQUIREMENT = "requirement"
    DEPENDENCY = "dependency"
    TIMELINE = "timeline"
    SCOPE_CHANGE = "scope_change"
    COMMITMENT = "commitment"
    PROGRESS = "progress"
    SENTIMENT = "sentiment"


class EvidenceClaim(BaseModel):
    """An extracted assertion from source material.

    A claim is not just "true" — it records who said it, where, when,
    how confident the extraction is, and whether it's contradicted.
    This is the fundamental unit of the agent's knowledge graph.
    """
    id: str = ""
    claim_text: str = ""
    claim_type: ClaimType = ClaimType.STATUS_UPDATE
    source_refs: list[SourceRef] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    observed_at: datetime = Field(default_factory=datetime.utcnow)
    subject_object_id: str = ""
    related_object_ids: list[str] = Field(default_factory=list)
    contradicted_by_claim_ids: list[str] = Field(default_factory=list)
    supported_by_claim_ids: list[str] = Field(default_factory=list)
    human_reviewed: bool = False
    valid_from: datetime | None = None
    valid_until: datetime | None = None


# ── Contradictions ──────────────────────────────────────────────────

class ContradictionSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ContradictionStatus(str, Enum):
    OPEN = "open"
    REVIEWING = "reviewing"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class Contradiction(BaseModel):
    """Detected conflict between two or more evidence claims.

    When multiple sources disagree (e.g., Slack says "done" but Linear
    says "in progress"), the system creates a Contradiction for the
    agent or a human to resolve.
    """
    id: str = ""
    title: str = ""
    conflicting_claim_ids: list[str] = Field(default_factory=list)
    affected_object_ids: list[str] = Field(default_factory=list)
    severity: ContradictionSeverity = ContradictionSeverity.MEDIUM
    status: ContradictionStatus = ContradictionStatus.OPEN
    suggested_resolution_owner_id: str = ""
    resolution_notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
