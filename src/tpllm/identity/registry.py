"""Canonical entity registry and bridges.

Copy-on-write registry of canonical entities, external identities, and
explicit aliases, plus the bridges that let existing layers use
canonical identity without being rewritten:

- envelope → external identities (duck-typed; integrations stay above
  identity in the dependency graph)
- OrganizationalActorRef → canonical entity (the original models untouched)
- DecisionCapsule actors → canonical entities (the capsule untouched;
  factual content never changes)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from tpllm.identity.models import (
    CanonicalEntity,
    CanonicalEntityType,
    CanonicalOrganizationalActorRef,
    EntityIdentityConflict,
    EntityResolutionPolicy,
    EntityResolutionStatus,
    ExplicitEntityAlias,
    ExternalEntityIdentity,
    ExternalIdentityType,
    SourceAvailabilityStatus,
    SourceReferenceState,
    build_external_identity_id,
    normalize_entity_alias,
)
from tpllm.identity.resolution import resolve_external_identity


class CanonicalEntityRegistry(BaseModel):
    entities: dict[str, CanonicalEntity] = Field(default_factory=dict)
    external_identities: dict[str, ExternalEntityIdentity] = Field(
        default_factory=dict
    )
    aliases: dict[str, ExplicitEntityAlias] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


# ── Copy-on-write registry operations ────────────────────────────────

def register_canonical_entity(
    *,
    registry: CanonicalEntityRegistry,
    entity: CanonicalEntity,
) -> CanonicalEntityRegistry:
    entities = dict(registry.entities)
    entities[entity.id] = entity
    return registry.model_copy(update={"entities": entities})


def register_external_identity(
    *,
    registry: CanonicalEntityRegistry,
    identity: ExternalEntityIdentity,
) -> CanonicalEntityRegistry:
    identities = dict(registry.external_identities)
    identities[identity.id] = identity
    return registry.model_copy(update={"external_identities": identities})


def register_explicit_alias(
    *,
    registry: CanonicalEntityRegistry,
    alias: ExplicitEntityAlias,
) -> CanonicalEntityRegistry:
    if alias.canonical_entity_id not in registry.entities:
        raise ValueError(
            f"Alias targets unknown entity '{alias.canonical_entity_id}'."
        )
    aliases = dict(registry.aliases)
    aliases[alias.id] = alias
    entities = dict(registry.entities)
    entity = entities[alias.canonical_entity_id]
    if alias.alias not in entity.aliases:
        entities[alias.canonical_entity_id] = entity.model_copy(
            update={"aliases": [*entity.aliases, alias.alias]}
        )
    return registry.model_copy(
        update={"aliases": aliases, "entities": entities}
    )


def link_external_identity(
    *,
    registry: CanonicalEntityRegistry,
    external_identity_id: str,
    canonical_entity_id: str,
    linked_at: datetime | None = None,
) -> CanonicalEntityRegistry:
    if external_identity_id not in registry.external_identities:
        raise ValueError(f"Unknown external identity '{external_identity_id}'.")
    if canonical_entity_id not in registry.entities:
        raise ValueError(f"Unknown canonical entity '{canonical_entity_id}'.")

    entity = registry.entities[canonical_entity_id]
    identity = registry.external_identities[external_identity_id]
    if external_identity_id in entity.external_identity_ids:
        return registry  # idempotent

    entities = dict(registry.entities)
    entities[canonical_entity_id] = entity.model_copy(
        update={
            "external_identity_ids": [
                *entity.external_identity_ids, external_identity_id,
            ]
        }
    )
    identities = dict(registry.external_identities)
    identities[external_identity_id] = identity.model_copy(
        update={
            "linked_at": linked_at or datetime.now(timezone.utc),
            "unlinked_at": None,
        }
    )
    return registry.model_copy(
        update={"entities": entities, "external_identities": identities}
    )


def unlink_external_identity(
    *,
    registry: CanonicalEntityRegistry,
    external_identity_id: str,
    canonical_entity_id: str,
    unlinked_at: datetime | None = None,
) -> CanonicalEntityRegistry:
    entity = registry.entities.get(canonical_entity_id)
    if entity is None or external_identity_id not in (
        entity.external_identity_ids
    ):
        return registry
    entities = dict(registry.entities)
    entities[canonical_entity_id] = entity.model_copy(
        update={
            "external_identity_ids": [
                i for i in entity.external_identity_ids
                if i != external_identity_id
            ]
        }
    )
    identities = dict(registry.external_identities)
    identity = identities.get(external_identity_id)
    if identity is not None:
        identities[external_identity_id] = identity.model_copy(
            update={"unlinked_at": unlinked_at or datetime.now(timezone.utc)}
        )
    return registry.model_copy(
        update={"entities": entities, "external_identities": identities}
    )


def mark_source_availability(
    *,
    registry: CanonicalEntityRegistry,
    external_identity_id: str,
    availability: SourceAvailabilityStatus,
    explanation: str,
    verified_at: datetime | None = None,
) -> CanonicalEntityRegistry:
    """Source content disappearing never erases identity history — the
    mapping survives, annotated as no longer retrievable."""
    identity = registry.external_identities.get(external_identity_id)
    if identity is None:
        return registry
    identities = dict(registry.external_identities)
    identities[external_identity_id] = identity.model_copy(
        update={
            "source_state": SourceReferenceState(
                source_system=identity.source_system,
                source_id=identity.external_id,
                availability=availability,
                last_verified_at=verified_at,
                explanation=explanation,
            )
        }
    )
    return registry.model_copy(update={"external_identities": identities})


# ── Conflict detection ───────────────────────────────────────────────

def detect_identity_conflicts(
    *,
    registry: CanonicalEntityRegistry,
) -> list[EntityIdentityConflict]:
    """Surface contradictions instead of silently choosing: the same
    external identity linked to two entities, or the same alias mapped to
    two canonical entities of one type."""
    conflicts: list[EntityIdentityConflict] = []
    counter = 0

    owners: dict[str, list[str]] = {}
    for entity in registry.entities.values():
        if not entity.active:
            continue
        for identity_id in entity.external_identity_ids:
            owners.setdefault(identity_id, []).append(entity.id)
    for identity_id, entity_ids in sorted(owners.items()):
        if len(entity_ids) > 1:
            counter += 1
            conflicts.append(
                EntityIdentityConflict(
                    id=f"conflict-{counter}",
                    entity_id=sorted(entity_ids)[0],
                    external_identity_ids=[identity_id],
                    conflict_type="identity_multi_link",
                    explanation=(
                        f"External identity '{identity_id}' is linked to "
                        f"{len(entity_ids)} active entities: "
                        f"{', '.join(sorted(entity_ids))}."
                    ),
                )
            )

    alias_owners: dict[tuple[str, str], list[str]] = {}
    for entity in registry.entities.values():
        if not entity.active:
            continue
        for alias in entity.aliases:
            key = (entity.entity_type.value, normalize_entity_alias(alias))
            alias_owners.setdefault(key, []).append(entity.id)
    for (entity_type, alias), entity_ids in sorted(alias_owners.items()):
        if len(set(entity_ids)) > 1:
            counter += 1
            conflicts.append(
                EntityIdentityConflict(
                    id=f"conflict-{counter}",
                    entity_id=sorted(set(entity_ids))[0],
                    conflict_type="alias_collision",
                    explanation=(
                        f"Alias '{alias}' maps to multiple {entity_type} "
                        f"entities: {', '.join(sorted(set(entity_ids)))}."
                    ),
                )
            )

    return conflicts


# ── Integration runtime bridge (duck-typed) ──────────────────────────

def extract_external_identities_from_envelope(
    envelope: object,
) -> list[ExternalEntityIdentity]:
    """Feed ``EvidenceEnvelope``s into the identity layer without
    a dependency reversal: the envelope is read duck-typed."""
    identities: list[ExternalEntityIdentity] = []
    source_system = str(getattr(envelope, "source_system", "unknown"))
    observed_at = getattr(envelope, "observed_at", None) or datetime.now(
        timezone.utc
    )
    source_pointer = getattr(envelope, "source_pointer", None)
    metadata: dict[str, Any] = dict(getattr(envelope, "metadata", {}) or {})

    for actor in getattr(envelope, "actor_refs", []) or []:
        actor_id = str(getattr(actor, "actor_id", "") or "")
        if not actor_id:
            continue
        identities.append(
            ExternalEntityIdentity(
                id=build_external_identity_id(
                    source_system=source_system,
                    identity_type=ExternalIdentityType.USER_ID,
                    external_id=actor_id,
                ),
                entity_type=CanonicalEntityType.PERSON,
                source_system=source_system,
                identity_type=ExternalIdentityType.USER_ID,
                external_id=actor_id,
                display_value=getattr(actor, "display_name", None),
                team_hint=getattr(actor, "team_id", None),
                role_hint=getattr(actor, "role", None),
                source_pointer=source_pointer,
                observed_at=observed_at,
            )
        )

    field_specs = [
        ("email", ExternalIdentityType.EMAIL, CanonicalEntityType.PERSON),
        (
            "repository",
            ExternalIdentityType.REPOSITORY_NAME,
            CanonicalEntityType.REPOSITORY,
        ),
        (
            "project_key",
            ExternalIdentityType.PROJECT_KEY,
            CanonicalEntityType.PROJECT,
        ),
        ("kpi_name", ExternalIdentityType.KPI_NAME, CanonicalEntityType.KPI),
        (
            "service_name",
            ExternalIdentityType.SERVICE_NAME,
            CanonicalEntityType.SERVICE,
        ),
    ]
    for field, identity_type, entity_type in field_specs:
        value = metadata.get(field)
        if not value:
            continue
        identities.append(
            ExternalEntityIdentity(
                id=build_external_identity_id(
                    source_system=source_system,
                    identity_type=identity_type,
                    external_id=str(value),
                ),
                entity_type=entity_type,
                source_system=source_system,
                identity_type=identity_type,
                external_id=str(value),
                display_value=str(value),
                email=str(value) if field == "email" else None,
                source_pointer=source_pointer,
                observed_at=observed_at,
            )
        )

    return identities


# ── Organizational actor bridge ──────────────────────────────────────

def canonicalize_actor_ref(
    *,
    actor: Any,
    registry: CanonicalEntityRegistry,
    policy: EntityResolutionPolicy | None = None,
    observed_at: datetime | None = None,
) -> CanonicalOrganizationalActorRef:
    """Resolve an ``OrganizationalActorRef`` against the
    canonical registry. The actor is read duck-typed so the identity
    layer stays low-level (it imports no higher layer), and the actor
    itself is preserved untouched — the bridge only adds WHO; capacities
    stay in the relationship ontology."""
    actor_id = str(getattr(actor, "actor_id", "") or "")
    identity = ExternalEntityIdentity(
        id=build_external_identity_id(
            source_system="ontology",
            identity_type=ExternalIdentityType.USER_ID,
            external_id=actor_id,
        ),
        entity_type=CanonicalEntityType.PERSON,
        source_system="ontology",
        identity_type=ExternalIdentityType.USER_ID,
        external_id=actor_id,
        display_value=getattr(actor, "display_name", None),
        team_hint=getattr(actor, "team_id", None),
        role_hint=getattr(actor, "role", None),
        observed_at=observed_at or datetime.now(timezone.utc),
    )
    result = resolve_external_identity(
        identity=identity,
        canonical_entities=list(registry.entities.values()),
        existing_identities=list(registry.external_identities.values()),
        policy=policy or EntityResolutionPolicy(),
    )
    return CanonicalOrganizationalActorRef(
        actor=actor,
        canonical_entity_id=result.canonical_entity_id,
        resolution_status=result.status,
    )


class ResolvedCapsuleActors(BaseModel):
    """Resolution alongside a decision capsule — never inside it. The
    capsule's factual content is untouched."""
    capsule_id: str
    requested_by: CanonicalOrganizationalActorRef
    decision_authority: CanonicalOrganizationalActorRef | None
    position_actors: list[CanonicalOrganizationalActorRef] = Field(
        default_factory=list
    )


def resolve_decision_capsule_actors(
    *,
    capsule: object,
    registry: CanonicalEntityRegistry,
    policy: EntityResolutionPolicy | None = None,
) -> ResolvedCapsuleActors:
    """Recognize that Priya in a meeting, Priya in Slack, and Priya in
    the authority field are one canonical actor — without changing a
    single factual field of the capsule."""
    authority = getattr(capsule, "decision_authority", None)
    return ResolvedCapsuleActors(
        capsule_id=str(getattr(capsule, "id", "capsule")),
        requested_by=canonicalize_actor_ref(
            actor=capsule.requested_by, registry=registry, policy=policy,
        ),
        decision_authority=(
            canonicalize_actor_ref(
                actor=authority, registry=registry, policy=policy,
            )
            if authority is not None
            else None
        ),
        position_actors=[
            canonicalize_actor_ref(
                actor=position.actor, registry=registry, policy=policy,
            )
            for position in getattr(capsule, "actor_positions", [])
        ],
    )
