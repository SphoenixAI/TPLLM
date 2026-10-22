"""Data minimization and retention.

TPLLM preferentially persists normalized claims, ontology relations,
evidence references, source pointers, timestamps, and confidence — not
full copies of every source system. The source artifact stays in its
system of record; we keep a pointer and a content hash.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from tpllm.integrations.envelopes import EvidenceEnvelope


class RetentionClass(str, Enum):
    FULL = "full"
    EXCERPT = "excerpt"
    POINTER_ONLY = "pointer_only"


class ArtifactPointer(BaseModel):
    source_system: str
    source_id: str
    source_pointer: str | None
    title: str | None
    observed_at: datetime
    content_hash: str | None


_EXCERPT_LENGTH = 500


def content_hash(content: str | None) -> str | None:
    if content is None:
        return None
    return hashlib.sha256(content.encode()).hexdigest()


def build_artifact_pointer(envelope: EvidenceEnvelope) -> ArtifactPointer:
    return ArtifactPointer(
        source_system=envelope.source_system,
        source_id=envelope.source_id,
        source_pointer=envelope.source_pointer,
        title=envelope.title,
        observed_at=envelope.observed_at,
        content_hash=content_hash(envelope.content),
    )


def minimize_envelope(
    *,
    envelope: EvidenceEnvelope,
    retention_class: RetentionClass,
) -> tuple[EvidenceEnvelope, ArtifactPointer]:
    """Reduce stored content per retention class. The pointer (with
    content hash) always survives, so the original remains verifiable in
    its source system without being copied here."""
    pointer = build_artifact_pointer(envelope)
    if retention_class == RetentionClass.FULL:
        minimized = envelope.model_copy(
            update={"retention_class": retention_class.value}
        )
    elif retention_class == RetentionClass.EXCERPT:
        minimized = envelope.model_copy(
            update={
                "content": (
                    envelope.content[:_EXCERPT_LENGTH]
                    if envelope.content
                    else None
                ),
                "retention_class": retention_class.value,
            }
        )
    else:
        minimized = envelope.model_copy(
            update={"content": None, "retention_class": retention_class.value}
        )
    return minimized, pointer
