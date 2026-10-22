"""Identity models, policy, normalization, and
source-level guardrails."""

from __future__ import annotations

import pytest

from lltpm.identity import (
    CanonicalEntity,
    CanonicalEntityType,
    EntityMergeStatus,
    EntityResolutionCandidate,
    EntityResolutionEvidence,
    EntityResolutionPolicy,
    EntityResolutionStatus,
    ExternalEntityIdentity,
    IdentityEvidenceType,
    build_external_identity_id,
    normalize_display_name,
    normalize_email,
    normalize_entity_alias,
    normalize_handle,
    propose_entity_merge,
)
from lltpm.identity import ExternalIdentityType
from tests._identity_fixtures import AS_OF, entity, identity


class TestCoreModels:
    def test_canonical_entity_validates(self):
        person = entity()
        assert person.entity_type == CanonicalEntityType.PERSON
        assert person.active is True

    def test_external_identity_validates_with_timestamps(self):
        slack = identity(display_value="Priya Rao")
        assert slack.observed_at == AS_OF
        # Temporal fields exist for historical resolution.
        assert slack.linked_at is None
        assert slack.unlinked_at is None

    def test_evidence_strength_bounded(self):
        with pytest.raises(ValueError, match="within"):
            EntityResolutionEvidence(
                id="e1", external_identity_id="x", candidate_entity_id="c",
                evidence_type=IdentityEvidenceType.EXACT_EMAIL,
                strength=1.4, explanation="too strong", observed_at=AS_OF,
            )

    def test_evidence_must_explain_itself(self):
        with pytest.raises(ValueError, match="explain"):
            EntityResolutionEvidence(
                id="e1", external_identity_id="x", candidate_entity_id="c",
                evidence_type=IdentityEvidenceType.EXACT_EMAIL,
                strength=0.9, explanation="  ", observed_at=AS_OF,
            )

    def test_candidate_score_bounded(self):
        with pytest.raises(ValueError, match="within"):
            EntityResolutionCandidate(
                external_identity_id="x", candidate_entity_id="c",
                score=1.2, status=EntityResolutionStatus.CANDIDATE,
                explanation="overflow",
            )

    def test_person_policy_stricter_than_service(self):
        policy = EntityResolutionPolicy()
        assert policy.person_automatic_resolution_threshold > (
            policy.automatic_resolution_threshold
        )
        assert policy.require_strong_person_identifier is True
        assert policy.allow_display_name_only_resolution is False

    def test_merge_proposal_preserves_evidence(self):
        proposal = propose_entity_merge(
            source=entity("svc-a", CanonicalEntityType.SERVICE, "Auth Graph"),
            target=entity("svc-b", CanonicalEntityType.SERVICE,
                          "Account Graph"),
            reason="Same production service under two names",
            evidence_ids=["ev-1", "ev-2"],
            confidence=0.93,
            created_at=AS_OF,
        )
        assert proposal.evidence_ids == ["ev-1", "ev-2"]
        assert proposal.status == EntityMergeStatus.PROPOSED

    def test_no_secret_fields_in_identity_models(self):
        for model in (CanonicalEntity, ExternalEntityIdentity):
            field_names = set(model.model_fields)
            for forbidden in ("token", "secret", "password", "credential"):
                assert not any(forbidden in n for n in field_names)


class TestNormalization:
    def test_separator_variants_normalize_together(self):
        assert normalize_entity_alias("Account-Graph") == (
            normalize_entity_alias("account graph")
        )
        assert normalize_display_name("P.  Rao") == "p rao"

    def test_distinct_terms_stay_distinct(self):
        assert normalize_entity_alias("Auth") != (
            normalize_entity_alias("Authorization Service")
        )

    def test_handle_and_email_normalization(self):
        assert normalize_handle("@Priya") == "priya"
        assert normalize_email("  Priya.Rao@Company.COM ") == (
            "priya.rao@company.com"
        )

    def test_external_identity_id_deterministic(self):
        a = build_external_identity_id(
            source_system="slack",
            identity_type=ExternalIdentityType.USER_ID,
            external_id="U129",
        )
        b = build_external_identity_id(
            source_system="Slack",
            identity_type=ExternalIdentityType.USER_ID,
            external_id="u129",
        )
        c = build_external_identity_id(
            source_system="slack",
            identity_type=ExternalIdentityType.USER_ID,
            external_id="U130",
        )
        assert a == b  # same source identity → same ID
        assert a != c


class TestSourceGuardrails:
    def test_identity_layer_grants_and_sends_nothing(self):
        import inspect
        import lltpm.identity.evidence as evidence_mod
        import lltpm.identity.graph as graph_mod
        import lltpm.identity.merge as merge_mod
        import lltpm.identity.models as models_mod
        import lltpm.identity.registry as registry_mod
        import lltpm.identity.resolution as resolution_mod

        forbidden = (
            "lltpm.application",
            "apply_calibration",
            "issue_approval",
            "approve_calibration",
            "employee_score",
            "performance_score",
            "send_message",
            "create_ticket",
        )
        for mod in (models_mod, evidence_mod, resolution_mod, graph_mod,
                    registry_mod, merge_mod):
            source = inspect.getsource(mod)
            for pattern in forbidden:
                assert pattern not in source, (
                    f"{mod.__name__} must not contain '{pattern}' — the "
                    f"identity layer connects evidence; it grants, sends, "
                    f"and scores nothing."
                )
