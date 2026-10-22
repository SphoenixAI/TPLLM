"""Identity graph read model.

A projection of canonical entities, external identities, and IDENTIFIES
edges from resolution results. Read model only — it never mutates
organizational or structural graphs. REPRESENTS edges keep repositories
and services distinct entities that relate, never one entity.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from tpllm.identity.models import (
    CanonicalEntity,
    EntityResolutionResult,
    EntityResolutionStatus,
    ExternalEntityIdentity,
    IdentityGraphEdge,
    IdentityGraphEdgeType,
)


class IdentityGraphSnapshot(BaseModel):
    id: str
    canonical_entities: list[CanonicalEntity] = Field(default_factory=list)
    external_identities: list[ExternalEntityIdentity] = Field(
        default_factory=list
    )
    edges: list[IdentityGraphEdge] = Field(default_factory=list)
    as_of: datetime


def build_identifies_edge(
    *,
    external_identity_id: str,
    canonical_entity_id: str,
    confidence: float,
    evidence_ids: list[str] | None = None,
) -> IdentityGraphEdge:
    return IdentityGraphEdge(
        id=f"edge-identifies-{external_identity_id}-{canonical_entity_id}",
        source_id=external_identity_id,
        target_id=canonical_entity_id,
        edge_type=IdentityGraphEdgeType.IDENTIFIES,
        evidence_ids=list(evidence_ids or []),
        confidence=confidence,
    )


def build_represents_edge(
    *,
    source_entity_id: str,
    target_entity_id: str,
    confidence: float,
    evidence_ids: list[str] | None = None,
) -> IdentityGraphEdge:
    """e.g. repository REPRESENTS service — related, never equal."""
    return IdentityGraphEdge(
        id=f"edge-represents-{source_entity_id}-{target_entity_id}",
        source_id=source_entity_id,
        target_id=target_entity_id,
        edge_type=IdentityGraphEdgeType.REPRESENTS,
        evidence_ids=list(evidence_ids or []),
        confidence=confidence,
    )


def build_identity_graph_snapshot(
    *,
    canonical_entities: list[CanonicalEntity],
    external_identities: list[ExternalEntityIdentity],
    resolution_results: list[EntityResolutionResult],
    as_of: datetime,
    extra_edges: list[IdentityGraphEdge] | None = None,
) -> IdentityGraphSnapshot:
    """Deterministic read-model snapshot. Only RESOLVED results produce
    IDENTIFIES edges; ambiguous identities appear as nodes without
    edges — visibly unresolved rather than silently guessed."""
    edges: list[IdentityGraphEdge] = []
    for result in sorted(
        resolution_results, key=lambda r: r.external_identity_id
    ):
        if result.status != EntityResolutionStatus.RESOLVED:
            continue
        if result.canonical_entity_id is None:
            continue
        top_evidence: list[str] = []
        for candidate in result.candidates:
            if candidate.candidate_entity_id == result.canonical_entity_id:
                top_evidence = candidate.positive_evidence_ids
                break
        edges.append(
            build_identifies_edge(
                external_identity_id=result.external_identity_id,
                canonical_entity_id=result.canonical_entity_id,
                confidence=result.confidence,
                evidence_ids=top_evidence,
            )
        )

    for edge in extra_edges or []:
        edges.append(edge)

    return IdentityGraphSnapshot(
        id=f"idgraph-{as_of.isoformat()}",
        canonical_entities=sorted(canonical_entities, key=lambda e: e.id),
        external_identities=sorted(
            external_identities, key=lambda i: i.id
        ),
        edges=edges,
        as_of=as_of,
    )
