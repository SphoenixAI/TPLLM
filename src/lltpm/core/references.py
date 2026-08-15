"""Core lightweight references (Commit 20).

Contracts only — never duplicates of the rich domain models. A
``ProjectEvidence`` (Commit 11) is owned by intelligence;
``EvidenceReferenceView`` here is just the pointer shape peer domains
pass to each other.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ProjectReference(BaseModel):
    project_id: str
    project_name: str | None = None


class WorkstreamReference(BaseModel):
    workstream_id: str
    project_id: str
    name: str | None = None


class EvidenceReferenceView(BaseModel):
    evidence_id: str
    source_system: str | None = None
    source_type: str | None = None
    observed_at: datetime | None = None


class TemporalReference(BaseModel):
    """A point-in-time anchor. Every cross-domain read model that must be
    reconstructable later carries one."""
    as_of: datetime
    observed_at: datetime | None = None
    valid_until: datetime | None = None
