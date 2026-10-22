"""Evidence envelopes — the normalized form every tool event takes
.

All tools (Slack, meetings, tickets, GitHub, metrics) normalize into
EvidenceEnvelope. Envelopes carry provenance and pointers back to the
source system; they never carry OAuth tokens, secrets, or signing keys
— that is enforced at the model level, not by convention.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, model_validator

from tpllm.coordination.models import OrganizationalActorRef
from tpllm.intelligence.project_understanding import (
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


#: Credential shapes that turn up inside human prose: a token pasted
#: into a pull request body, a curl command in a ticket, a key in a
#: chat message. The key-level rule above cannot catch these, because
#: the field is called ``body`` and the secret is a substring of it.
_SECRET_TEXT_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Atlassian API tokens. Added alongside Jira, which made a
    # token pasted into a ticket description a realistic way for a
    # credential to reach append-only history, and this pattern was
    # the one the GitHub and Slack work had no reason to include.
    re.compile(r"\bATATT[A-Za-z0-9_\-=]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)\b(?:bearer|authorization:)\s+[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?"
        r"-----END [A-Z ]*PRIVATE KEY-----"
    ),
)

SECRET_TEXT_PLACEHOLDER = "[redacted-credential]"


def _envelope_id_digest(idempotency_key: str) -> str:
    """A stable, collision-free id derived from the whole key.

    Derived rather than random so that re-normalizing the same payload
    produces the same envelope id, which is what lets a caller address a
    piece of evidence across a replay.
    """
    return hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]


def redact_secret_text(text: str | None) -> tuple[str | None, bool]:
    """Remove credential material from free text. Returns (text, found).

    Evidence content is prose a person wrote in someone else's system,
    so it can contain anything — including a token pasted into a pull
    request description. The journal is append-only, which makes a
    credential that reaches it permanent, so the substring is replaced
    here rather than at the storage boundary where refusing the write
    would also lose the legitimate evidence around it.

    Only the credential is removed; the surrounding sentence survives,
    because the sentence is the evidence.
    """
    if not text:
        return text, False
    redacted = text
    for pattern in _SECRET_TEXT_PATTERNS:
        redacted = pattern.sub(SECRET_TEXT_PLACEHOLDER, redacted)
    return redacted, redacted != text


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
    idempotency_key: str | None = None,
) -> EvidenceEnvelope:
    """Build a sanitized envelope with a deterministic idempotency key.

    ``idempotency_key`` overrides the derived one, and a normalizer that
    has the provider's own event identity should always pass it. The
    derived key folds in ``observed_at``, which is right for a polled
    resource whose timestamp is its version and wrong for a webhook: the
    same delivery can arrive carrying a different timestamp, and a key
    that moved with it would turn one event into two facts. Keying on
    the provider's event id is the published guidance, and this is the
    parameter that lets a caller follow it.
    """
    key = idempotency_key or build_idempotency_key(
        source_system=source_system,
        source_id=source_id,
        event_type=event_type,
        observed_at=observed_at,
    )
    safe_content, content_had_secret = redact_secret_text(content)
    safe_title, title_had_secret = redact_secret_text(title)
    safe_metadata = sanitize_metadata(metadata or {})
    if content_had_secret or title_had_secret:
        # Said out loud: an operator reading this evidence should know
        # the excerpt is not verbatim, and why. The flag is named to
        # avoid the secret-key markers above, which — correctly — would
        # otherwise refuse a key containing the word "credential".
        safe_metadata["redaction_applied"] = True
    return EvidenceEnvelope(
        # Hashed, not sliced. This was `key[:16]`, which was fine while
        # every key was a sha256 digest — and became a collision the
        # moment a normalizer was allowed to supply a readable key, since
        # the first sixteen characters of
        # `github:pull_request:NODE-…` are the same for every pull
        # request. Eight envelopes shared two ids. The id reaches the
        # journal as `DurableEvidenceRecord.evidence_id` and the runtime
        # as `root_evidence_ids`, so evidence provenance was naming a
        # group of unrelated facts.
        id=f"env-{_envelope_id_digest(key)}",
        source_system=source_system,
        source_type=source_type,
        source_id=source_id,
        event_type=event_type,
        observed_at=observed_at,
        received_at=received_at or datetime.now(timezone.utc),
        project_ids=list(project_ids),
        actor_refs=list(actor_refs or []),
        title=safe_title,
        content=safe_content,
        metadata=safe_metadata,
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
    """Convert an envelope into ``ProjectEvidence``. Envelopes
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
