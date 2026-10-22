"""Explicit, auditable, reversible entity merges.

Two canonical entities are never silently merged. A merge is proposed,
gated (PERSON merges are never automatic without strong verified
identity evidence), applied with full pre-merge snapshots, and can be
reversed to the exact prior state. Source entities are marked inactive,
never deleted — identity history survives every merge.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tpllm.identity.evidence import is_strong_identity_evidence
from tpllm.identity.models import (
    CanonicalEntity,
    CanonicalEntityType,
    EntityMergeProposal,
    EntityMergeRecord,
    EntityMergeStatus,
    EntityResolutionEvidence,
    EntityResolutionPolicy,
)
from tpllm.identity.registry import CanonicalEntityRegistry


def propose_entity_merge(
    *,
    source: CanonicalEntity,
    target: CanonicalEntity,
    reason: str,
    evidence_ids: list[str],
    confidence: float,
    created_at: datetime | None = None,
) -> EntityMergeProposal:
    if source.entity_type != target.entity_type:
        raise ValueError(
            f"Cannot propose merging a {source.entity_type.value} into a "
            f"{target.entity_type.value} — types must match."
        )
    return EntityMergeProposal(
        id=f"merge-{source.id}-into-{target.id}",
        source_entity_id=source.id,
        target_entity_id=target.id,
        status=EntityMergeStatus.PROPOSED,
        reason=reason,
        evidence_ids=list(evidence_ids),
        confidence=confidence,
        created_at=created_at or datetime.now(timezone.utc),
    )


def can_auto_merge_entities(
    *,
    source: CanonicalEntity,
    target: CanonicalEntity,
    evidence: list[EntityResolutionEvidence],
    policy: EntityResolutionPolicy,
) -> bool:
    """PERSON: never automatic unless a strong verified identity mapping
    exists AND the person threshold is met. Less sensitive entities
    (repository, service, KPI, project) may auto-merge at high
    confidence — still with a full merge record."""
    if source.entity_type != target.entity_type:
        return False
    positive_strength = sum(e.strength for e in evidence if e.positive)
    negative_strength = sum(e.strength for e in evidence if not e.positive)
    confidence = max(0.0, min(1.0, positive_strength - negative_strength))

    if source.entity_type == CanonicalEntityType.PERSON:
        has_strong = any(
            is_strong_identity_evidence(e, CanonicalEntityType.PERSON)
            for e in evidence
        )
        return has_strong and confidence >= (
            policy.person_automatic_resolution_threshold
        )
    return confidence >= policy.automatic_resolution_threshold


def approve_merge_proposal(
    proposal: EntityMergeProposal,
) -> EntityMergeProposal:
    return proposal.model_copy(update={"status": EntityMergeStatus.APPROVED})


def reject_merge_proposal(proposal: EntityMergeProposal) -> EntityMergeProposal:
    return proposal.model_copy(update={"status": EntityMergeStatus.REJECTED})


def apply_entity_merge(
    *,
    registry: CanonicalEntityRegistry,
    proposal: EntityMergeProposal,
    applied_at: datetime | None = None,
) -> tuple[CanonicalEntityRegistry, EntityMergeRecord]:
    """Move identities, preserve aliases, mark the source inactive (never
    delete), and emit an immutable merge record with full snapshots."""
    source = registry.entities.get(proposal.source_entity_id)
    target = registry.entities.get(proposal.target_entity_id)
    if source is None or target is None:
        raise ValueError("Merge requires both entities to exist.")
    if source.entity_type != target.entity_type:
        raise ValueError("Merge requires matching entity types.")
    if proposal.status == EntityMergeStatus.REJECTED:
        raise ValueError("A rejected proposal cannot be applied.")

    stamp = applied_at or datetime.now(timezone.utc)

    if not source.active:
        # Idempotent: the merge already happened; nothing moves twice.
        return registry, EntityMergeRecord(
            id=f"mrec-{proposal.id}-duplicate",
            source_entity_id=source.id,
            target_entity_id=target.id,
            status=EntityMergeStatus.APPLIED,
            moved_external_identity_ids=[],
            previous_source_snapshot=source,
            previous_target_snapshot=target,
            evidence_ids=list(proposal.evidence_ids),
            applied_at=stamp,
        )

    moved = [
        i for i in source.external_identity_ids
        if i not in target.external_identity_ids
    ]
    merged_aliases = list(target.aliases)
    for alias in [source.canonical_name, *source.aliases]:
        if alias not in merged_aliases:
            merged_aliases.append(alias)

    entities = dict(registry.entities)
    entities[target.id] = target.model_copy(
        update={
            "external_identity_ids": [
                *target.external_identity_ids, *moved,
            ],
            "aliases": merged_aliases,
            "updated_at": stamp,
        }
    )
    # Inactive, not deleted: history survives.
    entities[source.id] = source.model_copy(
        update={
            "active": False,
            "external_identity_ids": [],
            "updated_at": stamp,
        }
    )

    record = EntityMergeRecord(
        id=f"mrec-{proposal.id}",
        source_entity_id=source.id,
        target_entity_id=target.id,
        status=EntityMergeStatus.APPLIED,
        moved_external_identity_ids=moved,
        previous_source_snapshot=source,
        previous_target_snapshot=target,
        evidence_ids=list(proposal.evidence_ids),
        applied_at=stamp,
    )
    return registry.model_copy(update={"entities": entities}), record


def reverse_entity_merge(
    *,
    registry: CanonicalEntityRegistry,
    merge_record: EntityMergeRecord,
    reversed_at: datetime | None = None,
) -> CanonicalEntityRegistry:
    """Restore both entities exactly as they were before the merge,
    including external identity assignments."""
    entities = dict(registry.entities)
    entities[merge_record.source_entity_id] = (
        merge_record.previous_source_snapshot
    )
    entities[merge_record.target_entity_id] = (
        merge_record.previous_target_snapshot
    )
    return registry.model_copy(update={"entities": entities})
