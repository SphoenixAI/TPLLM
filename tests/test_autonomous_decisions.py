"""Autonomous finalization, agent authority
identity, snapshots, review, and reopen/supersede."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tpllm.coordination import (
    AGENT_AUTHORITY_ACTOR_ID,
    DECISION_ORIGIN_AUTONOMOUS,
    AgentAuthorityRef,
    OrganizationalAutonomyBudget,
    OrganizationalDecisionAuthorityPolicy,
    OrganizationalDecisionMode,
    OrganizationalDecisionRisk,
    OrganizationalDecisionStatus,
    can_agent_finalize_decision,
    capture_decision_snapshot,
    consume_autonomy_budget,
    finalize_agent_decision,
    get_latest_conclusive_decision,
    is_agent_decided,
    reopen_decision,
    schedule_autonomous_review,
    select_agent_recommendation,
    supersede_decision,
)
from tests._coordination_fixtures import AS_OF, PROJECT, WS, actor, decision
from tests._recommendation_fixtures import (
    POLICY,
    assessment,
    capsule,
    criteria,
)


def _recommendation(caps=None, assessments=None):
    return select_agent_recommendation(
        capsule=caps or capsule(),
        assessments=assessments if assessments is not None else [
            assessment("opt-a", {f"crit-{i}": 0.98 for i in range(3)},
                       evidence_confidence=0.98),
            assessment("opt-b", {f"crit-{i}": 0.30 for i in range(3)}),
        ],
        criteria=criteria(), policy=POLICY, critical_unknowns=[], now=AS_OF,
    )


def _policy(**overrides):
    defaults = dict(
        id="policy-startup-v4", organization_id="startup",
        mode=OrganizationalDecisionMode.BOUNDED_AUTONOMOUS,
        autonomous_allowed_decision_types=["internal_technical"],
        autonomous_allowed_project_ids=[PROJECT],
        autonomous_allowed_workstream_ids=[WS],
        maximum_autonomous_risk=OrganizationalDecisionRisk.MODERATE,
        created_at=AS_OF - timedelta(days=1),
        expires_at=AS_OF + timedelta(days=30),
    )
    defaults.update(overrides)
    return OrganizationalDecisionAuthorityPolicy(**defaults)


def _eligible(policy=None, recommendation=None, caps=None):
    policy = policy or _policy()
    recommendation = recommendation or _recommendation(caps=caps)
    return policy, recommendation, can_agent_finalize_decision(
        recommendation=recommendation, capsule=caps or capsule(),
        policy=policy, decision_risk=OrganizationalDecisionRisk.LOW,
        recent_autonomous_decisions=[], now=AS_OF,
        decision_type="internal_technical",
    )


def _finalize(policy=None, recommendation=None, caps=None):
    caps = caps or capsule()
    policy, recommendation, assessment_result = _eligible(
        policy=policy, recommendation=recommendation, caps=caps,
    )
    return finalize_agent_decision(
        recommendation=recommendation, capsule=caps,
        authority_assessment=assessment_result, policy=policy, now=AS_OF,
    )


class TestAgentAuthorityIdentity:
    def test_agent_authority_is_not_a_human(self):
        ref = AgentAuthorityRef(
            authority_policy_id="policy-startup-v4",
            authority_scope="internal_technical",
        )
        actor_ref = ref.as_actor_reference()
        assert actor_ref.actor_id == AGENT_AUTHORITY_ACTOR_ID
        assert actor_ref.display_name == "TPLLM"
        assert "delegated policy" in actor_ref.role
        # No human team is impersonated.
        assert actor_ref.team_id is None

    def test_agent_decisions_are_distinguishable(self):
        record, _, _ = _finalize()
        assert is_agent_decided(record) is True
        human_record = decision("dec-human", decided_days_ago=1)
        assert is_agent_decided(human_record) is False


class TestAutonomousFinalization:
    def test_eligible_decision_becomes_conclusive(self):
        record, snapshot, execution = _finalize()
        assert record.status == OrganizationalDecisionStatus.CONCLUSIVE
        assert record.conclusion == "opt-a"
        assert record.decided_at == AS_OF
        assert record.evidence_ids
        assert DECISION_ORIGIN_AUTONOMOUS in record.consequence_summary
        assert execution.selected_option_id == "opt-a"
        assert execution.created_decision_id == record.id

    def test_ineligible_decision_is_refused(self):
        policy = _policy(mode=OrganizationalDecisionMode.HUMAN_VALIDATED)
        recommendation = _recommendation()
        caps = capsule()
        assessment_result = can_agent_finalize_decision(
            recommendation=recommendation, capsule=caps, policy=policy,
            decision_risk=OrganizationalDecisionRisk.LOW,
            recent_autonomous_decisions=[], now=AS_OF,
            decision_type="internal_technical",
        )
        with pytest.raises(ValueError, match="eligible authority"):
            finalize_agent_decision(
                recommendation=recommendation, capsule=caps,
                authority_assessment=assessment_result, policy=policy,
                now=AS_OF,
            )

    def test_snapshot_captured_before_decision(self):
        record, snapshot, execution = _finalize()
        assert snapshot.captured_at == AS_OF
        assert snapshot.relevant_evidence_ids
        # The pre-decision state is preserved in full.
        assert snapshot.recommendation.recommended_option_id == "opt-a"
        assert snapshot.capsule.decision_question
        assert snapshot.authority_assessment.autonomous_eligible is True
        assert execution.snapshot_id == snapshot.id

    def test_review_scheduled_when_policy_requires(self):
        record, _, execution = _finalize()
        assert execution.review_due_at == AS_OF + timedelta(hours=24)
        review = schedule_autonomous_review(execution=execution)
        assert review is not None
        assert review.decision_id == record.id
        assert review.reviewed_by is None  # pending

    def test_no_review_when_policy_does_not_require(self):
        _, _, execution = _finalize(
            policy=_policy(human_review_required_after_execution=False),
        )
        assert execution.review_due_at is None
        assert schedule_autonomous_review(execution=execution) is None

    def test_finalization_does_not_mutate_recommendation_or_capsule(self):
        caps = capsule()
        recommendation = _recommendation(caps=caps)
        before_caps = caps.model_dump()
        before_rec = recommendation.model_dump()
        _finalize(recommendation=recommendation, caps=caps)
        assert caps.model_dump() == before_caps
        assert recommendation.model_dump() == before_rec


class TestAgentCannotExpandAuthority:
    def test_finalization_never_writes_policy(self):
        policy = _policy()
        before = policy.model_dump()
        _finalize(policy=policy)
        assert policy.model_dump() == before

    def test_no_policy_mutation_in_source(self):
        import inspect
        import tpllm.coordination.autonomous_decisions as mod

        source = inspect.getsource(mod)
        # The module must never write policy scope, thresholds, or budget.
        for pattern in (
            "policy.maximum_autonomous_risk =",
            "policy.minimum_autonomous_confidence =",
            "policy.autonomous_allowed_decision_types =",
            "policy.autonomous_allowed_project_ids =",
            "policy.maximum_autonomous_decisions_per_day =",
            "policy.mode =",
        ):
            assert pattern not in source
        # And it must not copy-with-update a policy either.
        assert "policy.model_copy" not in source


class TestBudget:
    def test_budget_consumption(self):
        budget = OrganizationalAutonomyBudget(
            policy_id="p", max_decisions_per_day=2,
            current_day=AS_OF.date(), decisions_used=0,
        )
        after = consume_autonomy_budget(budget=budget, on_day=AS_OF)
        assert after.decisions_used == 1
        assert budget.decisions_used == 0  # copy-on-write
        after2 = consume_autonomy_budget(budget=after, on_day=AS_OF)
        assert after2.exhausted is True

    def test_budget_resets_next_day(self):
        budget = OrganizationalAutonomyBudget(
            policy_id="p", max_decisions_per_day=2,
            current_day=AS_OF.date(), decisions_used=2,
        )
        tomorrow = AS_OF + timedelta(days=1)
        after = consume_autonomy_budget(budget=budget, on_day=tomorrow)
        assert after.current_day == tomorrow.date()
        assert after.decisions_used == 1
        assert after.exhausted is False


class TestReopenAndSupersede:
    def test_human_can_reopen_agent_decision(self):
        record, _, _ = _finalize()
        reopened = reopen_decision(
            decision=record, reason="New coexistence evidence",
            evidence_ids=["ev-new"],
        )
        assert reopened.status.value == "reopened"
        assert "New coexistence evidence" in reopened.consequence_summary
        # An agent decision is no harder to overturn than a human one.
        assert get_latest_conclusive_decision(
            decisions=[reopened], workstream_id=WS, as_of=AS_OF,
        ) is None

    def test_human_can_supersede_agent_decision(self):
        record, _, _ = _finalize()
        human_decision = decision("dec-human", decided_days_ago=0)
        old, new = supersede_decision(old=record, new=human_decision)
        assert old.status.value == "superseded"
        assert old.superseded_by_decision_id == "dec-human"
        latest = get_latest_conclusive_decision(
            decisions=[old, new], workstream_id=WS,
            as_of=AS_OF + timedelta(minutes=1),
        )
        assert latest.id == "dec-human"
        assert is_agent_decided(latest) is False
