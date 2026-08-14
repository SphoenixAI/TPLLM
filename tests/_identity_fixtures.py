"""Shared fixtures for identity-layer tests (Commit 19)."""

from __future__ import annotations

from datetime import datetime

from lltpm.identity import (
    CanonicalEntity,
    CanonicalEntityRegistry,
    CanonicalEntityType,
    EntityResolutionPolicy,
    ExternalEntityIdentity,
    ExternalIdentityType,
    build_external_identity_id,
    link_external_identity,
    register_canonical_entity,
    register_external_identity,
)

AS_OF = datetime(2026, 8, 12, 12, 0, 0)
POLICY = EntityResolutionPolicy()


def entity(
    eid: str = "person_priya_rao",
    entity_type: CanonicalEntityType = CanonicalEntityType.PERSON,
    name: str = "Priya Rao",
    aliases: list[str] | None = None,
    metadata: dict | None = None,
) -> CanonicalEntity:
    return CanonicalEntity(
        id=eid,
        entity_type=entity_type,
        canonical_name=name,
        aliases=aliases or [],
        created_at=AS_OF,
        updated_at=AS_OF,
        metadata=metadata or {},
    )


def identity(
    source_system: str = "slack",
    identity_type: ExternalIdentityType = ExternalIdentityType.USER_ID,
    external_id: str = "U129",
    entity_type: CanonicalEntityType = CanonicalEntityType.PERSON,
    **overrides,
) -> ExternalEntityIdentity:
    return ExternalEntityIdentity(
        id=build_external_identity_id(
            source_system=source_system,
            identity_type=identity_type,
            external_id=external_id,
        ),
        entity_type=entity_type,
        source_system=source_system,
        identity_type=identity_type,
        external_id=external_id,
        observed_at=AS_OF,
        **overrides,
    )


def registry_with(
    entities: list[CanonicalEntity],
    linked: list[tuple[ExternalEntityIdentity, str]] = (),
) -> CanonicalEntityRegistry:
    registry = CanonicalEntityRegistry()
    for item in entities:
        registry = register_canonical_entity(registry=registry, entity=item)
    for external, entity_id in linked:
        registry = register_external_identity(
            registry=registry, identity=external,
        )
        registry = link_external_identity(
            registry=registry,
            external_identity_id=external.id,
            canonical_entity_id=entity_id,
            linked_at=AS_OF,
        )
    return registry


def priya_registry() -> CanonicalEntityRegistry:
    """The spec's cross-system scenario: person_priya_rao with a linked
    Jira email identity (the verified bridge)."""
    person = entity(metadata={"team": "platform-architecture",
                              "role": "Platform Architecture Lead"})
    jira = identity(
        source_system="jira",
        identity_type=ExternalIdentityType.EMAIL,
        external_id="priya.rao@company.com",
        email="priya.rao@company.com",
        display_value="Priya Rao",
    )
    return registry_with([person], [(jira, person.id)])
