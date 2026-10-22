"""Canonical identity layer.

Connects identities aggressively enough to reconstruct organizational
reality, but conservatively enough that TPLLM never invents who
somebody is. Identity answers WHO; the relationship ontology answers IN
WHAT CAPACITY — the two are never collapsed.

This layer sits low in the dependency graph (integrations → identity →
ontology/intelligence/coordination). It never grants authority, sends
nothing, and scores no people.
"""

from tpllm.identity.evidence import (
    DEFAULT_EVIDENCE_STRENGTHS,
    build_resolution_evidence,
    deduplicate_evidence_by_origin,
    is_strong_identity_evidence,
    kpi_identity_compatible,
)
from tpllm.identity.graph import (
    IdentityGraphSnapshot,
    build_identifies_edge,
    build_identity_graph_snapshot,
    build_represents_edge,
)
from tpllm.identity.merge import (
    apply_entity_merge,
    approve_merge_proposal,
    can_auto_merge_entities,
    propose_entity_merge,
    reject_merge_proposal,
    reverse_entity_merge,
)
from tpllm.identity.models import (
    CanonicalEntity,
    CanonicalEntityType,
    CanonicalOrganizationalActorRef,
    EntityIdentityConflict,
    EntityMergeProposal,
    EntityMergeRecord,
    EntityMergeStatus,
    EntityResolutionCandidate,
    EntityResolutionEvidence,
    EntityResolutionPolicy,
    EntityResolutionResult,
    EntityResolutionStatus,
    ExplicitEntityAlias,
    ExternalEntityIdentity,
    ExternalIdentityType,
    IdentityEvidenceType,
    IdentityGraphEdge,
    IdentityGraphEdgeType,
    SourceAvailabilityStatus,
    SourceReferenceState,
    build_external_identity_id,
    normalize_display_name,
    normalize_email,
    normalize_entity_alias,
    normalize_handle,
)
from tpllm.identity.registry import (
    CanonicalEntityRegistry,
    ResolvedCapsuleActors,
    canonicalize_actor_ref,
    detect_identity_conflicts,
    extract_external_identities_from_envelope,
    link_external_identity,
    mark_source_availability,
    register_canonical_entity,
    register_explicit_alias,
    register_external_identity,
    resolve_decision_capsule_actors,
    unlink_external_identity,
)
from tpllm.identity.resolution import (
    generate_resolution_candidates,
    resolve_external_identity,
    resolve_identity_as_of,
    score_resolution_candidate,
)

__all__ = [
    "DEFAULT_EVIDENCE_STRENGTHS",
    "CanonicalEntity",
    "CanonicalEntityRegistry",
    "CanonicalEntityType",
    "CanonicalOrganizationalActorRef",
    "EntityIdentityConflict",
    "EntityMergeProposal",
    "EntityMergeRecord",
    "EntityMergeStatus",
    "EntityResolutionCandidate",
    "EntityResolutionEvidence",
    "EntityResolutionPolicy",
    "EntityResolutionResult",
    "EntityResolutionStatus",
    "ExplicitEntityAlias",
    "ExternalEntityIdentity",
    "ExternalIdentityType",
    "IdentityEvidenceType",
    "IdentityGraphEdge",
    "IdentityGraphEdgeType",
    "IdentityGraphSnapshot",
    "ResolvedCapsuleActors",
    "SourceAvailabilityStatus",
    "SourceReferenceState",
    "apply_entity_merge",
    "approve_merge_proposal",
    "build_external_identity_id",
    "build_identifies_edge",
    "build_identity_graph_snapshot",
    "build_represents_edge",
    "build_resolution_evidence",
    "can_auto_merge_entities",
    "canonicalize_actor_ref",
    "deduplicate_evidence_by_origin",
    "detect_identity_conflicts",
    "extract_external_identities_from_envelope",
    "generate_resolution_candidates",
    "is_strong_identity_evidence",
    "kpi_identity_compatible",
    "link_external_identity",
    "mark_source_availability",
    "normalize_display_name",
    "normalize_email",
    "normalize_entity_alias",
    "normalize_handle",
    "propose_entity_merge",
    "register_canonical_entity",
    "register_explicit_alias",
    "register_external_identity",
    "reject_merge_proposal",
    "resolve_decision_capsule_actors",
    "resolve_external_identity",
    "resolve_identity_as_of",
    "reverse_entity_merge",
    "score_resolution_candidate",
    "unlink_external_identity",
]
