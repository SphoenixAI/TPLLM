"""Authority policy, risk classification, and
autonomous eligibility gating."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from lltpm.coordination import (
    CommitmentType,
    OrganizationalAutonomyBudget,
    OrganizationalAutonomyState,
    OrganizationalDecisionAuthorityPolicy,
    OrganizationalDecisionMode,
    OrganizationalDecisionRisk,
    WorkstreamCommitment,
    can_agent_finalize_decision,
    classify_organizational_decision_risk,
    is_never_autonomous_category,
    risk_at_most,
    select_agent_recommendation,
)
from tests._coordination_fixtures import AS_OF, PROJECT, WS, request
from tests._recommendation_fixtures import (
    POLICY,
    assessment,
    capsule,
    criteria,
)


def _recommendation(assessments=None, unknowns=(), caps=None):
    return select_agent_recommendation(
        capsule=caps or capsule(),
        assessments=assessments if assessments is not None else [
            assessment("opt-a", {f"crit-{i}": 0.98 for i in range(3)},
                       evidence_confidence=0.98),
            assessment("opt-b", {f"crit-{i}": 0.30 for i in range(3)}),
        ],
        criteria=criteria(), policy=POLICY,
        critical_unknowns=list(unknowns), now=AS_OF,
    )


def _policy(**overrides):
    defaults = dict(
        id="policy-startup-v4", organization_id="startup",
        mode=OrganizationalDecisionMode.BOUNDED_AUTONOMOUS,
        autonomous_allowed_decision_types=["internal_technical"],
        autonomous_allowed_project_ids=[PROJECT],
        autonomous_allowed_workstream_ids=[WS],
        maximum_autonomous_risk=OrganizationalDecisionRisk.MODERATE,
        minimum_autonomous_confidence=0.90,
        minimum_option_margin=0.15,
        created_at=AS_OF - timedelta(days=1),
        expires_at=AS_OF + timedelta(days=30),
    )
    defaults.update(overrides)
    return OrganizationalDecisionAuthorityPolicy(**defaults)


def _assess(policy=None, risk=OrganizationalDecisionRisk.LOW, **overrides):
    defaults = dict(
        recommendation=_recommendation(),
        capsule=capsule(),
        policy=policy or _policy(),
        decision_risk=risk,
        recent_autonomous_decisions=[],
        now=AS_OF,
        decision_type="internal_technical",
    )
    defaults.update(overrides)
    return can_agent_finalize_decision(**defaults)


class TestPolicyDefaults:
    def test_default_mode_is_human_validated(self):
        policy = OrganizationalDecisionAuthorityPolicy(
            organization_id="acme", created_at=AS_OF,
        )
        assert policy.mode == OrganizationalDecisionMode.HUMAN_VALIDATED
        assert policy.require_reversible_decision is True
        assert policy.human_review_required_after_execution is True
        assert policy.maximum_autonomous_risk == (
            OrganizationalDecisionRisk.LOW
        )

    def test_policy_cannot_authorize_critical_autonomy(self):
        with pytest.raises(ValueError, match="never authorize"):
            _policy(
                maximum_autonomous_risk=OrganizationalDecisionRisk.CRITICAL,
            )

    def test_autonomy_is_never_inferred_from_headcount(self):
        import inspect
        import lltpm.coordination.authority_policy as mod

        source = inspect.getsource(mod).lower()
        for pattern in ("employee_count", "headcount ==", "team_size",
                        "founder_only"):
            assert pattern not in source


class TestModeGating:
    def test_advisory_only_cannot_finalize(self):
        result = _assess(
            policy=_policy(mode=OrganizationalDecisionMode.ADVISORY_ONLY),
        )
        assert result.autonomous_eligible is False
        assert any("advisory_only" in b for b in result.blockers)

    def test_human_validated_cannot_finalize(self):
        result = _assess(
            policy=_policy(mode=OrganizationalDecisionMode.HUMAN_VALIDATED),
        )
        assert result.autonomous_eligible is False

    def test_explicit_bounded_autonomous_is_eligible(self):
        result = _assess()
        assert result.autonomous_eligible is True, result.blockers
        assert result.policy_id == "policy-startup-v4"


class TestScopeGating:
    def test_expired_policy_blocks(self):
        result = _assess(
            policy=_policy(expires_at=AS_OF - timedelta(hours=1)),
        )
        assert result.autonomous_eligible is False
        assert any("expired" in b for b in result.blockers)

    def test_unsupported_decision_type_blocks(self):
        result = _assess(decision_type="customer_commitment")
        assert result.autonomous_eligible is False
        assert any("not in the policy" in b for b in result.blockers)

    def test_out_of_scope_project_blocks(self):
        result = _assess(
            policy=_policy(autonomous_allowed_project_ids=["other-project"]),
        )
        assert result.autonomous_eligible is False
        assert any("outside the policy scope" in b for b in result.blockers)

    def test_out_of_scope_workstream_blocks(self):
        result = _assess(
            policy=_policy(autonomous_allowed_workstream_ids=["ws-other"]),
        )
        assert result.autonomous_eligible is False

    def test_risk_ceiling_enforced(self):
        result = _assess(risk=OrganizationalDecisionRisk.HIGH)
        assert result.autonomous_eligible is False
        assert any("exceeds the policy ceiling" in b for b in result.blockers)


class TestRecommendationQualityGating:
    def test_low_confidence_blocks(self):
        weak = _recommendation([
            assessment("opt-a", {f"crit-{i}": 0.80 for i in range(3)},
                       evidence_confidence=0.90),
        ])
        result = _assess(recommendation=weak)
        assert result.autonomous_eligible is False
        assert any("below the autonomous threshold" in b
                   for b in result.blockers)

    def test_critical_unknown_blocks(self):
        # The recommendation itself abstains, which also blocks autonomy.
        blocked = _recommendation(unknowns=["Does mobile coexist safely?"])
        result = _assess(recommendation=blocked)
        assert result.autonomous_eligible is False
        assert any("nothing to finalize" in b for b in result.blockers)

    def test_abstained_recommendation_blocks(self):
        contested = _recommendation([
            assessment("opt-a", {f"crit-{i}": 0.81 for i in range(3)}),
            assessment("opt-b", {f"crit-{i}": 0.79 for i in range(3)}),
        ])
        result = _assess(recommendation=contested)
        assert result.autonomous_eligible is False

    def test_non_reversible_blocked_when_required(self):
        irreversible = _recommendation([
            assessment("opt-a", {f"crit-{i}": 0.98 for i in range(3)},
                       evidence_confidence=0.98, reversible=False),
            assessment("opt-b", {f"crit-{i}": 0.30 for i in range(3)}),
        ])
        result = _assess(recommendation=irreversible)
        assert result.autonomous_eligible is False
        assert any("reversible" in b for b in result.blockers)


class TestBudgetAndKillSwitch:
    def test_exhausted_budget_blocks(self):
        budget = OrganizationalAutonomyBudget(
            policy_id="policy-startup-v4", max_decisions_per_day=2,
            current_day=AS_OF.date(), decisions_used=2,
        )
        result = _assess(budget=budget)
        assert result.autonomous_eligible is False
        assert any("budget exhausted" in b for b in result.blockers)

    def test_kill_switch_always_wins(self):
        state = OrganizationalAutonomyState(
            enabled=True, kill_switch_engaged=True,
            reason="incident in progress", updated_at=AS_OF,
        )
        result = _assess(autonomy_state=state)
        assert result.autonomous_eligible is False
        # Kill switch short-circuits: it is the only blocker reported.
        assert len(result.blockers) == 1
        assert "kill switch" in result.blockers[0].lower()
        assert "incident in progress" in result.blockers[0]

    def test_disabled_autonomy_blocks(self):
        state = OrganizationalAutonomyState(
            enabled=False, updated_at=AS_OF,
        )
        assert _assess(autonomy_state=state).autonomous_eligible is False


def _capsule_about(question: str):
    """A capsule whose request genuinely matches the question, so the
    classifier reads consistent text rather than a half-overridden
    fixture."""
    return capsule(
        request=request(
            "req-risk", requested_action=question,
            why_needed="Needed to proceed.",
        ),
    ).model_copy(update={
        "decision_question": question,
        "impact_if_decided": "Needed to proceed.",
        "impact_if_delayed": "Work waits.",
    })


class TestRiskClassification:
    def _classify(self, question, commitments=(), kpis=(), assessments=None):
        caps = _capsule_about(question)
        rec = _recommendation(assessments=assessments, caps=caps)
        return classify_organizational_decision_risk(
            capsule=caps, recommendation=rec,
            commitments=list(commitments), affected_kpis=list(kpis),
        )

    def test_reversible_internal_choice_is_low(self):
        assert self._classify(
            "Choose internal event naming convention",
        ) == OrganizationalDecisionRisk.LOW

    def test_architecture_choice_is_moderate(self):
        assert self._classify(
            "Choose internal event schema architecture",
        ) == OrganizationalDecisionRisk.MODERATE

    def test_launch_scope_is_high(self):
        assert self._classify(
            "Change the launch scope for phase one",
        ) == OrganizationalDecisionRisk.HIGH

    def test_customer_commitment_is_high(self):
        commitment = WorkstreamCommitment(
            id="cm-1", workstream_id=WS,
            commitment_type=CommitmentType.CUSTOMER,
            description="Customer delivery", status="open",
            evidence_ids=["ev-1"],
        )
        assert self._classify(
            "Adjust internal sequencing", commitments=[commitment],
        ) == OrganizationalDecisionRisk.HIGH

    def test_irreversible_option_is_high(self):
        assert self._classify(
            "Pick a rollout order",
            assessments=[
                assessment("opt-a", {f"crit-{i}": 0.98 for i in range(3)},
                           evidence_confidence=0.98, reversible=False),
                assessment("opt-b", {f"crit-{i}": 0.30 for i in range(3)}),
            ],
        ) == OrganizationalDecisionRisk.HIGH

    def test_security_exception_is_critical(self):
        assert self._classify(
            "Approve a security exception for the payments path",
        ) == OrganizationalDecisionRisk.CRITICAL

    def test_personnel_decision_is_critical(self):
        assert self._classify(
            "Proceed with termination of the contractor engagement",
        ) == OrganizationalDecisionRisk.CRITICAL

    def test_risk_ordering(self):
        assert risk_at_most(
            OrganizationalDecisionRisk.LOW,
            OrganizationalDecisionRisk.MODERATE,
        )
        assert not risk_at_most(
            OrganizationalDecisionRisk.HIGH,
            OrganizationalDecisionRisk.MODERATE,
        )


class TestNeverAutonomousCategories:
    @pytest.mark.parametrize(
        "question",
        [
            "Approve compensation adjustment for the team",
            "Proceed with termination of the vendor contract",
            "Grant access credentials to the production database",
            "Approve a regulatory waiver for the data residency rule",
            "Authorize a wire transfer to the partner",
        ],
    )
    def test_banned_categories_detected(self, question):
        caps = capsule().model_copy(update={"decision_question": question})
        rec = _recommendation(caps=caps)
        assert is_never_autonomous_category(
            capsule=caps, recommendation=rec,
        ) is not None

    def test_banned_category_blocks_even_at_high_confidence(self):
        # Category ban ignores confidence entirely.
        caps = _capsule_about(
            "Authorize a wire transfer to the partner account",
        )
        rec = _recommendation(caps=caps)
        assert rec.confidence >= 0.90
        result = _assess(
            recommendation=rec, capsule=caps,
            risk=OrganizationalDecisionRisk.LOW,  # even if misclassified
        )
        assert result.autonomous_eligible is False
        assert any("never be finalized" in b for b in result.blockers)

    def test_large_customer_commitment_blocked_by_risk(self):
        # Spec 22.33: a $500k customer commitment is refused because its
        # risk exceeds the bounded ceiling, even at 0.97 confidence.
        caps = _capsule_about("Sign a $500,000 customer commitment")
        rec = _recommendation(caps=caps)
        assert rec.confidence >= 0.90
        risk = classify_organizational_decision_risk(
            capsule=caps, recommendation=rec, commitments=[],
            affected_kpis=[],
        )
        assert risk == OrganizationalDecisionRisk.HIGH
        result = _assess(recommendation=rec, capsule=caps, risk=risk)
        assert result.autonomous_eligible is False
        assert any("exceeds the policy ceiling" in b for b in result.blockers)

    def test_ordinary_decision_is_not_banned(self):
        caps = _capsule_about("Choose the internal event schema")
        assert is_never_autonomous_category(
            capsule=caps, recommendation=_recommendation(caps=caps),
        ) is None
