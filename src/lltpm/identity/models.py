"""Canonical identity models (Commit 19).

The canonical identity layer lets TPLLM understand when the same real
organizational entity appears under different names, IDs, handles, and
aliases across tools — without ever inventing who somebody is.

Safety principles baked into these models:
- Resolution is evidence-backed, confidence-scored, and reversible.
- Human identities receive the strictest merge policy: a false merge of
  two people is dangerous; keeping two identities temporarily separate
  is merely inconvenient. Default conservative.
- Identity answers WHO; the relationship ontology answers IN WHAT
  CAPACITY. This layer never collapses ownership/expertise/authority.
- This is not a people database: only the identity information needed to
  connect evidence, decisions, roles, requests, ownership, expertise,
  workstreams, and KPI stewardship is persisted. No secrets, ever.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ── Enums ──────────────────────────────────────────────────────────────

class CanonicalEntityType(str, Enum):
    PERSON = "person"
    TEAM = "team"
    PROJECT = "project"
    WORKSTREAM = "workstream"
    SERVICE = "service"
    REPOSITORY = "repository"
    KPI = "kpi"
    MILESTONE = "milestone"
    FEATURE = "feature"
    SYSTEM = "system"
    DOCUMENT = "document"
    DECISION_TOPIC = "decision_topic"
    ORGANIZATION = "organization"
    OTHER = "other"


class ExternalIdentityType(str, Enum):
    USER_ID = "user_id"
    EMAIL = "email"
    HANDLE = "handle"
    DISPLAY_NAME = "display_name"
    REPOSITORY_NAME = "repository_name"
    SERVICE_NAME = "service_name"
    PROJECT_KEY = "project_key"
    ISSUE_COMPONENT = "issue_component"
    KPI_NAME = "kpi_name"
    URL = "url"
    DOCUMENT_ID = "document_id"
    SYSTEM_ID = "system_id"
    OTHER = "other"


class EntityResolutionStatus(str, Enum):
    UNRESOLVED = "unresolved"
    CANDIDATE = "candidate"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    CONTESTED = "contested"
    REJECTED = "rejected"


class EntityMergeStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    APPLIED = "applied"
    REJECTED = "rejected"
    REVERSED = "reversed"


class IdentityEvidenceType(str, Enum):
    EXACT_EXTERNAL_ID = "exact_external_id"
    EXACT_EMAIL = "exact_email"
    VERIFIED_EMAIL_ALIAS = "verified_email_alias"
    EXACT_HANDLE = "exact_handle"
    DISPLAY_NAME_MATCH = "display_name_match"
    TEAM_MATCH = "team_match"
    ROLE_MATCH = "role_match"
    SHARED_SOURCE_REFERENCE = "shared_source_reference"
    REPOSITORY_METADATA = "repository_metadata"
    SERVICE_METADATA = "service_metadata"
    PROJECT_METADATA = "project_metadata"
    EXPLICIT_ALIAS = "explicit_alias"
    HUMAN_CONFIRMATION = "human_confirmation"
    NEGATIVE_EVIDENCE = "negative_evidence"


class IdentityGraphEdgeType(str, Enum):
    IDENTIFIES = "identifies"
    ALIAS_OF = "alias_of"
    MEMBER_OF = "member_of"
    OWNS = "owns"
    REPRESENTS = "represents"


class SourceAvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    DELETED = "deleted"
    INACCESSIBLE = "inaccessible"
    UNKNOWN = "unknown"


# ── Source availability ──────────────────────────────────────────────

class SourceReferenceState(BaseModel):
    """Identity stays historically explainable even when the original
    source content disappears — the mapping survives with an explanation
    that its evidence is no longer retrievable."""
    source_system: str
    source_id: str
    availability: SourceAvailabilityStatus
    last_verified_at: datetime | None = None
    explanation: str | None = None


# ── Core models ──────────────────────────────────────────────────────

class CanonicalEntity(BaseModel):
    id: str
    entity_type: CanonicalEntityType
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    external_identity_ids: list[str] = Field(default_factory=list)
    active: bool = True
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalEntityIdentity(BaseModel):
    id: str
    entity_type: CanonicalEntityType
    source_system: str
    identity_type: ExternalIdentityType
    external_id: str
    display_value: str | None = None
    email: str | None = None
    handle: str | None = None
    team_hint: str | None = None
    role_hint: str | None = None
    source_pointer: str | None = None
    observed_at: datetime
    linked_at: datetime | None = None
    unlinked_at: datetime | None = None
    source_state: SourceReferenceState | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EntityResolutionEvidence(BaseModel):
    id: str
    external_identity_id: str
    candidate_entity_id: str
    evidence_type: IdentityEvidenceType
    strength: float
    positive: bool = True
    explanation: str
    source_ids: list[str] = Field(default_factory=list)
    observed_at: datetime

    @model_validator(mode="after")
    def _check(self) -> "EntityResolutionEvidence":
        if not (0.0 <= self.strength <= 1.0):
            raise ValueError("Evidence strength must be within [0, 1].")
        if not self.explanation.strip():
            raise ValueError("Identity evidence must explain itself.")
        return self


class EntityResolutionCandidate(BaseModel):
    external_identity_id: str
    candidate_entity_id: str
    score: float
    status: EntityResolutionStatus
    positive_evidence_ids: list[str] = Field(default_factory=list)
    negative_evidence_ids: list[str] = Field(default_factory=list)
    explanation: str

    @model_validator(mode="after")
    def _check(self) -> "EntityResolutionCandidate":
        if not (0.0 <= self.score <= 1.0):
            raise ValueError("Candidate score must be within [0, 1].")
        return self


class EntityResolutionResult(BaseModel):
    external_identity_id: str
    status: EntityResolutionStatus
    canonical_entity_id: str | None = None
    candidates: list[EntityResolutionCandidate] = Field(default_factory=list)
    confidence: float
    explanation: str


class EntityResolutionPolicy(BaseModel):
    """PERSON is stricter than every other entity type — by policy, not
    by accident."""
    automatic_resolution_threshold: float = 0.90
    ambiguous_margin: float = 0.10
    person_automatic_resolution_threshold: float = 0.98
    require_strong_person_identifier: bool = True
    allow_display_name_only_resolution: bool = False
    maximum_candidates: int = 5


class ExplicitEntityAlias(BaseModel):
    id: str
    canonical_entity_id: str
    source_system: str | None = None
    alias: str
    created_by: str
    created_at: datetime
    evidence_ids: list[str] = Field(default_factory=list)


class IdentityGraphEdge(BaseModel):
    id: str
    source_id: str
    target_id: str
    edge_type: IdentityGraphEdgeType
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float


class EntityMergeProposal(BaseModel):
    id: str
    source_entity_id: str
    target_entity_id: str
    status: EntityMergeStatus
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float
    created_at: datetime


class EntityMergeRecord(BaseModel):
    """Reversibility by construction: the record carries full pre-merge
    snapshots of both entities."""
    id: str
    source_entity_id: str
    target_entity_id: str
    status: EntityMergeStatus
    moved_external_identity_ids: list[str] = Field(default_factory=list)
    previous_source_snapshot: CanonicalEntity
    previous_target_snapshot: CanonicalEntity
    evidence_ids: list[str] = Field(default_factory=list)
    applied_at: datetime | None = None
    reversed_at: datetime | None = None


class EntityIdentityConflict(BaseModel):
    id: str
    entity_id: str
    external_identity_ids: list[str] = Field(default_factory=list)
    conflict_type: str
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)
    resolved: bool = False


# ── Normalization helpers (deterministic, non-destructive) ───────────

_WHITESPACE = re.compile(r"\s+")
_SEPARATORS = re.compile(r"[-_./]+")
_NAME_PUNCTUATION = re.compile(r"[^\w\s-]")


def normalize_display_name(value: str) -> str:
    lowered = value.strip().lower()
    lowered = _NAME_PUNCTUATION.sub(" ", lowered)
    lowered = _SEPARATORS.sub(" ", lowered)
    return _WHITESPACE.sub(" ", lowered).strip()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_handle(value: str) -> str:
    return value.strip().lstrip("@").lower()


def normalize_entity_alias(value: str) -> str:
    lowered = value.strip().lower()
    lowered = _SEPARATORS.sub(" ", lowered)
    return _WHITESPACE.sub(" ", lowered).strip()


# ── Deterministic IDs ────────────────────────────────────────────────

def build_external_identity_id(
    *,
    source_system: str,
    identity_type: ExternalIdentityType,
    external_id: str,
) -> str:
    """Same source identity → same external identity ID. Supports
    webhook idempotency across repeated deliveries."""
    payload = "|".join(
        [source_system.strip().lower(), identity_type.value,
         external_id.strip().lower()]
    )
    return f"xid-{hashlib.sha256(payload.encode()).hexdigest()[:20]}"


# ── Organizational actor bridge model ────────────────────────────────

class CanonicalOrganizationalActorRef(BaseModel):
    """Bridge wrapper: an existing Commit 14 actor reference plus its
    canonical resolution — the original actor is preserved untouched."""
    actor: Any
    canonical_entity_id: str | None = None
    resolution_status: EntityResolutionStatus = (
        EntityResolutionStatus.UNRESOLVED
    )
