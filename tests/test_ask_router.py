"""Tests for Commit 15 — ask routing and the no-pointless-ping guardrail."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from lltpm.coordination import (
    ActionRequestType,
    ActorRelationType,
    CapacityConstraintType,
    ExpertiseRequirement,
    RoutingCandidate,
    TeamCapacityConstraint,
    answer_already_exists,
    build_action_routing_plan,
    identify_routing_candidates,
)
from lltpm.intelligence import (
    UnderstandingClaim,
    UnderstandingClaimStatus,
    UnderstandingClaimType,
)
from tests._coordination_fixtures import AS_OF, PROJECT, actor, decision, request, understanding


def _relation(actor_ref, topics=None):
    return SimpleNamespace(
        actor=actor_ref, expertise_topics=topics or [],
    )


def _candidates(**overrides):
    approver = actor(
        "priya", relations=[ActorRelationType.MAY_APPROVE,
                            ActorRelationType.OWNS_SYSTEM],
    )
    expert = actor(
        "sam", role="Staff Engineer", team="identity",
        relations=[ActorRelationType.HAS_EXPERTISE],
    )
    historian = actor(
        "lee", role="Former TL", team="platform-architecture",
        relations=[ActorRelationType.HAS_HISTORICAL_CONTEXT],
    )
    defaults = dict(
        request=request("req-1"),
        actor_relations=[
            _relation(expert, topics=["authorization contracts"]),
            _relation(approver),
            _relation(historian),
        ],
        expertise_requirements=[
            ExpertiseRequirement(
                topic="authorization contracts",
                why_needed="contract compatibility judgment",
                required_depth="deep",
            )
        ],
        capacity_constraints=[],
    )
    defaults.update(overrides)
    return identify_routing_candidates(**defaults)


class TestRoutingCandidates:
    def test_authority_distinct_from_expertise(self):
        candidates = _candidates()
        by_id = {c.actor.actor_id: c for c in candidates}
        assert by_id["priya"].authority_match is True
        assert by_id["priya"].expertise_match is False
        assert by_id["sam"].authority_match is False
        assert by_id["sam"].expertise_match is True
        # Authority sorts first.
        assert candidates[0].actor.actor_id == "priya"

    def test_routing_deterministic(self):
        first = [c.model_dump() for c in _candidates()]
        second = [c.model_dump() for c in _candidates()]
        assert first == second

    def test_no_numeric_person_score(self):
        field_names = set(RoutingCandidate.model_fields)
        assert not any("score" in name for name in field_names)
        assert not any("rank" in name for name in field_names)
        # Matches are booleans plus a textual reason, never a rating.
        candidate = _candidates()[0]
        assert isinstance(candidate.authority_match, bool)
        assert isinstance(candidate.routing_reason, str)


class TestRoutingPlan:
    def test_plan_routes_to_authority_with_capsule(self):
        plan = build_action_routing_plan(
            request=request("req-1"),
            candidates=_candidates(),
            decision_capsule_id="capsule-req-1",
        )
        assert plan.primary_recipient.actor_id == "priya"
        assert plan.missing_authority is False
        assert plan.recommended_delivery_mode == "DECISION_CAPSULE"

    def test_missing_authority_emits_explicit_gap(self):
        expert_only = _candidates(
            actor_relations=[
                _relation(actor(
                    "sam", role="Staff Engineer", team="identity",
                    relations=[ActorRelationType.HAS_EXPERTISE],
                ), topics=["authorization contracts"]),
            ],
        )
        plan = build_action_routing_plan(
            request=request("req-1"), candidates=expert_only,
        )
        assert plan.missing_authority is True
        assert plan.primary_recipient is None
        assert plan.backup_recipient is not None  # expertise as fallback
        assert plan.recommended_delivery_mode == "MEETING_DECISION"
        assert "gap" in plan.routing_reason

    def test_capacity_warning_does_not_change_authority(self):
        constraint = TeamCapacityConstraint(
            id="cap-1", team_id="platform-architecture",
            constraint_type=CapacityConstraintType.APPROVAL_QUEUE,
            description="Three launch-critical approval queues",
            severity="high",
            effective_from=AS_OF - timedelta(days=6),
            evidence_ids=["ev-queue"],
        )
        candidates = _candidates(capacity_constraints=[constraint])
        plan = build_action_routing_plan(
            request=request("req-1"), candidates=candidates,
        )
        # Still routed to the authority; the constraint only warns.
        assert plan.primary_recipient.actor_id == "priya"
        assert plan.capacity_warning is not None
        assert "cap-1" in plan.capacity_warning
        assert "authority itself is unchanged" in plan.capacity_warning

    def test_expertise_request_mode(self):
        plan = build_action_routing_plan(
            request=request(
                "req-x", request_type=ActionRequestType.EXPERTISE,
                required_authority_relation=None,
                required_expertise_topics=["authorization contracts"],
            ),
            candidates=_candidates(
                request=request(
                    "req-x", request_type=ActionRequestType.EXPERTISE,
                    required_authority_relation=None,
                    required_expertise_topics=["authorization contracts"],
                ),
            ),
        )
        assert plan.recommended_delivery_mode == "EXPERTISE_REQUEST"


class TestNoPointlessPing:
    def test_existing_conclusive_answer_suppresses_ping(self):
        answered = decision(
            "dec-1",
            topic="Contract v3 enterprise approval",
            question="Approve contract v3 for enterprise accounts?",
            conclusion="Contract v3 approved for enterprise accounts",
        )
        assert answer_already_exists(
            request=request("req-1"),
            decisions=[answered],
            understanding=understanding(),
            evidence=[],
        ) is True

    def test_unanswered_request_allows_ask(self):
        unrelated = decision(
            "dec-2",
            topic="Search index rollout",
            question="Roll out the new search index cluster?",
            conclusion="Search index rollout deferred to Q4",
        )
        assert answer_already_exists(
            request=request("req-1"),
            decisions=[unrelated],
            understanding=understanding(),
            evidence=[],
        ) is False

    def test_supported_claim_counts_as_answer(self):
        snapshot = understanding().model_copy(
            update={
                "claims": [
                    UnderstandingClaim(
                        id="c-1", project_id=PROJECT,
                        claim_type=UnderstandingClaimType.SCOPE,
                        statement=(
                            "Contract v3 approved for enterprise accounts "
                            "integrations"
                        ),
                        status=UnderstandingClaimStatus.SUPPORTED,
                        confidence=0.7, evidence_ids=["ev-1"],
                    )
                ]
            }
        )
        assert answer_already_exists(
            request=request("req-1"),
            decisions=[],
            understanding=snapshot,
            evidence=[],
        ) is True
