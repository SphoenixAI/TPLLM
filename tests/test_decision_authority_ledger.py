"""Tests for Commit 22 — the append-only decision ledger, the symmetric
evaluation matrix, and both end-to-end spec scenarios."""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.coordination import (
    DECISION_ORIGIN_HUMAN_VALIDATED_AGENT,
    ActorRelationType,
    DecisionEvaluationAction,
    DecisionLedgerEventType,
    DecisionOrigin,
    HumanDecisionEvaluation,
    OrganizationalAutonomyState,
    OrganizationalDecisionAuthorityPolicy,
    OrganizationalDecisionLedger,
    OrganizationalDecisionMode,
    OrganizationalDecisionOutcome,
    OrganizationalDecisionRisk,
    OrganizationalDecisionStatus,
    append_autonomous_execution,
    append_human_evaluation,
    append_lifecycle_event,
    append_outcome,
    append_recommendation,
    build_decision_intelligence_panel,
    build_evaluation_matrix,
    can_agent_finalize_decision,
    classify_organizational_decision_risk,
    finalize_agent_decision,
    finalize_human_validated_decision,
    is_agent_decided,
    render_panel_sections,
    select_agent_recommendation,
)
from lltpm.core import ActorReference
from tests._coordination_fixtures import AS_OF, PROJECT, WS, actor, request
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


def _auto_policy(**overrides):
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


def _evaluation(action, recommendation, **overrides):
    defaults = dict(
        id="eval-1", recommendation_id=recommendation.id,
        evaluator=ActorReference(
            actor_id="priya", display_name="Priya Rao",
            role="Platform Architecture Lead", team_id="platform",
        ),
        action=action, evaluated_at=AS_OF,
    )
    defaults.update(overrides)
    return HumanDecisionEvaluation(**defaults)


class TestLedgerAppendOnly:
    def test_recommendation_and_evaluation_recorded(self):
        recommendation = _recommendation()
        ledger = append_recommendation(
            ledger=OrganizationalDecisionLedger(),
            recommendation=recommendation,
        )
        assert ledger.entries[0].event_type == (
            DecisionLedgerEventType.RECOMMENDED.value
        )
        ledger = append_human_evaluation(
            ledger=ledger,
            evaluation=_evaluation(
                DecisionEvaluationAction.VALIDATE, recommendation,
                selected_option_id="opt-a",
            ),
            decision_id="decision-1",
        )
        assert ledger.entries[1].event_type == (
            DecisionLedgerEventType.HUMAN_VALIDATED.value
        )

    def test_append_is_copy_on_write(self):
        original = OrganizationalDecisionLedger()
        updated = append_recommendation(
            ledger=original, recommendation=_recommendation(),
        )
        assert original.entries == []
        assert len(updated.entries) == 1

    def test_replay_is_idempotent(self):
        recommendation = _recommendation()
        ledger = append_recommendation(
            ledger=OrganizationalDecisionLedger(),
            recommendation=recommendation,
        )
        again = append_recommendation(
            ledger=ledger, recommendation=recommendation,
        )
        assert len(again.entries) == 1

    def test_lifecycle_and_outcome_events(self):
        ledger = append_lifecycle_event(
            ledger=OrganizationalDecisionLedger(),
            decision_id="decision-1",
            event_type=DecisionLedgerEventType.REOPENED,
            occurred_at=AS_OF,
        )
        ledger = append_outcome(
            ledger=ledger,
            outcome=OrganizationalDecisionOutcome(
                decision_id="decision-1",
                observed_outcome="Three integrations unblocked",
                positive=True, affected_kpi_ids=["kpi-contract-validation"],
                affected_milestone_ids=["integration freeze"],
                evidence_ids=["ev-outcome"], observed_at=AS_OF,
            ),
        )
        types = [e.event_type for e in ledger.entries]
        assert DecisionLedgerEventType.REOPENED.value in types
        assert DecisionLedgerEventType.OUTCOME_OBSERVED.value in types
        outcome_entry = ledger.entries[-1]
        assert outcome_entry.evidence_ids == ["ev-outcome"]


class TestEvaluationMatrix:
    def _matrix(self, human_option, outcome_positive):
        recommendation = _recommendation()
        ledger = append_recommendation(
            ledger=OrganizationalDecisionLedger(),
            recommendation=recommendation,
        )
        action = (
            DecisionEvaluationAction.VALIDATE
            if human_option == recommendation.recommended_option_id
            else DecisionEvaluationAction.MODIFY
        )
        ledger = append_human_evaluation(
            ledger=ledger,
            evaluation=_evaluation(
                action, recommendation, selected_option_id=human_option,
            ),
            decision_id=recommendation.decision_capsule_id,
        )
        outcome = OrganizationalDecisionOutcome(
            decision_id=recommendation.decision_capsule_id,
            observed_outcome="observed", positive=outcome_positive,
            observed_at=AS_OF + timedelta(days=14),
        )
        return build_evaluation_matrix(ledger=ledger, outcomes=[outcome])

    @pytest.mark.parametrize(
        "human_option,positive,followed",
        [
            ("opt-a", True, True),    # agent A, human A, good
            ("opt-a", False, True),   # agent A, human A, bad
            ("opt-b", True, False),   # agent A, human B, good
            ("opt-b", False, False),  # agent A, human B, bad
        ],
    )
    def test_all_four_symmetric_cases(self, human_option, positive, followed):
        cases = self._matrix(human_option, positive)
        assert len(cases) == 1
        case = cases[0]
        assert case.agent_recommended_option_id == "opt-a"
        assert case.final_option_id == human_option
        assert case.human_followed_agent is followed
        assert case.outcome_positive is positive

    def test_matrix_is_not_one_sided(self):
        # The model can express the agent being wrong and the human being
        # wrong; neither is privileged.
        agent_right = self._matrix("opt-a", True)[0]
        agent_wrong = self._matrix("opt-a", False)[0]
        assert agent_right.outcome_positive != agent_wrong.outcome_positive
        assert agent_right.human_followed_agent == (
            agent_wrong.human_followed_agent
        )


class TestHumanValidatedScenario:
    """Spec 22.31 — the full human-validated flow."""

    def test_end_to_end(self):
        caps = capsule()
        recommendation = _recommendation(caps=caps)

        # The agent recommends with an evidenced rationale.
        assert recommendation.recommended_option_id == "opt-a"
        assert recommendation.confidence >= 0.85
        assert recommendation.rationale.decisive_factors
        assert recommendation.rationale.evidence_against_ids
        assert any(
            "Mobile" in d
            for d in recommendation.rationale.dissenting_positions
        )

        # The panel shows recommendation beside authority and contacts.
        panel = build_decision_intelligence_panel(
            capsule=caps, recommendation=recommendation, routing_plan=None,
        )
        titles = [s["title"] for s in render_panel_sections(panel)]
        assert "Recommended action" in titles
        assert "Evidence against" in titles
        assert "Decision authority" in titles
        assert panel.decision_authority.role == "Platform Architecture Lead"

        # A human validates; the result is a normal conclusive record.
        evaluation = _evaluation(
            DecisionEvaluationAction.VALIDATE, recommendation,
            selected_option_id="opt-a",
        )
        record = finalize_human_validated_decision(
            recommendation=recommendation, evaluation=evaluation,
            capsule=caps,
        )
        assert record.status == OrganizationalDecisionStatus.CONCLUSIVE
        assert DECISION_ORIGIN_HUMAN_VALIDATED_AGENT in (
            record.consequence_summary
        )
        assert is_agent_decided(record) is False
        assert record.decision_authority.actor_id == "priya"

        # And the whole path is on the ledger.
        ledger = append_recommendation(
            ledger=OrganizationalDecisionLedger(),
            recommendation=recommendation,
        )
        ledger = append_human_evaluation(
            ledger=ledger, evaluation=evaluation, decision_id=record.id,
        )
        origins = {e.decision_origin for e in ledger.entries}
        assert DecisionOrigin.AGENT_RECOMMENDATION.value in origins
        assert DecisionOrigin.HUMAN_VALIDATED_AGENT.value in origins


class TestAutonomousStartupScenario:
    """Spec 22.32 — bounded autonomous finalization."""

    def _internal_capsule(self):
        return capsule(
            request=request(
                "req-schema",
                requested_action="Choose internal event schema implementation",
                why_needed="Unblocks internal service work.",
            ),
        ).model_copy(update={
            "decision_question": (
                "Choose internal event schema implementation"
            ),
            "impact_if_decided": "Unblocks internal service work.",
            "impact_if_delayed": "Internal work waits.",
        })

    def test_end_to_end(self):
        caps = self._internal_capsule()
        recommendation = _recommendation(caps=caps)
        policy = _auto_policy()

        risk = classify_organizational_decision_risk(
            capsule=caps, recommendation=recommendation, commitments=[],
            affected_kpis=[],
        )
        assert risk in (
            OrganizationalDecisionRisk.LOW, OrganizationalDecisionRisk.MODERATE,
        )

        assessment_result = can_agent_finalize_decision(
            recommendation=recommendation, capsule=caps, policy=policy,
            decision_risk=risk, recent_autonomous_decisions=[], now=AS_OF,
            decision_type="internal_technical",
            autonomy_state=OrganizationalAutonomyState(updated_at=AS_OF),
        )
        assert assessment_result.autonomous_eligible is True, (
            assessment_result.blockers
        )

        record, snapshot, execution = finalize_agent_decision(
            recommendation=recommendation, capsule=caps,
            authority_assessment=assessment_result, policy=policy, now=AS_OF,
        )
        assert record.status == OrganizationalDecisionStatus.CONCLUSIVE
        assert is_agent_decided(record) is True
        assert "policy-startup-v4" in record.decision_authority.role
        assert snapshot.captured_at == AS_OF
        assert execution.review_due_at is not None

        ledger = append_autonomous_execution(
            ledger=append_recommendation(
                ledger=OrganizationalDecisionLedger(),
                recommendation=recommendation,
            ),
            execution=execution,
            evidence_ids=snapshot.relevant_evidence_ids,
        )
        auto_entries = [
            e for e in ledger.entries
            if e.event_type == (
                DecisionLedgerEventType.AUTONOMOUSLY_FINALIZED.value
            )
        ]
        assert len(auto_entries) == 1
        assert auto_entries[0].authority_policy_id == "policy-startup-v4"
        assert auto_entries[0].evidence_ids

    def test_replayed_execution_does_not_duplicate(self):
        caps = self._internal_capsule()
        recommendation = _recommendation(caps=caps)
        policy = _auto_policy()
        assessment_result = can_agent_finalize_decision(
            recommendation=recommendation, capsule=caps, policy=policy,
            decision_risk=OrganizationalDecisionRisk.LOW,
            recent_autonomous_decisions=[], now=AS_OF,
            decision_type="internal_technical",
        )
        _, _, execution = finalize_agent_decision(
            recommendation=recommendation, capsule=caps,
            authority_assessment=assessment_result, policy=policy, now=AS_OF,
        )
        ledger = append_autonomous_execution(
            ledger=OrganizationalDecisionLedger(), execution=execution,
        )
        replayed = append_autonomous_execution(
            ledger=ledger, execution=execution,
        )
        assert len(replayed.entries) == 1


def executable_source(module) -> str:
    """Module source with docstrings and comments stripped.

    Guardrails must target executable behavior, not prose — a docstring
    explaining that something is deliberately *not* reused must not trip
    the guard that enforces it.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)
    return ast.unparse(tree)  # unparse also drops comments


class TestGuardrails:
    def test_helper_strips_prose_but_keeps_code(self):
        # The guard must not be defeatable by prose, nor fire on it.
        import sys
        import types

        source = (
            '"""Mentions forbidden_marker in a module docstring."""\n'
            "# forbidden_marker in a comment\n"
            "def f():\n"
            '    """Also forbidden_marker here."""\n'
            "    return forbidden_marker_call()\n"
        )
        module = types.ModuleType("_guardrail_probe")
        module.__file__ = "_guardrail_probe.py"
        sys.modules["_guardrail_probe"] = module
        try:
            import inspect

            original = inspect.getsource
            inspect.getsource = lambda m: source if m is module else original(m)
            try:
                stripped = executable_source(module)
            finally:
                inspect.getsource = original
        finally:
            del sys.modules["_guardrail_probe"]

        # Prose occurrences are gone; the executable call survives.
        assert "forbidden_marker in a module docstring" not in stripped
        assert "forbidden_marker in a comment" not in stripped
        assert "forbidden_marker_call()" in stripped

    def test_no_calibration_governance_reuse(self):
        import lltpm.coordination.authority_policy as pol
        import lltpm.coordination.autonomous_decisions as auto
        import lltpm.coordination.decision_authority_ledger as led

        for mod in (pol, auto, led):
            source = executable_source(mod)
            for pattern in (
                "is_application_eligible", "lltpm.application",
                "apply_calibration", "issue_approval",
                "register_active_calibration",
            ):
                assert pattern not in source, (
                    f"{mod.__name__} must not reuse calibration governance "
                    f"('{pattern}') — organizational decisions are a "
                    f"separate authority domain."
                )

    def test_no_worker_scoring(self):
        import lltpm.coordination.authority_policy as pol
        import lltpm.coordination.autonomous_decisions as auto
        import lltpm.coordination.decision_authority_ledger as led

        for mod in (pol, auto, led):
            source = executable_source(mod).lower()
            for pattern in ("employee_score", "performance_score",
                            "productivity", "responsiveness"):
                assert pattern not in source
