"""The resolution engine's safety behavior."""

from __future__ import annotations

from datetime import datetime

import pytest

from lltpm.identity import (
    CanonicalEntityType,
    EntityResolutionEvidence,
    EntityResolutionStatus,
    ExternalIdentityType,
    IdentityEvidenceType,
    resolve_external_identity,
    score_resolution_candidate,
)
from tests._identity_fixtures import (
    AS_OF,
    POLICY,
    entity,
    identity,
    priya_registry,
)


def _resolve(new_identity, registry):
    return resolve_external_identity(
        identity=new_identity,
        canonical_entities=list(registry.entities.values()),
        existing_identities=list(registry.external_identities.values()),
        policy=POLICY,
    )


def _alex_registry():
    """Two Alex Chens, same team, no strong identifiers anywhere."""
    from tests._identity_fixtures import registry_with

    alex_a = entity("person_alex_chen_a", name="Alex Chen",
                    metadata={"team": "platform"})
    alex_b = entity("person_alex_chen_b", name="Alex Chen",
                    metadata={"team": "platform"})
    return registry_with([alex_a, alex_b])


class TestPersonResolution:
    def test_exact_verified_email_resolves_person(self):
        registry = priya_registry()
        slack = identity(
            source_system="slack", external_id="U129",
            display_value="Priya Rao", email="priya.rao@company.com",
        )
        result = _resolve(slack, registry)
        assert result.status == EntityResolutionStatus.RESOLVED
        assert result.canonical_entity_id == "person_priya_rao"
        assert result.confidence >= 0.98

    def test_previously_linked_external_id_resolves(self):
        registry = priya_registry()
        # First linked via email; now the same Jira identity arrives again.
        same_jira = identity(
            source_system="jira",
            identity_type=ExternalIdentityType.EMAIL,
            external_id="priya.rao@company.com",
            email="priya.rao@company.com",
        )
        result = _resolve(same_jira, registry)
        assert result.status == EntityResolutionStatus.RESOLVED
        assert result.canonical_entity_id == "person_priya_rao"

    def test_display_name_alone_does_not_resolve_person(self):
        registry = priya_registry()
        meet = identity(
            source_system="meet", external_id="meet-attendee-7",
            display_value="Priya Rao",
        )
        result = _resolve(meet, registry)
        assert result.status != EntityResolutionStatus.RESOLVED
        assert result.canonical_entity_id is None

    def test_name_and_team_alone_do_not_resolve_person(self):
        registry = priya_registry()
        meet = identity(
            source_system="meet", external_id="meet-attendee-7",
            display_value="Priya Rao", team_hint="platform-architecture",
            role_hint="Platform Architecture Lead",
        )
        result = _resolve(meet, registry)
        # 0.25 + 0.15 + 0.10 = 0.50 — never enough for a human.
        assert result.status != EntityResolutionStatus.RESOLVED
        assert result.canonical_entity_id is None

    def test_two_alex_chens_are_ambiguous(self):
        result = _resolve(
            identity(source_system="slack", external_id="U777",
                     display_value="Alex Chen", team_hint="platform"),
            _alex_registry(),
        )
        assert result.status == EntityResolutionStatus.AMBIGUOUS
        assert result.canonical_entity_id is None
        assert len(result.candidates) == 2
        assert "no guess" in result.explanation.lower()


class TestScoringBehavior:
    def test_negative_evidence_lowers_score(self):
        from tests._identity_fixtures import registry_with

        person = entity(metadata={})
        other_jira = identity(
            source_system="jira",
            identity_type=ExternalIdentityType.EMAIL,
            external_id="someone.else@company.com",
            email="someone.else@company.com",
        )
        registry = registry_with([person], [(other_jira, person.id)])
        # Same display name but a *different* verified email.
        imposter = identity(
            source_system="slack", external_id="U888",
            display_value="Priya Rao", email="priya.rao@company.com",
        )
        result = _resolve(imposter, registry)
        assert result.status != EntityResolutionStatus.RESOLVED
        top = result.candidates[0]
        assert top.negative_evidence_ids  # email mismatch recorded
        assert top.score < 0.25  # 0.25 name − 0.90 mismatch → clamped low

    def test_strong_evidence_outweighs_weak_name_mismatch(self):
        registry = priya_registry()
        github = identity(
            source_system="github",
            identity_type=ExternalIdentityType.HANDLE,
            external_id="prao-infra",
            handle="prao-infra",
            display_value="P. Rao",  # does not match "Priya Rao"
            email="priya.rao@company.com",  # verified bridge
        )
        result = _resolve(github, registry)
        assert result.status == EntityResolutionStatus.RESOLVED
        assert result.canonical_entity_id == "person_priya_rao"

    def test_shared_origin_duplicates_do_not_inflate(self):
        person = entity()
        slack = identity(display_value="Priya Rao")

        def evidence_item(eid):
            return EntityResolutionEvidence(
                id=eid,
                external_identity_id=slack.id,
                candidate_entity_id=person.id,
                evidence_type=IdentityEvidenceType.DISPLAY_NAME_MATCH,
                strength=0.25,
                explanation="Name stated in Slack export",
                source_ids=["slack-export-1"],  # same origin
                observed_at=AS_OF,
            )

        # Slack export + transcript quoting it + summary quoting the
        # transcript: one observation, not three proofs.
        candidate = score_resolution_candidate(
            identity=slack,
            candidate=person,
            evidence=[evidence_item("e1"), evidence_item("e2"),
                      evidence_item("e3")],
            policy=POLICY,
        )
        assert candidate.score == pytest.approx(0.25)

    def test_entity_type_mismatch_rejected(self):
        from tests._identity_fixtures import registry_with

        team = entity("team_platform", CanonicalEntityType.TEAM, "Priya Rao")
        registry = registry_with([team])
        person_identity = identity(display_value="Priya Rao")
        result = _resolve(person_identity, registry)
        assert result.status == EntityResolutionStatus.UNRESOLVED
        assert result.candidates == []

    def test_resolution_deterministic(self):
        registry = _alex_registry()
        new_identity = identity(
            source_system="slack", external_id="U777",
            display_value="Alex Chen", team_hint="platform",
        )
        first = _resolve(new_identity, registry).model_dump()
        second = _resolve(new_identity, registry).model_dump()
        assert first == second

    def test_empty_candidate_set_unresolved(self):
        from lltpm.identity import CanonicalEntityRegistry

        result = _resolve(identity(), CanonicalEntityRegistry())
        assert result.status == EntityResolutionStatus.UNRESOLVED
        assert result.confidence == 0.0

    def test_close_scores_return_ambiguous_even_above_floor(self):
        # Margin rule: two service candidates both matched via explicit
        # aliases score identically → AMBIGUOUS despite high scores.
        from tests._identity_fixtures import registry_with

        service_a = entity("svc-a", CanonicalEntityType.SERVICE,
                           "Account Graph", aliases=["AGS"])
        service_b = entity("svc-b", CanonicalEntityType.SERVICE,
                           "Aggregation Service", aliases=["AGS"])
        registry = registry_with([service_a, service_b])
        result = _resolve(
            identity(
                source_system="jira",
                identity_type=ExternalIdentityType.ISSUE_COMPONENT,
                external_id="AGS",
                entity_type=CanonicalEntityType.SERVICE,
                display_value="AGS",
            ),
            registry,
        )
        assert result.status == EntityResolutionStatus.AMBIGUOUS
        assert result.canonical_entity_id is None
