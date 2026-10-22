"""Human evaluation paths, the decision panel,
and layer guardrails."""

from __future__ import annotations

import pytest

from tpllm.coordination import (
    DECISION_ORIGIN_HUMAN_MODIFIED_AGENT,
    DECISION_ORIGIN_HUMAN_VALIDATED_AGENT,
    ActionRequestType,
    ActorRelationType,
    AgentRecommendationStatus,
    DecisionEvaluationAction,
    ExpertiseRequirement,
    HumanDecisionEvaluation,
    OrganizationalDecisionStatus,
    build_action_routing_plan,
    build_decision_intelligence_panel,
    build_deferral_research_need,
    finalize_human_validated_decision,
    identify_routing_candidates,
    render_panel_sections,
    select_agent_recommendation,
)
from tpllm.core import ActorReference
from tests._coordination_fixtures import AS_OF, actor, request
from tests._recommendation_fixtures import (
    POLICY,
    assessment,
    capsule,
    criteria,
)


def _recommendation(caps=None, assessments=None, unknowns=()):
    return select_agent_recommendation(
        capsule=caps or capsule(),
        assessments=assessments if assessments is not None else [
            assessment("opt-a", {f"crit-{i}": 0.95 for i in range(3)}),
            assessment("opt-b", {f"crit-{i}": 0.40 for i in range(3)}),
        ],
        criteria=criteria(),
        policy=POLICY,
        critical_unknowns=list(unknowns),
        now=AS_OF,
    )


def _evaluator():
    return ActorReference(
        actor_id="priya", display_name="Priya Rao",
        role="Platform Architecture Lead", team_id="platform",
    )


def _evaluation(action, **overrides):
    defaults = dict(
        id="eval-1", recommendation_id="rec-capsule-req-1",
        evaluator=_evaluator(), action=action, evaluated_at=AS_OF,
    )
    defaults.update(overrides)
    return HumanDecisionEvaluation(**defaults)


class TestHumanValidation:
    def test_validate_creates_conclusive_commit14_record(self):
        recommendation = _recommendation()
        caps = capsule()
        record = finalize_human_validated_decision(
            recommendation=recommendation,
            evaluation=_evaluation(
                DecisionEvaluationAction.VALIDATE,
                recommendation_id=recommendation.id,
                selected_option_id=recommendation.recommended_option_id,
            ),
            capsule=caps,
        )
        # A normal organizational decision — no competing decision type.
        assert record.status == OrganizationalDecisionStatus.CONCLUSIVE
        assert record.decision_authority.actor_id == "priya"
        assert record.decided_at == AS_OF
        assert record.evidence_ids
        assert DECISION_ORIGIN_HUMAN_VALIDATED_AGENT in (
            record.consequence_summary
        )

    def test_modify_preserves_original_recommendation(self):
        recommendation = _recommendation()
        record = finalize_human_validated_decision(
            recommendation=recommendation,
            evaluation=_evaluation(
                DecisionEvaluationAction.MODIFY,
                recommendation_id=recommendation.id,
                selected_option_id="opt-b",
                modification_summary="Approve for enterprise only",
            ),
            capsule=capsule(),
        )
        assert record.conclusion.startswith("opt-b")
        assert "enterprise only" in record.conclusion
        # The agent's original recommendation is preserved in the record
        # and unchanged on the recommendation itself.
        assert "opt-a" in record.consequence_summary
        assert DECISION_ORIGIN_HUMAN_MODIFIED_AGENT in (
            record.consequence_summary
        )
        assert recommendation.recommended_option_id == "opt-a"

    def test_choosing_a_different_option_counts_as_modification(self):
        recommendation = _recommendation()
        record = finalize_human_validated_decision(
            recommendation=recommendation,
            evaluation=_evaluation(
                DecisionEvaluationAction.VALIDATE,
                recommendation_id=recommendation.id,
                selected_option_id="opt-b",
            ),
            capsule=capsule(),
        )
        assert DECISION_ORIGIN_HUMAN_MODIFIED_AGENT in (
            record.consequence_summary
        )

    def test_reject_does_not_finalize_or_mutate_agent_authority(self):
        recommendation = _recommendation()
        before = recommendation.model_dump()
        evaluation = _evaluation(
            DecisionEvaluationAction.REJECT,
            recommendation_id=recommendation.id,
            rejection_reason="Mobile coexistence risk is unacceptable now",
        )
        with pytest.raises(ValueError, match="validate/modify"):
            finalize_human_validated_decision(
                recommendation=recommendation, evaluation=evaluation,
                capsule=capsule(),
            )
        # Rejection is recorded, never treated as proof the agent erred,
        # and never rewrites the recommendation or any policy.
        assert recommendation.model_dump() == before
        assert evaluation.rejection_reason

    def test_evaluation_must_match_recommendation(self):
        with pytest.raises(ValueError, match="does not belong"):
            finalize_human_validated_decision(
                recommendation=_recommendation(),
                evaluation=_evaluation(
                    DecisionEvaluationAction.VALIDATE,
                    recommendation_id="rec-other",
                    selected_option_id="opt-a",
                ),
                capsule=capsule(),
            )


class TestDeferral:
    def test_defer_creates_research_need(self):
        recommendation = _recommendation()
        need = build_deferral_research_need(
            recommendation=recommendation,
            evaluation=_evaluation(
                DecisionEvaluationAction.DEFER,
                recommendation_id=recommendation.id,
                defer_reason="Need cross-client validation results",
            ),
        )
        assert need.question == recommendation.decision_question
        assert "cross-client validation" in need.missing_evidence[0]
        assert need.requested_by.role == "Platform Architecture Lead"

    def test_non_deferral_rejected(self):
        recommendation = _recommendation()
        with pytest.raises(ValueError, match="deferral"):
            build_deferral_research_need(
                recommendation=recommendation,
                evaluation=_evaluation(
                    DecisionEvaluationAction.VALIDATE,
                    recommendation_id=recommendation.id,
                    selected_option_id="opt-a",
                ),
            )


class TestDecisionPanel:
    def _panel(self, recommendation=None, with_routing=False):
        caps = capsule()
        routing = None
        if with_routing:
            authority = actor(
                relations=[ActorRelationType.MAY_APPROVE,
                           ActorRelationType.OWNS_SYSTEM],
            )
            expert = actor(
                "sam", role="Staff Engineer", team="identity",
                relations=[ActorRelationType.HAS_EXPERTISE],
            )
            from types import SimpleNamespace
            candidates = identify_routing_candidates(
                request=request("req-1"),
                actor_relations=[
                    SimpleNamespace(actor=authority, expertise_topics=[]),
                    SimpleNamespace(actor=expert,
                                    expertise_topics=["contracts"]),
                ],
                expertise_requirements=[
                    ExpertiseRequirement(
                        topic="contracts", why_needed="compat",
                        required_depth="deep",
                    )
                ],
                capacity_constraints=[],
            )
            routing = build_action_routing_plan(
                request=request("req-1"), candidates=candidates,
            )
        return build_decision_intelligence_panel(
            capsule=caps,
            recommendation=(
                recommendation if recommendation is not None
                else _recommendation(caps=caps)
            ),
            routing_plan=routing,
        )

    def test_panel_contains_recommendation(self):
        panel = self._panel()
        assert panel.recommendation is not None
        assert panel.recommendation.recommended_option_id == "opt-a"

    def test_panel_contains_authority_with_role(self):
        panel = self._panel()
        assert panel.decision_authority is not None
        assert panel.decision_authority.role == "Platform Architecture Lead"

    def test_panel_contains_contacts_and_experts(self):
        panel = self._panel(with_routing=True)
        contact_ids = {c.actor_id for c in panel.relevant_contacts}
        assert "priya" in contact_ids  # authority
        assert "dana" in contact_ids   # requester
        assert "kim" in contact_ids    # dissenting position holder
        assert {c.actor_id for c in panel.expertise_contacts} == {"sam"}

    def test_panel_contains_evidence_provenance(self):
        panel = self._panel()
        assert panel.evidence_refs
        assert panel.latest_conclusive_decision == (
            "Contract v3 preferred, pending prepaid validation"
        )

    def test_panel_does_not_mutate_facts(self):
        caps = capsule()
        recommendation = _recommendation(caps=caps)
        before_capsule = caps.model_dump()
        before_rec = recommendation.model_dump()
        build_decision_intelligence_panel(
            capsule=caps, recommendation=recommendation, routing_plan=None,
        )
        assert caps.model_dump() == before_capsule
        assert recommendation.model_dump() == before_rec

    def test_panel_shows_recommendation_even_when_human_must_validate(self):
        # The recommendation is never hidden pending validation.
        panel = self._panel()
        sections = render_panel_sections(panel)
        titles = [s["title"] for s in sections]
        assert "Recommended action" in titles
        assert "Why this option wins" in titles
        assert "Evidence against" in titles
        assert "Decision authority" in titles

    def test_panel_renders_abstention_honestly(self):
        caps = capsule()
        abstained = _recommendation(
            caps=caps,
            assessments=[
                assessment("opt-a", {f"crit-{i}": 0.81 for i in range(3)}),
                assessment("opt-b", {f"crit-{i}": 0.79 for i in range(3)}),
            ],
        )
        assert abstained.status == AgentRecommendationStatus.CONTESTED
        panel = build_decision_intelligence_panel(
            capsule=caps, recommendation=abstained, routing_plan=None,
        )
        sections = render_panel_sections(panel)
        body = next(
            s for s in sections if s["title"] == "Agent recommendation"
        )["body"]
        assert "contested" in body
        assert "Recommended action" not in [s["title"] for s in sections]

    def test_panel_without_recommendation(self):
        caps = capsule()
        panel = build_decision_intelligence_panel(
            capsule=caps, recommendation=None, routing_plan=None,
        )
        sections = render_panel_sections(panel)
        assert any(
            s.get("body") == "Not evaluated." for s in sections
        )


class TestGuardrails:
    def test_no_application_or_calibration_authority(self):
        import inspect
        import tpllm.coordination.recommendations as rec
        import tpllm.coordination.decision_evaluation as ev
        import tpllm.coordination.recommendation_validation as val

        for mod in (rec, ev, val):
            source = inspect.getsource(mod)
            for pattern in (
                "tpllm.application", "apply_calibration", "issue_approval",
                "is_application_eligible", "transition_status",
                "register_active_calibration",
            ):
                assert pattern not in source, (
                    f"{mod.__name__} must not reference '{pattern}' — "
                    f"organizational decisions are not calibration "
                    f"application."
                )

    def test_no_employee_scoring(self):
        import inspect
        import tpllm.coordination.recommendations as rec
        import tpllm.coordination.decision_evaluation as ev
        import tpllm.coordination.recommendation_validation as val

        for mod in (rec, ev, val):
            source = inspect.getsource(mod).lower()
            for pattern in ("employee_score", "performance_score",
                            "productivity", "responsiveness"):
                assert pattern not in source
