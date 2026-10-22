"""Entity resolution engine.

external identities → candidate matches → evidence-backed resolution →
canonical entity — never string-similarity → merge-everything.

Ambiguity is a first-class outcome: close candidates return AMBIGUOUS,
people never resolve from weak evidence, and the resolver is fully
deterministic for the same inputs.
"""

from __future__ import annotations

from datetime import datetime

from lltpm.identity.evidence import (
    build_resolution_evidence,
    deduplicate_evidence_by_origin,
    is_strong_identity_evidence,
    kpi_identity_compatible,
)
from lltpm.identity.models import (
    CanonicalEntity,
    CanonicalEntityType,
    EntityResolutionCandidate,
    EntityResolutionEvidence,
    EntityResolutionPolicy,
    EntityResolutionResult,
    EntityResolutionStatus,
    ExternalEntityIdentity,
    normalize_display_name,
    normalize_email,
    normalize_entity_alias,
    normalize_handle,
)

_MEANINGFUL_CANDIDATE_SCORE = 0.25

# Entities whose identity is meaningful only within a project scope: a
# "Beta" milestone in one project never matches "Beta" in another.
_PROJECT_SCOPED_TYPES = {
    CanonicalEntityType.KPI,
    CanonicalEntityType.MILESTONE,
    CanonicalEntityType.WORKSTREAM,
    CanonicalEntityType.FEATURE,
}


def _project_scope_compatible(
    identity: ExternalEntityIdentity, candidate: CanonicalEntity
) -> bool:
    if identity.entity_type not in _PROJECT_SCOPED_TYPES:
        return True
    identity_project = identity.metadata.get("project_id")
    candidate_project = candidate.metadata.get("project_id")
    if identity_project and candidate_project:
        return identity_project == candidate_project
    return True


def generate_resolution_candidates(
    *,
    identity: ExternalEntityIdentity,
    canonical_entities: list[CanonicalEntity],
    linked_identities: list[ExternalEntityIdentity],
    policy: EntityResolutionPolicy,
) -> list[CanonicalEntity]:
    """Candidates via exact IDs, emails, handles, names, aliases, and
    hints. Entity types must match — a PERSON never matches a TEAM, a
    KPI never matches a SERVICE. KPI candidates must be semantically
    compatible."""
    identities_by_id = {i.id: i for i in linked_identities}
    candidates: list[CanonicalEntity] = []

    display = identity.display_value or identity.external_id
    normalized_display = normalize_display_name(display)
    normalized_alias = normalize_entity_alias(display)
    identity_email = normalize_email(identity.email) if identity.email else None
    identity_handle = (
        normalize_handle(identity.handle) if identity.handle else None
    )

    for entity in canonical_entities:
        if not entity.active:
            continue
        if entity.entity_type != identity.entity_type:
            continue  # type mismatch is never a candidate
        if not _project_scope_compatible(identity, entity):
            continue  # out-of-scope project entities never match
        if identity.entity_type == CanonicalEntityType.KPI:
            if not kpi_identity_compatible(
                identity=identity, candidate=entity,
                metadata=identity.metadata,
            ):
                continue

        matched = False
        linked = [
            identities_by_id[i]
            for i in entity.external_identity_ids
            if i in identities_by_id
        ]
        for other in linked:
            if (
                other.source_system == identity.source_system
                and other.external_id == identity.external_id
            ):
                matched = True
            if identity_email and other.email and normalize_email(
                other.email
            ) == identity_email:
                matched = True
            if identity_handle and other.handle and normalize_handle(
                other.handle
            ) == identity_handle:
                matched = True
        if identity_email and entity.metadata.get("email") and (
            normalize_email(str(entity.metadata["email"])) == identity_email
        ):
            matched = True
        if normalize_display_name(entity.canonical_name) == normalized_display:
            matched = True
        if any(
            normalize_entity_alias(alias) == normalized_alias
            for alias in entity.aliases
        ):
            matched = True

        if matched:
            candidates.append(entity)

    candidates.sort(key=lambda e: e.id)
    return candidates[: policy.maximum_candidates]


def score_resolution_candidate(
    *,
    identity: ExternalEntityIdentity,
    candidate: CanonicalEntity,
    evidence: list[EntityResolutionEvidence],
    policy: EntityResolutionPolicy,
) -> EntityResolutionCandidate:
    """Transparent deterministic scoring: Σ positive − Σ negative,
    source-origin deduplicated, clamped to [0, 1]."""
    deduped = deduplicate_evidence_by_origin(evidence)
    positive = [e for e in deduped if e.positive]
    negative = [e for e in deduped if not e.positive]

    raw = sum(e.strength for e in positive) - sum(
        e.strength for e in negative
    )
    score = max(0.0, min(1.0, raw))

    parts = [f"+{e.strength:.2f} {e.evidence_type.value}" for e in positive]
    parts += [f"-{e.strength:.2f} {e.evidence_type.value}" for e in negative]

    return EntityResolutionCandidate(
        external_identity_id=identity.id,
        candidate_entity_id=candidate.id,
        score=score,
        status=EntityResolutionStatus.CANDIDATE,
        positive_evidence_ids=[e.id for e in positive],
        negative_evidence_ids=[e.id for e in negative],
        explanation="; ".join(parts) if parts else "no evidence",
    )


def _has_strong_person_evidence(
    candidate: EntityResolutionCandidate,
    evidence: list[EntityResolutionEvidence],
) -> bool:
    evidence_by_id = {e.id: e for e in evidence}
    return any(
        is_strong_identity_evidence(
            evidence_by_id[eid], CanonicalEntityType.PERSON
        )
        for eid in candidate.positive_evidence_ids
        if eid in evidence_by_id
    )


def resolve_external_identity(
    *,
    identity: ExternalEntityIdentity,
    canonical_entities: list[CanonicalEntity],
    existing_identities: list[ExternalEntityIdentity],
    policy: EntityResolutionPolicy,
) -> EntityResolutionResult:
    """Generate → evidence → score → sort → threshold + margin + PERSON
    strictness → RESOLVED / AMBIGUOUS / CANDIDATE / UNRESOLVED."""
    candidates = generate_resolution_candidates(
        identity=identity,
        canonical_entities=canonical_entities,
        linked_identities=existing_identities,
        policy=policy,
    )

    if not candidates:
        return EntityResolutionResult(
            external_identity_id=identity.id,
            status=EntityResolutionStatus.UNRESOLVED,
            confidence=0.0,
            explanation=(
                "No canonical entity of matching type resembles this "
                "identity."
            ),
        )

    all_evidence: dict[str, list[EntityResolutionEvidence]] = {}
    scored: list[EntityResolutionCandidate] = []
    for candidate in candidates:
        evidence = build_resolution_evidence(
            identity=identity,
            candidate=candidate,
            existing_identities=existing_identities,
        )
        all_evidence[candidate.id] = evidence
        scored.append(
            score_resolution_candidate(
                identity=identity,
                candidate=candidate,
                evidence=evidence,
                policy=policy,
            )
        )

    scored.sort(key=lambda c: (-c.score, c.candidate_entity_id))
    top = scored[0]
    second_score = scored[1].score if len(scored) > 1 else 0.0

    is_person = identity.entity_type == CanonicalEntityType.PERSON
    threshold = (
        policy.person_automatic_resolution_threshold
        if is_person
        else policy.automatic_resolution_threshold
    )

    # Margin rule: two closely scored meaningful candidates → AMBIGUOUS,
    # even when the top would otherwise pass the threshold.
    if (
        len(scored) >= 2
        and second_score >= _MEANINGFUL_CANDIDATE_SCORE
        and (top.score - second_score) < policy.ambiguous_margin
    ):
        return EntityResolutionResult(
            external_identity_id=identity.id,
            status=EntityResolutionStatus.AMBIGUOUS,
            canonical_entity_id=None,
            candidates=scored,
            confidence=top.score,
            explanation=(
                f"{len(scored)} canonical entities match this identity "
                f"within the ambiguity margin. More identity evidence is "
                f"required; no guess is made."
            ),
        )

    if top.score >= threshold:
        if is_person and policy.require_strong_person_identifier:
            if not _has_strong_person_evidence(
                top, all_evidence[top.candidate_entity_id]
            ):
                return EntityResolutionResult(
                    external_identity_id=identity.id,
                    status=EntityResolutionStatus.AMBIGUOUS,
                    canonical_entity_id=None,
                    candidates=scored,
                    confidence=top.score,
                    explanation=(
                        "Score passes the threshold but no strong person "
                        "identifier (verified email, linked ID, explicit "
                        "alias, or human confirmation) exists — a person "
                        "never auto-resolves from weak evidence."
                    ),
                )
        return EntityResolutionResult(
            external_identity_id=identity.id,
            status=EntityResolutionStatus.RESOLVED,
            canonical_entity_id=top.candidate_entity_id,
            candidates=scored,
            confidence=top.score,
            explanation=(
                f"Resolved to {top.candidate_entity_id} "
                f"({top.explanation})."
            ),
        )

    if len(scored) >= 2 and second_score >= _MEANINGFUL_CANDIDATE_SCORE:
        return EntityResolutionResult(
            external_identity_id=identity.id,
            status=EntityResolutionStatus.AMBIGUOUS,
            canonical_entity_id=None,
            candidates=scored,
            confidence=top.score,
            explanation=(
                "Multiple canonical entities partially match; none passes "
                "the resolution threshold."
            ),
        )

    return EntityResolutionResult(
        external_identity_id=identity.id,
        status=EntityResolutionStatus.CANDIDATE,
        canonical_entity_id=None,
        candidates=scored,
        confidence=top.score,
        explanation=(
            f"Best candidate {top.candidate_entity_id} scores "
            f"{top.score:.2f}, below the "
            f"{'person ' if is_person else ''}threshold {threshold:.2f} — "
            f"kept as a candidate, not resolved."
        ),
    )


def resolve_identity_as_of(
    *,
    identity: ExternalEntityIdentity,
    registry_history: list[object],
    as_of: datetime,
) -> EntityResolutionResult:
    """Point-in-time resolution against the most recent historical
    snapshot at or before ``as_of``. Advanced temporal queries are
    deferred; the timestamps that make them possible exist today."""
    eligible = [
        snapshot
        for snapshot in registry_history
        if getattr(snapshot, "as_of", None) is not None
        and snapshot.as_of <= as_of
    ]
    if not eligible:
        return EntityResolutionResult(
            external_identity_id=identity.id,
            status=EntityResolutionStatus.UNRESOLVED,
            confidence=0.0,
            explanation="No identity snapshot exists at or before as_of.",
        )
    snapshot = sorted(eligible, key=lambda s: s.as_of)[-1]
    return resolve_external_identity(
        identity=identity,
        canonical_entities=list(snapshot.canonical_entities),
        existing_identities=list(snapshot.external_identities),
        policy=EntityResolutionPolicy(),
    )
