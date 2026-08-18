"""Tests for Commit 30 — the recommendation/execution race.

    T0  the agent recommends Option B; policy says it may act
    T1  a critical KPI fails
    T2  autonomous execution finalizes the T0 recommendation

At T2 the recommendation answers a question about a world that no longer
exists. Everything here checks that it cannot execute anyway, and that
the refusals happen in the right order — the kill switch outranks
staleness, staleness outranks authority, and nothing ever widens what
Commit 22 already decided.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.coordination import (
    OrganizationalDecisionMode,
    OrganizationalDecisionRisk,
    OrganizationalDecisionStatus,
)
from lltpm.coordination.decision_timing import DecisionTimingAction
from lltpm.runtime import (
    IntelligenceArtifactType as A,
    RecommendationValidityStatus,
    RuntimeBlockReason,
    guarded_autonomous_finalization,
)
from tests._coordination_fixtures import AS_OF
from tests._recommendation_fixtures import capsule
from tests._runtime_fixtures import (
    FakeScenarioRecommendation,
    authority_policy,
    autonomy_state,
    decision_context,
    eligible_recommendation,
)

T0_CONTEXT = decision_context()
T1_CONTEXT = decision_context(
    critical_kpi_states={"kpi-activation-rate": "failing"},
)


def _attempt(**overrides):
    caps = overrides.pop("capsule", None) or capsule()
    defaults = dict(
        recommendation=overrides.pop(
            "recommendation", None,
        ) or eligible_recommendation(caps),
        capsule=caps,
        policy=overrides.pop("policy", None) or authority_policy(),
        decision_risk=OrganizationalDecisionRisk.LOW,
        current_decision_context=T0_CONTEXT,
        recommendation_context=T0_CONTEXT,
        recent_autonomous_decisions=[],
        now=AS_OF,
        autonomy_state=autonomy_state(),
        decision_type="internal_technical",
    )
    defaults.update(overrides)
    return guarded_autonomous_finalization(**defaults)


class TestTheHappyPath:
    def test_a_current_recommendation_executes(self):
        result, finalization = _attempt()
        assert result.executed is True
        assert result.block_reason is None
        assert finalization is not None

    def test_execution_produces_a_conclusive_decision(self):
        _, finalization = _attempt()
        record, _snapshot, execution = finalization
        assert record.status == OrganizationalDecisionStatus.CONCLUSIVE
        assert execution.recommendation_id

    def test_the_result_reports_the_decision_it_created(self):
        result, finalization = _attempt()
        record, _, execution = finalization
        assert result.decision_id == record.id
        assert result.execution_id == execution.id

    def test_the_explanation_says_authority_was_rechecked(self):
        result, _ = _attempt()
        assert "re-checked at the moment of execution" in result.explanation


class TestTheStaleRace:
    """The canonical Commit 30 safety case."""

    def test_a_material_event_between_t0_and_t2_blocks_execution(self):
        result, finalization = _attempt(current_decision_context=T1_CONTEXT)
        assert result.executed is False
        assert result.block_reason == RuntimeBlockReason.STALE_RECOMMENDATION
        assert finalization is None

    def test_the_block_names_both_fingerprints(self):
        result, _ = _attempt(current_decision_context=T1_CONTEXT)
        assert result.validity.recommendation_state_fingerprint in (
            result.explanation
        )
        assert result.validity.current_state_fingerprint in result.explanation
        assert result.validity.recommendation_state_fingerprint != (
            result.validity.current_state_fingerprint
        )

    def test_a_fresh_recommendation_is_demanded(self):
        result, _ = _attempt(current_decision_context=T1_CONTEXT)
        assert result.requires_fresh_recommendation is True
        assert "Recompute the recommendation" in result.explanation
        assert "re-check authority" in result.explanation

    def test_the_validity_assessment_is_attached(self):
        result, _ = _attempt(current_decision_context=T1_CONTEXT)
        assert result.validity.status == (
            RecommendationValidityStatus.INVALIDATED
        )
        assert "critical_kpi_states" in result.validity.changed_inputs

    def test_recomputing_against_the_new_state_allows_execution(self):
        # The fix for a stale recommendation is a fresh one, not a bypass.
        blocked, _ = _attempt(current_decision_context=T1_CONTEXT)
        assert blocked.blocked is True

        fresh, finalization = _attempt(
            current_decision_context=T1_CONTEXT,
            recommendation_context=T1_CONTEXT,
        )
        assert fresh.executed is True
        assert finalization is not None

    def test_an_already_resolved_decision_blocks_with_its_own_reason(self):
        result, _ = _attempt(
            current_decision_context=decision_context(decision_resolved=True),
        )
        assert result.block_reason == (
            RuntimeBlockReason.DECISION_ALREADY_RESOLVED
        )

    def test_irrelevant_new_evidence_does_not_block(self):
        result, _ = _attempt(
            current_decision_context=decision_context(
                supporting_evidence_ids=["ev-9", "ev-10"],
                noncritical_kpi_states={"kpi-page-views": "down"},
            ),
        )
        assert result.executed is True, (
            "a third confirmation of a known fact must not cost the "
            "organization a decision"
        )


class TestOrderOfRefusals:
    def test_the_kill_switch_short_circuits_before_staleness(self):
        result, _ = _attempt(
            current_decision_context=T1_CONTEXT,
            autonomy_state=autonomy_state(
                kill_switch_engaged=True, reason="incident response",
            ),
        )
        assert result.block_reason == RuntimeBlockReason.KILL_SWITCH_ENGAGED
        assert "incident response" in result.explanation

    def test_the_kill_switch_blocks_even_a_perfect_recommendation(self):
        result, finalization = _attempt(
            autonomy_state=autonomy_state(kill_switch_engaged=True),
        )
        assert result.executed is False
        assert finalization is None

    def test_disabled_autonomy_is_treated_as_a_kill_switch(self):
        result, _ = _attempt(autonomy_state=autonomy_state(enabled=False))
        assert result.block_reason == RuntimeBlockReason.KILL_SWITCH_ENGAGED

    def test_a_missing_artifact_blocks_before_authority(self):
        result, _ = _attempt(
            required_artifact_types={A.SCENARIO_LATTICE},
            available_artifact_types=set(),
        )
        assert result.block_reason == (
            RuntimeBlockReason.REQUIRED_ARTIFACT_UNAVAILABLE
        )
        assert "scenario_lattice" in result.explanation
        assert result.requires_fresh_recommendation is True

    def test_available_artifacts_do_not_block(self):
        result, _ = _attempt(
            required_artifact_types={A.SCENARIO_LATTICE},
            available_artifact_types={A.SCENARIO_LATTICE, A.KPI_STATE},
        )
        assert result.executed is True

    def test_artifact_checking_is_skipped_when_not_declared(self):
        result, _ = _attempt(required_artifact_types={A.SCENARIO_LATTICE})
        assert result.executed is True


class TestAuthorityIsRecheckedNotAssumed:
    def test_an_expired_policy_blocks(self):
        result, _ = _attempt(
            policy=authority_policy(expires_at=AS_OF - timedelta(hours=1)),
        )
        assert result.block_reason == RuntimeBlockReason.AUTHORITY_DENIED
        assert any("expired" in b for b in result.authority_blockers)

    def test_a_human_validated_mode_blocks(self):
        result, _ = _attempt(
            policy=authority_policy(
                mode=OrganizationalDecisionMode.HUMAN_VALIDATED,
            ),
        )
        assert result.block_reason == RuntimeBlockReason.AUTHORITY_DENIED

    def test_an_out_of_scope_decision_type_blocks(self):
        result, _ = _attempt(decision_type="external_commitment")
        assert result.block_reason == RuntimeBlockReason.AUTHORITY_DENIED

    def test_the_authority_blockers_are_reported(self):
        result, _ = _attempt(decision_type="external_commitment")
        assert result.authority_blockers

    def test_the_guard_never_widens_authority(self):
        # Every branch of the guard can only refuse; there is no path that
        # produces eligibility Commit 22 did not already grant.
        import ast
        import inspect

        import lltpm.runtime.validity as validity

        source = inspect.getsource(validity)
        tree = ast.parse(source)
        assignments = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "autonomous_eligible"
            and isinstance(node.ctx, ast.Store)
        ]
        assert assignments == [], (
            "the runtime must never assign autonomous eligibility; only "
            "Commit 22's policy decides it"
        )


class TestScenarioGate:
    def test_a_confident_scenario_recommendation_passes(self):
        result, _ = _attempt(
            scenario_recommendation=FakeScenarioRecommendation(
                changed_by_scenario_analysis=True,
                scenario_probability_confidence=0.82,
            ),
        )
        assert result.executed is True

    def test_a_weak_scenario_model_blocks(self):
        result, _ = _attempt(
            scenario_recommendation=FakeScenarioRecommendation(
                changed_by_scenario_analysis=True,
                scenario_probability_confidence=0.10,
            ),
        )
        assert result.block_reason == (
            RuntimeBlockReason.SCENARIO_CONFIDENCE_TOO_LOW
        )
        assert "confidence" in result.explanation

    def test_a_research_first_recommendation_blocks(self):
        result, _ = _attempt(
            scenario_recommendation=FakeScenarioRecommendation(
                recommended_action=DecisionTimingAction.RESEARCH_FIRST,
            ),
        )
        assert result.block_reason == (
            RuntimeBlockReason.SCENARIO_CONFIDENCE_TOO_LOW
        )

    def test_the_scenario_gate_runs_after_staleness(self):
        result, _ = _attempt(
            current_decision_context=T1_CONTEXT,
            scenario_recommendation=FakeScenarioRecommendation(
                changed_by_scenario_analysis=True,
                scenario_probability_confidence=0.05,
            ),
        )
        assert result.block_reason == RuntimeBlockReason.STALE_RECOMMENDATION


class TestNoTimeOfCheckTimeOfUseGap:
    def test_validation_and_finalization_are_one_call(self):
        # A caller cannot validate here and finalize later: the only way
        # to obtain the finalization tuple is through the guard, and the
        # guard produces it in the same invocation that validated.
        blocked, finalization = _attempt(current_decision_context=T1_CONTEXT)
        assert blocked.executed is False
        assert finalization is None

        allowed, finalization = _attempt()
        assert allowed.executed is True
        assert finalization is not None

    def test_a_blocked_attempt_creates_no_decision(self):
        result, finalization = _attempt(current_decision_context=T1_CONTEXT)
        assert result.decision_id is None
        assert result.execution_id is None
        assert finalization is None

    def test_repeated_blocked_attempts_stay_blocked(self):
        for _ in range(3):
            result, finalization = _attempt(
                current_decision_context=T1_CONTEXT,
            )
            assert result.executed is False
            assert finalization is None
