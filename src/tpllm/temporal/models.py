"""Temporal truth models.

Before predicting alternate futures, the system must reconstruct exactly
what was known at an arbitrary point in time — never accidentally using
information that became known later.

The central distinction is bitemporal:

    REALITY TIME   (valid_from / valid_until)  — when it was true
    KNOWLEDGE TIME (known_from / known_until)  — when we learned it

Integration failed at 09:00. The team discovered it at 10:15. Evidence
was ingested at 10:18. A forecast generated at 09:30 must not know about
the failure, even though the failure had already occurred.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class TemporalFactWindow(BaseModel):
    """Separates when a fact was true from when it was known."""
    valid_from: datetime
    valid_until: datetime | None = None
    known_from: datetime
    known_until: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> "TemporalFactWindow":
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("valid_until precedes valid_from.")
        if self.known_until is not None and self.known_until < self.known_from:
            raise ValueError("known_until precedes known_from.")
        return self

    def was_true_at(self, moment: datetime) -> bool:
        """Reality time: was this fact true then, regardless of whether
        anyone knew it?"""
        if moment < self.valid_from:
            return False
        return self.valid_until is None or moment < self.valid_until

    def was_known_at(self, moment: datetime) -> bool:
        """Knowledge time: had we learned it by then?"""
        if moment < self.known_from:
            return False
        return self.known_until is None or moment < self.known_until


class TemporalEventType(str, Enum):
    EVIDENCE_OBSERVED = "evidence_observed"
    CLAIM_SUPPORTED = "claim_supported"
    CLAIM_CONTESTED = "claim_contested"
    CLAIM_STALE = "claim_stale"
    POSITION_RECORDED = "position_recorded"
    RECOMMENDATION_CREATED = "recommendation_created"
    RECOMMENDATION_EXPIRED = "recommendation_expired"
    DECISION_PROPOSED = "decision_proposed"
    DECISION_CONCLUSIVE = "decision_conclusive"
    DECISION_REOPENED = "decision_reopened"
    DECISION_SUPERSEDED = "decision_superseded"
    KPI_OBSERVED = "kpi_observed"
    BLOCKER_OPENED = "blocker_opened"
    BLOCKER_RESOLVED = "blocker_resolved"
    ACTION_REQUESTED = "action_requested"
    ACTION_RESOLVED = "action_resolved"
    OUTCOME_OBSERVED = "outcome_observed"


class TemporalEvent(BaseModel):
    """One observable change, stamped in both time dimensions.

    ``occurred_at`` is reality time; ``known_at`` is knowledge time. They
    are never assumed equal.
    """
    id: str
    project_id: str
    event_type: TemporalEventType
    entity_ids: list[str] = Field(default_factory=list)
    occurred_at: datetime
    known_at: datetime
    source_object_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    summary: str

    @model_validator(mode="after")
    def _check(self) -> "TemporalEvent":
        if self.known_at < self.occurred_at:
            raise ValueError(
                "known_at precedes occurred_at — a fact cannot be known "
                "before it happens."
            )
        return self

    @property
    def knowledge_lag_seconds(self) -> float:
        return (self.known_at - self.occurred_at).total_seconds()


class ProjectTemporalState(BaseModel):
    """The reconstructed state of a project at a specific ``as_of``.

    This is the root state of every scenario tree; its
    ``evidence_cutoff_at`` is what guarantees a forecast cannot leak
    future information.
    """
    project_id: str
    as_of: datetime
    understanding_snapshot_id: str | None = None
    workstream_state_ids: list[str] = Field(default_factory=list)
    active_claim_ids: list[str] = Field(default_factory=list)
    contested_claim_ids: list[str] = Field(default_factory=list)
    stale_claim_ids: list[str] = Field(default_factory=list)
    active_decision_ids: list[str] = Field(default_factory=list)
    current_recommendation_ids: list[str] = Field(default_factory=list)
    open_action_request_ids: list[str] = Field(default_factory=list)
    current_kpi_state_ids: list[str] = Field(default_factory=list)
    active_blocker_ids: list[str] = Field(default_factory=list)
    active_commitment_ids: list[str] = Field(default_factory=list)
    current_authority_relation_ids: list[str] = Field(default_factory=list)
    unresolved_unknown_ids: list[str] = Field(default_factory=list)
    evidence_cutoff_at: datetime
    state_fingerprint: str


def build_state_fingerprint(state_parts: dict[str, list[str] | str]) -> str:
    """Deterministic fingerprint over the reconstructed state, so an
    identical historical reconstruction is provably identical."""
    pieces: list[str] = []
    for key in sorted(state_parts):
        value = state_parts[key]
        if isinstance(value, list):
            pieces.append(f"{key}={','.join(sorted(value))}")
        else:
            pieces.append(f"{key}={value}")
    payload = "|".join(pieces)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


# ── Decision lineage ─────────────────────────────────────────────────

class DecisionLineageNode(BaseModel):
    decision_id: str
    parent_decision_id: str | None = None
    status: str
    recommendation_id: str | None = None
    decision_origin: str = "unknown"
    selected_option_id: str | None = None
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    superseded_by: str | None = None
    reopened_at: datetime | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class DecisionLineage(BaseModel):
    root_decision_id: str
    nodes: list[DecisionLineageNode] = Field(default_factory=list)
    current_authoritative_decision_id: str | None = None
    explanation: str = ""


class SupersessionCycleError(ValueError):
    """Raised when supersession edges form a cycle.

    A decision reversal must be expressed through explicit reopen
    semantics, never by pointing two supersession edges at each other.
    """
