"""Interaction effects, preventive actions, and
the no-needless-meeting rule."""

from __future__ import annotations

import pytest

from tpllm.scenarios import (
    INTERVENTION_COSTS,
    InteractionEffect,
    MiscommunicationRisk,
    MiscommunicationType,
    apply_preventive_action,
    build_compound_delay_effect,
    cheapest_action,
    combine_interaction_effects,
    recommend_preventive_actions,
    recommends_meeting,
)


def _risk(risk_type=MiscommunicationType.STALE_DECISION_PROPAGATION,
          probability=0.4, **overrides):
    defaults = dict(
        id="risk-1", risk_type=risk_type,
        affected_entity_ids=["mobile"], topic="contract-version",
        probability=probability, probability_confidence=0.6, impact=0.6,
        evidence_ids=["ev-1"], explanation="base explanation",
    )
    defaults.update(overrides)
    return MiscommunicationRisk(**defaults)


class TestInteractionEffects:
    def test_effects_do_not_assume_independence(self):
        base = 0.3
        single = build_compound_delay_effect(factor_ids=["f1"])
        triple = build_compound_delay_effect(
            factor_ids=["f1", "f2", "f3"],
        )
        # One hazard alone does not compound.
        assert single.multiplier == pytest.approx(1.0)
        # Three simultaneous hazards compound beyond any one of them.
        assert triple.multiplier > 1.5
        combined = combine_interaction_effects(
            base_probability=base, effects=[triple],
        )
        assert combined > base

    def test_compounding_exceeds_naive_independence(self):
        # Naive independence would give 0.3 unchanged for each factor.
        base = 0.3
        compounded = combine_interaction_effects(
            base_probability=base,
            effects=[build_compound_delay_effect(
                factor_ids=["capacity", "lost-context", "authority"],
            )],
        )
        assert compounded > base * 1.5

    def test_combination_is_clamped(self):
        result = combine_interaction_effects(
            base_probability=0.9,
            effects=[
                InteractionEffect(
                    factor_ids=["a"], effect_type="x", multiplier=5.0,
                    confidence=0.5,
                )
            ],
        )
        assert result <= 0.99

    def test_multiplier_must_be_positive(self):
        with pytest.raises(ValueError, match="positive"):
            InteractionEffect(
                factor_ids=["a"], effect_type="x", multiplier=0.0,
                confidence=0.5,
            )

    def test_combination_is_deterministic(self):
        effects = [
            build_compound_delay_effect(factor_ids=["b", "a"]),
            build_compound_delay_effect(factor_ids=["c"]),
        ]
        assert combine_interaction_effects(
            base_probability=0.4, effects=effects,
        ) == combine_interaction_effects(
            base_probability=0.4, effects=list(reversed(effects)),
        )


class TestPreventiveActions:
    def test_existing_answer_beats_everything(self):
        actions = recommend_preventive_actions(
            risk=_risk(), answer_already_exists=True,
        )
        assert len(actions) == 1
        assert actions[0].action_type == "existing_answer"
        assert actions[0].requires_human_attention is False
        assert not recommends_meeting(actions)

    def test_context_packet_preferred_over_meeting(self):
        actions = recommend_preventive_actions(
            risk=_risk(risk_type=MiscommunicationType.LOST_CONTEXT),
        )
        assert not recommends_meeting(actions)
        assert cheapest_action(actions).action_type in (
            "context_packet", "direct_routing",
        )

    def test_decision_capsule_for_stale_propagation(self):
        actions = recommend_preventive_actions(risk=_risk())
        types = [a.action_type for a in actions]
        assert "decision_capsule" in types
        assert "meeting" not in types

    def test_actions_sorted_cheapest_first(self):
        actions = recommend_preventive_actions(
            risk=_risk(risk_type=MiscommunicationType.LOST_CONTEXT),
        )
        costs = [a.expected_cost for a in actions]
        assert costs == sorted(costs)

    def test_meeting_only_when_nothing_cheaper_applies(self):
        # A risk class with no cheap intervention mapped.
        actions = recommend_preventive_actions(
            risk=_risk(
                risk_type=MiscommunicationType.DUPLICATE_DECISION_PATH,
            ),
        )
        assert recommends_meeting(actions)
        assert actions[0].requires_human_attention is True

    def test_meeting_is_the_most_expensive_intervention(self):
        assert INTERVENTION_COSTS["meeting"] == max(
            INTERVENTION_COSTS.values()
        )
        assert INTERVENTION_COSTS["existing_answer"] == min(
            INTERVENTION_COSTS.values()
        )

    def test_kpi_and_terminology_get_targeted_actions(self):
        kpi_actions = recommend_preventive_actions(
            risk=_risk(
                risk_type=MiscommunicationType.KPI_DEFINITION_MISMATCH,
            ),
        )
        assert kpi_actions[0].action_type == (
            "kpi_definition_reconciliation"
        )
        term_actions = recommend_preventive_actions(
            risk=_risk(
                risk_type=MiscommunicationType.TERMINOLOGY_AMBIGUITY,
            ),
        )
        assert term_actions[0].action_type == "glossary_entry"


class TestActionEffect:
    def test_preventive_action_reduces_modeled_probability(self):
        risk = _risk(probability=0.6)
        action = cheapest_action(recommend_preventive_actions(risk=risk))
        reduced = apply_preventive_action(risk=risk, action=action)
        assert reduced.probability < risk.probability
        assert "modeled probability falls" in reduced.explanation

    def test_original_risk_unchanged(self):
        risk = _risk(probability=0.6)
        before = risk.model_dump()
        action = cheapest_action(recommend_preventive_actions(risk=risk))
        apply_preventive_action(risk=risk, action=action)
        assert risk.model_dump() == before

    def test_reduction_is_modeled_not_asserted_as_done(self):
        risk = _risk(probability=0.6)
        action = cheapest_action(recommend_preventive_actions(risk=risk))
        reduced = apply_preventive_action(risk=risk, action=action)
        # The language is explicitly about the model, not a claim that the
        # action was taken or succeeded.
        assert "modeled probability" in reduced.explanation
        assert reduced.probability > 0.0


class TestNoWorkerScoring:
    def test_no_person_metrics_in_interaction_source(self):
        import ast
        import inspect
        import tpllm.scenarios.interaction_models as models
        import tpllm.scenarios.interaction_risk as risk_mod
        import tpllm.scenarios.miscommunication as misc
        import tpllm.scenarios.failure_propagation as prop

        def executable(module):
            """Source with docstrings and comments stripped, so prose
            cannot trip a guard that targets executable behavior."""
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                # Only these node types have a *list* body that can hold a
                # docstring; IfExp and Lambda have expression bodies.
                if not isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef,
                     ast.AsyncFunctionDef),
                ):
                    continue
                body = node.body
                if (
                    body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body.pop(0)
            return ast.unparse(tree).lower()

        for module in (models, risk_mod, misc, prop):
            source = executable(module)
            for forbidden in (
                "employee_score", "performance_score", "productivity",
                "responsiveness", "competence", "attitude", "loyalty",
                "personality", "message_count",
            ):
                assert forbidden not in source, (
                    f"{module.__name__} must not model '{forbidden}' — this "
                    f"layer predicts organizational mechanics, not human "
                    f"worth."
                )
