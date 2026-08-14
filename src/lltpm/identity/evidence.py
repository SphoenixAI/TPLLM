"""Identity resolution evidence (Commit 19).

Evidence explains itself, strengths come from configurable defaults
(never hardcoded in models), negative evidence lowers confidence, and
shared-origin duplicates never multiply certainty.
"""

from __future__ import annotations

from datetime import datetime

from lltpm.identity.models import (
    CanonicalEntity,
    CanonicalEntityType,
    EntityResolutionEvidence,
    ExternalEntityIdentity,
    IdentityEvidenceType,
    normalize_display_name,
    normalize_email,
    normalize_entity_alias,
    normalize_handle,
)

# Configurable default strengths (policy-level, not baked into models).
DEFAULT_EVIDENCE_STRENGTHS: dict[IdentityEvidenceType, float] = {
    IdentityEvidenceType.EXACT_EXTERNAL_ID: 0.99,
    IdentityEvidenceType.EXACT_EMAIL: 0.98,
    IdentityEvidenceType.VERIFIED_EMAIL_ALIAS: 0.95,
    IdentityEvidenceType.EXACT_HANDLE: 0.60,
    IdentityEvidenceType.DISPLAY_NAME_MATCH: 0.25,
    IdentityEvidenceType.TEAM_MATCH: 0.15,
    IdentityEvidenceType.ROLE_MATCH: 0.10,
    IdentityEvidenceType.SHARED_SOURCE_REFERENCE: 0.30,
    IdentityEvidenceType.REPOSITORY_METADATA: 0.50,
    IdentityEvidenceType.SERVICE_METADATA: 0.50,
    IdentityEvidenceType.PROJECT_METADATA: 0.50,
    IdentityEvidenceType.EXPLICIT_ALIAS: 0.90,
    IdentityEvidenceType.HUMAN_CONFIRMATION: 1.00,
    IdentityEvidenceType.NEGATIVE_EVIDENCE: 0.90,
}

# Strong identifiers for PERSON: verified bridges only. Names, teams,
# and roles are never strong enough to identify a human.
_PERSON_STRONG_TYPES = {
    IdentityEvidenceType.EXACT_EXTERNAL_ID,
    IdentityEvidenceType.EXACT_EMAIL,
    IdentityEvidenceType.VERIFIED_EMAIL_ALIAS,
    IdentityEvidenceType.EXPLICIT_ALIAS,
    IdentityEvidenceType.HUMAN_CONFIRMATION,
}

_GENERAL_STRONG_TYPES = _PERSON_STRONG_TYPES | {
    IdentityEvidenceType.EXACT_HANDLE,
    IdentityEvidenceType.REPOSITORY_METADATA,
    IdentityEvidenceType.SERVICE_METADATA,
    IdentityEvidenceType.PROJECT_METADATA,
}


def is_strong_identity_evidence(
    evidence: EntityResolutionEvidence,
    entity_type: CanonicalEntityType,
) -> bool:
    if not evidence.positive:
        return False
    if entity_type == CanonicalEntityType.PERSON:
        return evidence.evidence_type in _PERSON_STRONG_TYPES
    return evidence.evidence_type in _GENERAL_STRONG_TYPES


def kpi_identity_compatible(
    *,
    identity: ExternalEntityIdentity,
    candidate: CanonicalEntity,
    metadata: dict,
) -> bool:
    """KPI aliases resolve on semantics, not names. Incompatible units
    ('percent' vs 'count') make candidates incompatible regardless of
    name similarity. Missing semantics stay compatible-but-ambiguous —
    the resolver decides, never a forced match."""
    identity_unit = (
        metadata.get("unit")
        or identity.metadata.get("unit")
    )
    candidate_unit = candidate.metadata.get("unit")
    if identity_unit and candidate_unit:
        if str(identity_unit).strip().lower() != (
            str(candidate_unit).strip().lower()
        ):
            return False
    identity_project = (
        metadata.get("project_id") or identity.metadata.get("project_id")
    )
    candidate_project = candidate.metadata.get("project_id")
    if identity_project and candidate_project:
        if identity_project != candidate_project:
            return False
    return True


def build_resolution_evidence(
    *,
    identity: ExternalEntityIdentity,
    candidate: CanonicalEntity,
    existing_identities: list[ExternalEntityIdentity],
) -> list[EntityResolutionEvidence]:
    """Deterministic, self-explaining evidence between one external
    identity and one candidate. ``existing_identities`` are identities
    already linked to the candidate."""
    evidence: list[EntityResolutionEvidence] = []
    counter = 0

    def add(
        evidence_type: IdentityEvidenceType,
        explanation: str,
        *,
        positive: bool = True,
        source_ids: list[str] | None = None,
        strength: float | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        nonlocal counter
        counter += 1
        evidence.append(
            EntityResolutionEvidence(
                id=f"idev-{identity.id}-{candidate.id}-{counter}",
                external_identity_id=identity.id,
                candidate_entity_id=candidate.id,
                evidence_type=evidence_type,
                strength=(
                    strength
                    if strength is not None
                    else DEFAULT_EVIDENCE_STRENGTHS[evidence_type]
                ),
                positive=positive,
                explanation=explanation,
                source_ids=source_ids or [identity.id],
                observed_at=observed_at or identity.observed_at,
            )
        )

    linked = [
        i for i in existing_identities
        if i.id in candidate.external_identity_ids and i.unlinked_at is None
    ]

    # Exact already-linked external ID (same system, same ID).
    for other in linked:
        if (
            other.source_system == identity.source_system
            and other.external_id == identity.external_id
        ):
            add(
                IdentityEvidenceType.EXACT_EXTERNAL_ID,
                f"External ID '{identity.external_id}' in "
                f"{identity.source_system} is already linked to this entity.",
                source_ids=[other.id],
            )

    # Verified email bridge.
    if identity.email:
        identity_email = normalize_email(identity.email)
        for other in linked:
            if other.email and normalize_email(other.email) == identity_email:
                add(
                    IdentityEvidenceType.EXACT_EMAIL,
                    f"Verified email '{identity_email}' matches an identity "
                    f"already linked via {other.source_system}.",
                    source_ids=[other.id],
                )
                break
        else:
            candidate_email = candidate.metadata.get("email")
            if candidate_email and normalize_email(str(candidate_email)) == (
                identity_email
            ):
                add(
                    IdentityEvidenceType.EXACT_EMAIL,
                    f"Verified email '{identity_email}' matches the "
                    f"canonical entity record.",
                    source_ids=[candidate.id],
                )

        # Negative: both sides carry verified emails that differ.
        for other in linked:
            if other.email and normalize_email(other.email) != identity_email:
                add(
                    IdentityEvidenceType.NEGATIVE_EVIDENCE,
                    f"Different verified emails: '{identity_email}' vs "
                    f"'{normalize_email(other.email)}'.",
                    positive=False,
                    source_ids=[other.id],
                )
                break

    # Handle match.
    if identity.handle:
        identity_handle = normalize_handle(identity.handle)
        for other in linked:
            if other.handle and normalize_handle(other.handle) == (
                identity_handle
            ):
                add(
                    IdentityEvidenceType.EXACT_HANDLE,
                    f"Handle '@{identity_handle}' matches a linked identity.",
                    source_ids=[other.id],
                )
                break

    # Explicit alias on the canonical entity.
    display = identity.display_value or identity.external_id
    normalized_display = normalize_entity_alias(display)
    if any(
        normalize_entity_alias(alias) == normalized_display
        for alias in candidate.aliases
    ):
        add(
            IdentityEvidenceType.EXPLICIT_ALIAS,
            f"'{display}' is an explicit alias of "
            f"'{candidate.canonical_name}'.",
        )
    elif identity.display_value and normalize_display_name(
        identity.display_value
    ) == normalize_display_name(candidate.canonical_name):
        add(
            IdentityEvidenceType.DISPLAY_NAME_MATCH,
            f"Display name '{identity.display_value}' matches canonical "
            f"name '{candidate.canonical_name}'.",
        )

    # Team / role hints — always weak.
    if identity.team_hint:
        candidate_team = candidate.metadata.get("team")
        if candidate_team and normalize_entity_alias(
            str(candidate_team)
        ) == normalize_entity_alias(identity.team_hint):
            add(
                IdentityEvidenceType.TEAM_MATCH,
                f"Team hint '{identity.team_hint}' matches.",
            )
    if identity.role_hint:
        candidate_role = candidate.metadata.get("role")
        if candidate_role and normalize_entity_alias(
            str(candidate_role)
        ) == normalize_entity_alias(identity.role_hint):
            add(
                IdentityEvidenceType.ROLE_MATCH,
                f"Role hint '{identity.role_hint}' matches.",
            )

    # KPI semantics: incompatible units are negative evidence.
    if (
        identity.entity_type == CanonicalEntityType.KPI
        and not kpi_identity_compatible(
            identity=identity, candidate=candidate, metadata=identity.metadata,
        )
    ):
        add(
            IdentityEvidenceType.NEGATIVE_EVIDENCE,
            f"KPI semantics incompatible: unit/scope of '{display}' does "
            f"not match '{candidate.canonical_name}'.",
            positive=False,
            source_ids=[candidate.id],
        )

    return evidence


def deduplicate_evidence_by_origin(
    evidence: list[EntityResolutionEvidence],
) -> list[EntityResolutionEvidence]:
    """Shared-origin duplicates count once: three copies of the same
    identity fact (an export, a transcript quoting it, a summary quoting
    the transcript) are one observation, not three proofs."""
    deduped: list[EntityResolutionEvidence] = []
    seen_origins: dict[tuple[IdentityEvidenceType, bool], set[str]] = {}
    for item in evidence:
        key = (item.evidence_type, item.positive)
        origins = seen_origins.setdefault(key, set())
        item_origins = set(item.source_ids) or {item.id}
        if origins & item_origins:
            continue  # same underlying origin already counted
        if not item.source_ids and any(
            d.evidence_type == item.evidence_type
            and d.positive == item.positive
            for d in deduped
        ):
            continue
        origins |= item_origins
        deduped.append(item)
    return deduped
