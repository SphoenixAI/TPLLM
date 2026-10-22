"""Canonical identity resolution on the ingest path.

The runtime's target flow has an explicit step between temporal
ordering and the ontology update: canonical identity resolution. It was
the one step with no implementation on the live path, and its absence
matters more than it sounds.

The portfolio premise is that four projects waiting on the same
approval authority should see one shared bottleneck rather than four
unrelated blockers. That only works if "Platform Architecture" in one
project and "platform-arch" in another resolve to the same canonical
node. Without resolution the portfolio layer is structurally unable to
notice the thing it exists to notice.

Two commitments shape this module.

**Resolution decisions are durable.** Persistence lists canonical identity
decisions among the things that must survive a crash, so a resolved
link is journalled as IDENTITY_LINKED before anything derives from it.
Replay then reconstructs the same identity graph without re-running
resolution against source systems that may be gone.

**Ambiguity is preserved, not guessed.** The resolver returns AMBIGUOUS and
CANDIDATE for a reason: resolving a person by display name alone is how
two colleagues with the same surname become one. Only RESOLVED links are
written, and the rest are reported so a human can settle them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from tpllm.identity.models import (
    CanonicalEntity,
    EntityResolutionPolicy,
    EntityResolutionStatus,
    ExternalEntityIdentity,
)
from tpllm.identity.registry import (
    CanonicalEntityRegistry,
    extract_external_identities_from_envelope,
    link_external_identity,
    register_external_identity,
)
from tpllm.identity.resolution import resolve_external_identity
from tpllm.persistence.models import JournalEventType, organization_stream
from tpllm.persistence.journal import build_journal_record


class IdentityResolutionOutcome(BaseModel):
    """What one ingest pass did about identity.

    ``unresolved`` and ``ambiguous`` are reported rather than silently
    dropped: an unlinked identity means the portfolio layer may still be
    seeing two names for one thing, and that is worth knowing.
    """
    linked: dict[str, str] = Field(default_factory=dict)
    already_linked: list[str] = Field(default_factory=list)
    ambiguous: dict[str, list[str]] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    journal_record_ids: list[str] = Field(default_factory=list)
    explanations: list[str] = Field(default_factory=list)

    @property
    def canonical_entity_ids(self) -> list[str]:
        return sorted(set(self.linked.values()))

    @property
    def needs_human_attention(self) -> bool:
        """Ambiguity is a question for a person, not a coin flip."""
        return bool(self.ambiguous)


def resolve_envelope_identities(
    *,
    envelopes: list[Any],
    registry: CanonicalEntityRegistry,
    organization_id: str,
    journal: Any = None,
    policy: EntityResolutionPolicy | None = None,
    now: datetime | None = None,
) -> tuple[CanonicalEntityRegistry, IdentityResolutionOutcome]:
    """Resolve the identities an ingest batch mentions, durably.

    Returns the updated registry and what happened. The registry is
    replaced rather than mutated, so a failure part-way leaves the
    caller's copy intact.
    """
    policy = policy or EntityResolutionPolicy()
    outcome = IdentityResolutionOutcome()

    identities: list[ExternalEntityIdentity] = []
    seen: set[str] = set()
    for envelope in envelopes:
        for identity in extract_external_identities_from_envelope(envelope):
            if identity.id in seen:
                continue
            seen.add(identity.id)
            identities.append(identity)

    for identity in sorted(identities, key=lambda i: i.id):
        existing = registry.external_identities.get(identity.id)
        if existing is not None and existing.linked_at is not None:
            outcome.already_linked.append(identity.id)
            continue

        if existing is None:
            registry = register_external_identity(
                registry=registry, identity=identity,
            )

        result = resolve_external_identity(
            identity=identity,
            canonical_entities=list(registry.entities.values()),
            existing_identities=list(registry.external_identities.values()),
            policy=policy,
        )
        outcome.explanations.append(result.explanation)

        if result.status == EntityResolutionStatus.RESOLVED and (
            result.canonical_entity_id
        ):
            registry = link_external_identity(
                registry=registry,
                external_identity_id=identity.id,
                canonical_entity_id=result.canonical_entity_id,
                linked_at=now,
            )
            outcome.linked[identity.id] = result.canonical_entity_id
            if journal is not None:
                record = _journal_link(
                    journal=journal,
                    organization_id=organization_id,
                    identity=identity,
                    canonical_entity_id=result.canonical_entity_id,
                    confidence=result.confidence,
                    occurred_at=identity.observed_at,
                    recorded_at=now or identity.observed_at,
                )
                outcome.journal_record_ids.append(record.id)
        elif result.status == EntityResolutionStatus.AMBIGUOUS:
            # Two plausible matches. Guessing here is how two people
            # with the same surname become one person.
            outcome.ambiguous[identity.id] = [
                c.candidate_entity_id for c in result.candidates
            ]
        else:
            outcome.unresolved.append(identity.id)

    return registry, outcome


def _journal_link(
    *,
    journal: Any,
    organization_id: str,
    identity: ExternalEntityIdentity,
    canonical_entity_id: str,
    confidence: float,
    occurred_at: datetime,
    recorded_at: datetime,
):
    """Make one resolution decision durable.

    Journalled before anything derives from it, so a replay rebuilds the
    same identity graph without re-resolving against source systems that
    may no longer answer.
    """
    return journal.append(
        record=build_journal_record(
            event_type=JournalEventType.IDENTITY_LINKED,
            stream_id=organization_stream(organization_id),
            occurred_at=occurred_at,
            recorded_at=max(recorded_at, occurred_at),
            payload={
                "external_identity_id": identity.id,
                "canonical_entity_id": canonical_entity_id,
                "source_system": identity.source_system,
                "identity_type": identity.identity_type.value,
                "entity_type": identity.entity_type.value,
                "confidence": round(confidence, 6),
            },
            organization_id=organization_id,
            source_key=f"{identity.id}->{canonical_entity_id}",
        )
    )


def registry_from_journal(
    *,
    journal: Any,
    organization_id: str | None = None,
    entities: list[CanonicalEntity] | None = None,
) -> CanonicalEntityRegistry:
    """Rebuild the identity graph from durable history.

    Replays IDENTITY_LINKED and IDENTITY_UNLINKED in order. Resolution
    is not re-run: the decision that was made is the decision that
    stands, which is also what keeps a replay from reaching for a source
    system that has since gone away.
    """
    registry = CanonicalEntityRegistry(
        entities={e.id: e for e in (entities or [])},
    )
    for record in journal.read_from(global_sequence=1):
        if organization_id is not None and record.organization_id not in (
            None, organization_id,
        ):
            continue
        if record.event_type == JournalEventType.IDENTITY_LINKED:
            registry = _apply_link(registry, record)
        elif record.event_type == JournalEventType.IDENTITY_UNLINKED:
            registry = _apply_unlink(registry, record)
    return registry


def _apply_link(
    registry: CanonicalEntityRegistry, record: Any,
) -> CanonicalEntityRegistry:
    payload = record.payload
    identity_id = str(payload.get("external_identity_id"))
    canonical_id = str(payload.get("canonical_entity_id"))
    if identity_id not in registry.external_identities:
        # The identity itself was not journalled separately; reconstruct
        # enough of it to carry the link, which is the durable part.
        registry = register_external_identity(
            registry=registry,
            identity=ExternalEntityIdentity(
                id=identity_id,
                source_system=str(payload.get("source_system", "unknown")),
                external_id=identity_id,
                identity_type=payload.get("identity_type", "handle"),
                entity_type=payload.get("entity_type", "system"),
                observed_at=record.occurred_at,
            ),
        )
    if canonical_id not in registry.entities:
        return registry
    return link_external_identity(
        registry=registry,
        external_identity_id=identity_id,
        canonical_entity_id=canonical_id,
        linked_at=record.occurred_at,
    )


def _apply_unlink(
    registry: CanonicalEntityRegistry, record: Any,
) -> CanonicalEntityRegistry:
    from tpllm.identity.registry import unlink_external_identity

    identity_id = str(record.payload.get("external_identity_id"))
    if identity_id not in registry.external_identities:
        return registry
    return unlink_external_identity(
        registry=registry,
        external_identity_id=identity_id,
        unlinked_at=record.occurred_at,
    )


def canonical_ids_for_envelope(
    *,
    envelope: Any,
    registry: CanonicalEntityRegistry,
) -> list[str]:
    """Which canonical entities this envelope mentions, by exact name.

    What makes the shared bottleneck visible: two envelopes naming the
    same authority differently come back with the same canonical id.

    Deliberately an exact-string lookup over linked identities, curated
    aliases, and canonical names — not a resolution decision. The
    registry resolves probabilistically and scores a name resemblance low on
    purpose; this helper answers "is this string a known name for a known
    entity", creates no durable link, and never merges anything.
    """
    found: list[str] = []
    for identity in extract_external_identities_from_envelope(envelope):
        stored = registry.external_identities.get(identity.id)
        if stored is None or stored.linked_at is None:
            continue
        for canonical_id, entity in registry.entities.items():
            if identity.id in getattr(entity, "external_identity_ids", []):
                if canonical_id not in found:
                    found.append(canonical_id)
    if found:
        return sorted(found)

    # Fall back to declared aliases and canonical names, which is how
    # "platform-arch" reaches the Platform Architecture entity before
    # any identity has been linked.
    by_name: dict[str, str] = {}
    for alias in registry.aliases.values():
        by_name[alias.alias.strip().lower()] = alias.canonical_entity_id
    for entity_id, entity in registry.entities.items():
        by_name[entity.canonical_name.strip().lower()] = entity_id
        for alias_text in entity.aliases:
            by_name[alias_text.strip().lower()] = entity_id

    for identity in extract_external_identities_from_envelope(envelope):
        for value in (identity.display_value, identity.handle, identity.email):
            if not value:
                continue
            canonical_id = by_name.get(value.strip().lower())
            if canonical_id and canonical_id not in found:
                found.append(canonical_id)
    return sorted(found)
