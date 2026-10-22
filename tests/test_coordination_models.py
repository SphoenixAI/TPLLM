"""Coordination models and their invariants."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tpllm.coordination import (
    ActorRelationType,
    CapacityConstraintType,
    CommitmentType,
    OrganizationalDecisionStatus,
    TeamCapacityConstraint,
    WorkstreamCommitment,
    WorkstreamKeyDate,
)
from tests._coordination_fixtures import AS_OF, PROJECT, WS, actor, decision, objective, request


class TestConclusiveInvariant:
    def test_conclusive_requires_conclusion(self):
        with pytest.raises(ValueError, match="conclusion"):
            decision("d1", conclusion=None)

    def test_conclusive_requires_authority(self):
        with pytest.raises(ValueError, match="authority"):
            decision("d1", decision_authority=None)

    def test_conclusive_requires_decided_at(self):
        with pytest.raises(ValueError, match="decided_at"):
            decision("d1", decided_at=None)

    def test_conclusive_requires_evidence(self):
        with pytest.raises(ValueError, match="evidence"):
            decision("d1", evidence_ids=[])

    def test_tentative_needs_none_of_it(self):
        record = decision(
            "d1", status=OrganizationalDecisionStatus.TENTATIVE,
        )
        assert record.conclusion is None


class TestObjectiveChain:
    def test_objective_links_project_and_company_goal(self):
        obj = objective(parent_objective_id="obj-project-atlas")
        assert obj.project_id == PROJECT
        assert obj.workstream_id == WS
        assert obj.parent_objective_id == "obj-project-atlas"
        assert "goal-platform-consolidation" in obj.company_goal_ids

    def test_product_value_separate_from_status(self):
        obj = objective()
        # Product value lives on the objective, independent of any health
        # or status field — it survives status changes.
        assert obj.product_value == "Single authorization layer for all clients"
        assert not hasattr(obj, "health")
        assert not hasattr(obj, "status")


class TestKeyDatesAndCommitments:
    def test_key_date_preserves_evidence(self):
        key_date = WorkstreamKeyDate(
            id="kd-1", workstream_id=WS, name="integration freeze",
            date=AS_OF + timedelta(days=14), date_type="freeze",
            hard_commitment=True, source_system="calendar",
            evidence_ids=["ev-freeze-doc"], confidence=0.8,
        )
        assert key_date.evidence_ids == ["ev-freeze-doc"]
        assert key_date.hard_commitment is True

    def test_commitment_preserves_type_and_counterparty(self):
        commitment = WorkstreamCommitment(
            id="cm-1", workstream_id=WS,
            commitment_type=CommitmentType.PARTNER,
            description="Deliver partner demo of graph authorization",
            committed_to_actor_or_org="AcmePay",
            committed_by_actor_id="dana",
            target_date=AS_OF + timedelta(days=21),
            status="open",
            evidence_ids=["ev-partner-email"],
        )
        assert commitment.commitment_type == CommitmentType.PARTNER
        assert commitment.committed_to_actor_or_org == "AcmePay"
        assert commitment.evidence_ids


class TestActorsAndRequests:
    def test_action_request_preserves_requester_role(self):
        req = request()
        assert req.requested_by.role == "Payments Integration Lead"
        assert req.requested_by.team_id == "payments"

    def test_expertise_and_authority_are_separate_relations(self):
        expert = actor(
            "sam", role="Staff Engineer", team="identity",
            relations=[ActorRelationType.HAS_EXPERTISE],
        )
        authority = actor(
            "priya",
            relations=[ActorRelationType.MAY_APPROVE,
                       ActorRelationType.OWNS_SYSTEM],
        )
        assert ActorRelationType.HAS_EXPERTISE in expert.relation_types
        assert ActorRelationType.MAY_APPROVE not in expert.relation_types
        assert ActorRelationType.MAY_APPROVE in authority.relation_types
        # Eight distinct relation types exist — never flattened.
        assert len(ActorRelationType) == 8


class TestCapacityConstraint:
    def test_constraint_is_team_level(self):
        constraint = TeamCapacityConstraint(
            id="cap-1", team_id="platform-infrastructure",
            constraint_type=CapacityConstraintType.APPROVAL_QUEUE,
            description=(
                "Owns three other launch-critical approval queues; this "
                "request is behind two higher-severity dependencies."
            ),
            severity="high",
            effective_from=AS_OF - timedelta(days=6),
            evidence_ids=["ev-queue-board"],
        )
        assert constraint.team_id == "platform-infrastructure"
        # Structural condition with evidence — no person field, no score.
        field_names = set(TeamCapacityConstraint.model_fields)
        assert "actor_id" not in field_names
        assert "person_id" not in field_names
        assert not any("score" in name for name in field_names)
