"""Core evidence contract (Commit 20).

The provenance shape every domain agrees on. The rich evidence model
(``ProjectEvidence``, Commit 11) stays owned by intelligence; this is
only the contract peers exchange.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


@runtime_checkable
class EvidenceBearing(Protocol):
    """Anything that can justify itself with evidence references."""

    @property
    def evidence_ids(self) -> list[str]:
        ...


class ProvenanceStamp(BaseModel):
    """Where a statement came from and how much weight it carries."""
    source_system: str | None = None
    source_id: str | None = None
    observed_at: datetime | None = None
    reliability: float | None = None
    relevance: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
