"""Tests for Commit 19 — explicit, auditable, reversible entity merges."""

from __future__ import annotations

import pytest

from lltpm.identity import (
    CanonicalEntityType,
    EntityMergeStatus,
    EntityResolutionEvidence,
    IdentityEvidenceType,
    apply_entity_merge,
    can_auto_merge_entities,
    propose_entity_merge,
    reject_merge_proposal,
    reverse_entity_merge,
)
from tests._identity_fixtures import (
    AS_OF,
    POLICY,
    entity,
    identity,
    registry_with,
)
from lltpm.identity import ExternalIdentityType


def _evidence(evidence_type, strength, positive=True, eid="e1"):
    return EntityResolutionEvidence(
        id=eid,
        external_identity_id="xid-1",
        candidate_entity_id="target",
        evidence_type=evidence_type,
        strength=strength,
        positive=positive,
        explanation=f"{evidence_type.value} evidence",
        observed_at=AS_OF,
    )


def _service_registry():
    source = entity("svc_auth_graph", CanonicalEntityType.SERVICE,
                    "Auth Graph", aliases=["AG"])
    target = entity("svc_account_graph", CanonicalEntityType.SERVICE,
                    "Account Graph", aliases=["AGS"])
    source_identity = identity(
        source_system="jira",
        identity_type=ExternalIdentityType.ISSUE_COMPONENT,
        external_id="AG",
        entity_type=CanonicalEntityType.SERVICE,
    )
    return registry_with(
        [source, target], [(source_identity, source.id)],
    ), source_identity


def _proposal(registry, confidence=0.95):
    return propose_entity_merge(
        source=registry.entities["svc_auth_graph"],
        target=registry.entities["svc_account_graph"],
        reason="Same production service under two names",
        evidence_ids=["ev-deploy-config"],
        confidence=confidence,
        created_at=AS_OF,
    )


class TestMergeGating:
    def test_person_merge_not_automatic_without_strong_evidence(self):
        source = entity("person_a", name="Alex Chen")
        target = entity("person_b", name="Alex Chen")
        weak = [
            _evidence(IdentityEvidenceType.DISPLAY_NAME_MATCH, 0.25),
            _evidence(IdentityEvidenceType.TEAM_MATCH, 0.15, eid="e2"),
            _evidence(IdentityEvidenceType.ROLE_MATCH, 0.10, eid="e3"),
        ]
        assert can_auto_merge_entities(
            source=source, target=target, evidence=weak, policy=POLICY,
        ) is False

    def test_person_merge_allowed_with_verified_identity(self):
        source = entity("person_a", name="P. Rao")
        target = entity("person_b", name="Priya Rao")
        strong = [
            _evidence(IdentityEvidenceType.EXACT_EMAIL, 0.98),
            _evidence(IdentityEvidenceType.HUMAN_CONFIRMATION, 1.0, eid="e2"),
        ]
        assert can_auto_merge_entities(
            source=source, target=target, evidence=strong, policy=POLICY,
        ) is True

    def test_service_merge_allowed_at_high_confidence(self):
        source = entity("svc_a", CanonicalEntityType.SERVICE, "Auth Graph")
        target = entity("svc_b", CanonicalEntityType.SERVICE, "Account Graph")
        evidence = [_evidence(IdentityEvidenceType.SERVICE_METADATA, 0.95)]
        assert can_auto_merge_entities(
            source=source, target=target, evidence=evidence, policy=POLICY,
        ) is True

    def test_type_mismatch_never_auto_merges(self):
        assert can_auto_merge_entities(
            source=entity("a", CanonicalEntityType.REPOSITORY, "account-graph"),
            target=entity("b", CanonicalEntityType.SERVICE, "Account Graph"),
            evidence=[_evidence(IdentityEvidenceType.HUMAN_CONFIRMATION, 1.0)],
            policy=POLICY,
        ) is False

    def test_type_mismatched_proposal_fails(self):
        with pytest.raises(ValueError, match="types must match"):
            propose_entity_merge(
                source=entity("a", CanonicalEntityType.REPOSITORY, "repo"),
                target=entity("b", CanonicalEntityType.SERVICE, "svc"),
                reason="x", evidence_ids=["e"], confidence=0.9,
                created_at=AS_OF,
            )


class TestMergeApplication:
    def test_merge_marks_source_inactive_not_deleted(self):
        registry, _ = _service_registry()
        merged, record = apply_entity_merge(
            registry=registry, proposal=_proposal(registry), applied_at=AS_OF,
        )
        source = merged.entities["svc_auth_graph"]
        assert source.active is False
        # Still present — history is never erased.
        assert "svc_auth_graph" in merged.entities
        assert record.status == EntityMergeStatus.APPLIED

    def test_merge_moves_external_identities(self):
        registry, source_identity = _service_registry()
        merged, record = apply_entity_merge(
            registry=registry, proposal=_proposal(registry), applied_at=AS_OF,
        )
        target = merged.entities["svc_account_graph"]
        assert source_identity.id in target.external_identity_ids
        assert merged.entities["svc_auth_graph"].external_identity_ids == []
        assert record.moved_external_identity_ids == [source_identity.id]

    def test_merge_preserves_aliases(self):
        registry, _ = _service_registry()
        merged, _ = apply_entity_merge(
            registry=registry, proposal=_proposal(registry), applied_at=AS_OF,
        )
        target = merged.entities["svc_account_graph"]
        # Target's own alias, plus the source name and its aliases.
        assert "AGS" in target.aliases
        assert "Auth Graph" in target.aliases
        assert "AG" in target.aliases

    def test_merge_produces_audit_record_with_snapshots(self):
        registry, _ = _service_registry()
        proposal = _proposal(registry)
        _, record = apply_entity_merge(
            registry=registry, proposal=proposal, applied_at=AS_OF,
        )
        assert record.previous_source_snapshot.active is True
        assert record.previous_target_snapshot.id == "svc_account_graph"
        assert record.evidence_ids == ["ev-deploy-config"]
        assert record.applied_at == AS_OF

    def test_rejected_proposal_cannot_apply(self):
        registry, _ = _service_registry()
        rejected = reject_merge_proposal(_proposal(registry))
        with pytest.raises(ValueError, match="rejected"):
            apply_entity_merge(registry=registry, proposal=rejected)
        # Registry unchanged.
        assert registry.entities["svc_auth_graph"].active is True

    def test_duplicate_merge_is_idempotent(self):
        registry, _ = _service_registry()
        proposal = _proposal(registry)
        merged, _ = apply_entity_merge(
            registry=registry, proposal=proposal, applied_at=AS_OF,
        )
        again, second_record = apply_entity_merge(
            registry=merged, proposal=proposal, applied_at=AS_OF,
        )
        assert again.entities == merged.entities
        assert second_record.moved_external_identity_ids == []

    def test_missing_entity_fails(self):
        registry, _ = _service_registry()
        proposal = _proposal(registry).model_copy(
            update={"source_entity_id": "does-not-exist"}
        )
        with pytest.raises(ValueError, match="exist"):
            apply_entity_merge(registry=registry, proposal=proposal)


class TestMergeReversal:
    def test_reversal_restores_pre_merge_state(self):
        registry, _ = _service_registry()
        before = {k: v.model_dump() for k, v in registry.entities.items()}

        merged, record = apply_entity_merge(
            registry=registry, proposal=_proposal(registry), applied_at=AS_OF,
        )
        assert merged.entities["svc_auth_graph"].active is False

        reversed_registry = reverse_entity_merge(
            registry=merged, merge_record=record, reversed_at=AS_OF,
        )
        after = {
            k: v.model_dump() for k, v in reversed_registry.entities.items()
        }
        assert after == before

    def test_original_registry_never_mutated(self):
        registry, _ = _service_registry()
        original = registry.model_dump()
        apply_entity_merge(
            registry=registry, proposal=_proposal(registry), applied_at=AS_OF,
        )
        assert registry.model_dump() == original
