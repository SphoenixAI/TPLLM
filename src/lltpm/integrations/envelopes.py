"""Evidence envelopes — the normalized form every tool event takes
(Commit 18).

All tools (Slack, meetings, tickets, GitHub, metrics) normalize into
EvidenceEnvelope. Envelopes carry provenance and pointers back to the
source system; they never carry OAuth tokens, secrets, or signing keys
— that is enforced at the model level, not by convention.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, model_validator

from lltpm.coordination.models import OrganizationalActorRef
from lltpm.intelligence.project_understanding import (
    EvidenceReference,
    EvidenceSourceType,
    ProjectEvidence,
)

_SECRET_KEY_MARKERS = (
    "token", "secret", "password", "api_key", "apikey", "authorization",
    "signing_key", "credential",
)


def is_secret_metadata_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Drop secret-looking keys before an envelope is built. Secrets live
    in the existing secrets infrastructure, never in evidence."""
    return {k: v for k, v in metadata.items() if not is_secret_metadata_key(k)}


def build_idempotency_key(
    *,
    source_system: str,
    source_id: str,
    event_type: str,
    observed_at: datetime,
) -> str:
    """Deterministic: the same webhook delivered twice yields the same
    key, so duplicates never duplicate evidence."""
    payload = "|".join(
        [source_system, source_id, event_type, observed_at.isoformat()]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class EvidenceEnvelope(BaseModel):
    id: str
    source_system: str
    source_type: str
    source_id: str
    event_type: str
    observed_at: datetime
    received_at: datetime
    project_ids: list[str] = Field(default_factory=list)
    actor_refs: list[OrganizationalActorRef] = Field(default_factory=list)
    title: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_pointer: str | None = None
    idempotency_key: str
    retention_class: str

    @model_validator(mode="after")
    def _check(self) -> "EvidenceEnvelope":
        if not self.idempotency_key:
            raise ValueError("An envelope requires an idempotency key.")
        for key in self.metadata:
            if is_secret_metadata_key(key):
                raise ValueError(
                    f"Metadata key '{key}' looks like a secret — tokens and "
                    f"keys must never be persisted in evidence models."
                )
        return self


def make_envelope(
    *,
    source_system: str,
    source_type: str,
    source_id: str,
    event_type: str,
    observed_at: datetime,
    project_ids: list[str],
    content: str | None = None,
    title: str | None = None,
    actor_refs: list[OrganizationalActorRef] | None = None,
    metadata: dict[str, Any] | None = None,
    source_pointer: str | None = None,
    retention_class: str = "excerpt",
    received_at: datetime | None = None,
) -> EvidenceEnvelope:
    """Build a sanitized envelope with a deterministic idempotency key."""
    key = build_idempotency_key(
        source_system=source_system,
        source_id=source_id,
        event_type=event_type,
        observed_at=observed_at,
    )
    return EvidenceEnvelope(
        id=f"env-{key[:16]}",
        source_system=source_system,
        source_type=source_type,
        source_id=source_id,
        event_type=event_type,
        observed_at=observed_at,
        received_at=received_at or datetime.now(timezone.utc),
        project_ids=list(project_ids),
        actor_refs=list(actor_refs or []),
        title=title,
        content=content,
        metadata=sanitize_metadata(metadata or {}),
        source_pointer=source_pointer,
        idempotency_key=key,
        retention_class=retention_class,
    )


_SOURCE_TYPE_MAP = {
    "slack": EvidenceSourceType.CHAT,
    "teams": EvidenceSourceType.CHAT,
    "gchat": EvidenceSourceType.CHAT,
    "meeting": EvidenceSourceType.MEETING,
    "zoom": EvidenceSourceType.MEETING,
    "meet": EvidenceSourceType.MEETING,
    "jira": EvidenceSourceType.TICKET,
    "linear": EvidenceSourceType.TICKET,
    "github": EvidenceSourceType.REPOSITORY,
    "notion": EvidenceSourceType.DOCUMENT,
    "gdrive": EvidenceSourceType.DOCUMENT,
    "email": EvidenceSourceType.MESSAGE,
}


def envelope_to_project_evidence(
    envelope: EvidenceEnvelope,
    *,
    project_id: str,
) -> ProjectEvidence | None:
    """Convert an envelope into Commit 11 ProjectEvidence. Envelopes
    without content contribute pointers only, not evidence text."""
    if not envelope.content:
        return None
    try:
        source_type = EvidenceSourceType(envelope.source_type)
    except ValueError:
        source_type = _SOURCE_TYPE_MAP.get(
            envelope.source_system.lower(), EvidenceSourceType.DOCUMENT
        )
    reference = EvidenceReference(
        id=f"ref-{envelope.id}",
        project_id=project_id,
        source_type=source_type,
        source_id=envelope.source_id,
        title=envelope.title,
        excerpt=envelope.content[:300],
        observed_at=envelope.observed_at,
        ingested_at=envelope.received_at,
        reliability=0.7,
        relevance=0.6,
        metadata=dict(envelope.metadata),
    )
    return ProjectEvidence(
        id=f"pev-{envelope.id}",
        project_id=project_id,
        content=envelope.content,
        source=reference,
        normalized_at=envelope.received_at,
    )
