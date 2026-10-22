"""The full scenario-aware pipeline, including the
mandatory Option B canonical case and the autonomy integration."""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.coordination import (
    AgentRecommendationStatus,
    DecisionPosture,
    DecisionTimingAction,
    OrganizationalDecisionMode,
    OrganizationalDecisionRisk,
    ScenarioDecisionPolicy,
    analyse_option_lattice,
    assess_optionality,
    assess_recoverability,
    build_scenario_aware_recommendation,
    calculate_decision_regret,
    evaluate_decision_timing,
    scenario_recommendation_is_autonomy_eligible,
    score_option_for_posture,
    select_agent_recommendation,
)
from lltpm.scenarios import InformationValueEstimate
from tests._recommendation_fixtures import (
    POLICY as STATIC_POLICY,
    assessment as static_assessment,
    capsule,
    criteria,
)
from tests._scenario_decision_fixtures import (
    T0,
    assessment,
    comparable_lattice,
    policy,
    risky_lattice,
    safe_lattice,
    utilities,
)


def _pipeline(
    *,
    static_scores: dict[str, float],
    lattices: dict[str, object],
    reversible: dict[str, bool],
    posture: DecisionPosture = DecisionPosture.PRESERVE_OPTIONALITY,
    violated: dict[str, list[str]] | None = None,
    information_value: float = 0.05,
    cost_of_delay: float = 0.6,
    policy_overrides: dict | None = None,
):
    """Run static evaluation, per-option scenario analysis, and the
    scenario-aware recommendation end to end."""
    violated = violated or {}
    scen_policy = policy(posture, **(policy_overrides or {}))

    assessments = {
        oid: assessment(
            oid, reversible=reversible[oid], violated=violated.get(oid),
            scores={f"crit-{i}": static_scores[oid] for i in range(3)},
        )
        for oid in lattices
    }

    caps = capsule()
    base = select_agent_recommendation(
        capsule=caps,
        assessments=list(assessments.values()),
        criteria=criteria(),
        policy=STATIC_POLICY,
        critical_unknowns=[],
        now=T0,
    )

    eligible = [
        oid for oid in sorted(lattices) if not violated.get(oid)
    ]
    analyses = [
        analyse_option_lattice(
            option_id=oid, lattice=lattices[oid],
            assessment=assessments[oid], outcome_utilities=utilities(),
        )
        for oid in eligible
    ]
    regrets = {
        r.option_id: r
        for r in calculate_decision_regret(
            option_analyses=analyses, outcome_utilities=utilities(),
        )
    }
    scores = [
        score_option_for_posture(
            option_analysis=a,
            static_assessment=assessments[a.option_id],
            regret=regrets[a.option_id],
            optionality=assess_optionality(
                option_id=a.option_id, lattice=lattices[a.option_id],
            ),
            recoverability=assess_recoverability(
                option_id=a.option_id, lattice=lattices[a.option_id],
            ),
            policy=scen_policy,
            static_score=static_scores[a.option_id],
        )
        for a in analyses
    ]
    timing = evaluate_decision_timing(
        option_analyses=analyses,
        information_values=[
            InformationValueEstimate(
                unknown_id="unk-coexistence", current_uncertainty=0.5,
                expected_probability_reduction=information_value,
                affected_outcomes=["launch_on_time"],
                priority=information_value,
                explanation="Coexistence validation would sharpen this.",
            )
        ],
        cost_of_delay=cost_of_delay, policy=scen_policy,
    )
    # The static preference is whichever option scores highest statically.
    static_preferred = sorted(
        static_scores, key=lambda k: (-static_scores[k], k),
    )[0]
    base = base.model_copy(update={"recommended_option_id": static_preferred})

    return build_scenario_aware_recommendation(
        base_recommendation=base, option_scores=scores,
        option_analyses=analyses, timing=timing, policy=scen_policy, now=T0,
    )


class TestOptionBCanonicalCase:
    """The mandatory case: A wins on static score, B wins on futures."""

    def _run(self, posture):
        return _pipeline(
            static_scores={"opt-a": 0.86, "opt-b": 0.82},
            lattices={"opt-a": risky_lattice(), "opt-b": safe_lattice()},
            reversible={"opt-a": False, "opt-b": True},
            posture=posture,
        )

    @pytest.mark.parametrize(
        "posture",
        [DecisionPosture.PRESERVE_OPTIONALITY, DecisionPosture.RISK_AVERSE],
    )
    def test_scenario_analysis_overturns_the_static_preference(self, posture):
        recommendation = self._run(posture)
        assert recommendation.static_preferred_option_id == "opt-a"
        assert recommendation.scenario_preferred_option_id == "opt-b"
        assert recommendation.recommended_option_id == "opt-b"
        assert recommendation.recommendation_changed_by_scenario_analysis is (
            True
        )

    def test_the_change_is_explained_with_numbers(self):
        recommendation = self._run(DecisionPosture.PRESERVE_OPTIONALITY)
        rationale = recommendation.scenario_rationale
        assert "Severe-failure probability falls" in rationale
        assert "reversibility rises" in rationale
        assert "recoverability" in rationale

    def test_both_preferences_are_retained_separately(self):
        recommendation = self._run(DecisionPosture.RISK_AVERSE)
        # The static view is never discarded — it is reported alongside.
        assert recommendation.static_preferred_option_id != (
            recommendation.scenario_preferred_option_id
        )
        by_option = {s.option_id: s for s in recommendation.option_scores}
        assert by_option["opt-a"].static_score == 0.86
        assert by_option["opt-b"].static_score == 0.82
        assert by_option["opt-b"].scenario_score > (
            by_option["opt-a"].scenario_score
        )

    def test_posture_is_recorded(self):
        recommendation = self._run(DecisionPosture.RISK_AVERSE)
        assert recommendation.posture == DecisionPosture.RISK_AVERSE
        assert all(
            s.posture == DecisionPosture.RISK_AVERSE
            for s in recommendation.option_scores
        )


class TestStaticRecommendationSurvivesWhenJustified:
    """Scenario integration must improve decisions, not mechanically
    prefer the conservative option."""

    def test_large_static_advantage_with_comparable_risk_keeps_a(self):
        recommendation = _pipeline(
            static_scores={"opt-a": 0.95, "opt-b": 0.70},
            lattices={
                "opt-a": comparable_lattice(), "opt-b": safe_lattice(),
            },
            reversible={"opt-a": True, "opt-b": True},
        )
        assert recommendation.static_preferred_option_id == "opt-a"
        assert recommendation.recommended_option_id == "opt-a"
        assert recommendation.recommendation_changed_by_scenario_analysis is (
            False
        )

    def test_insufficient_advantage_does_not_overturn(self):
        recommendation = _pipeline(
            static_scores={"opt-a": 0.86, "opt-b": 0.82},
            lattices={"opt-a": risky_lattice(), "opt-b": safe_lattice()},
            reversible={"opt-a": False, "opt-b": True},
            policy_overrides={"minimum_option_advantage": 0.95},
        )
        assert recommendation.recommended_option_id == "opt-a"
        assert recommendation.recommendation_changed_by_scenario_analysis is (
            False
        )
        assert "does not clear" in recommendation.scenario_rationale

    def test_weak_probability_confidence_does_not_overturn(self):
        recommendation = _pipeline(
            static_scores={"opt-a": 0.86, "opt-b": 0.82},
            lattices={"opt-a": risky_lattice(), "opt-b": safe_lattice()},
            reversible={"opt-a": False, "opt-b": True},
            policy_overrides={
                "minimum_scenario_probability_confidence": 0.99,
            },
        )
        assert recommendation.recommended_option_id == "opt-a"
        assert "below the" in recommendation.scenario_rationale
        assert "floor" in recommendation.scenario_rationale

    def test_agreement_is_reported_plainly(self):
        recommendation = _pipeline(
            static_scores={"opt-a": 0.60, "opt-b": 0.82},
            lattices={"opt-a": risky_lattice(), "opt-b": safe_lattice()},
            reversible={"opt-a": False, "opt-b": True},
        )
        assert recommendation.recommendation_changed_by_scenario_analysis is (
            False
        )
        assert "agrees with the static recommendation" in (
            recommendation.scenario_rationale
        )


class TestHardConstraintsRemainHard:
    def test_scenario_analysis_cannot_resurrect_a_violating_option(self):
        # Option A violates a hard constraint but has a fine future.
        recommendation = _pipeline(
            static_scores={"opt-a": 0.99, "opt-b": 0.70},
            lattices={
                "opt-a": comparable_lattice(), "opt-b": safe_lattice(),
            },
            reversible={"opt-a": True, "opt-b": True},
            violated={"opt-a": ["approved-launch-scope"]},
        )
        scored_options = {s.option_id for s in recommendation.option_scores}
        assert "opt-a" not in scored_options, (
            "an option violating a supported hard constraint must not be "
            "analysed back into contention"
        )
        # Even though A leads on static score, it cannot be recommended.
        assert recommendation.recommended_option_id == "opt-b"
        assert "disqualified before scenario analysis" in (
            recommendation.scenario_rationale
        )


class TestUncertaintyIsCarried:
    def test_probability_confidence_reported_separately(self):
        recommendation = _pipeline(
            static_scores={"opt-a": 0.86, "opt-b": 0.82},
            lattices={"opt-a": risky_lattice(), "opt-b": safe_lattice()},
            reversible={"opt-a": False, "opt-b": True},
        )
        assert 0.0 < recommendation.scenario_probability_confidence <= 1.0
        by_option = {s.option_id: s for s in recommendation.option_scores}
        assert recommendation.scenario_probability_confidence != (
            by_option["opt-b"].scenario_score
        )

    def test_pruned_mass_is_disclosed(self):
        recommendation = _pipeline(
            static_scores={"opt-a": 0.86, "opt-b": 0.82},
            lattices={"opt-a": risky_lattice(), "opt-b": safe_lattice()},
            reversible={"opt-a": False, "opt-b": True},
        )
        assert recommendation.omitted_probability_mass >= 0.0

    def test_important_unknowns_surface(self):
        recommendation = _pipeline(
            static_scores={"opt-a": 0.86, "opt-b": 0.82},
            lattices={"opt-a": risky_lattice(), "opt-b": safe_lattice()},
            reversible={"opt-a": False, "opt-b": True},
        )
        assert "unk-coexistence" in recommendation.important_unknown_ids

    def test_dominant_paths_recorded_for_the_chosen_option(self):
        recommendation = _pipeline(
            static_scores={"opt-a": 0.86, "opt-b": 0.82},
            lattices={"opt-a": risky_lattice(), "opt-b": safe_lattice()},
            reversible={"opt-a": False, "opt-b": True},
        )
        assert recommendation.dominant_success_paths


class TestAutonomyIntegration:
    def _recommendation(self, **overrides):
        return _pipeline(
            static_scores={"opt-a": 0.86, "opt-b": 0.82},
            lattices={"opt-a": risky_lattice(), "opt-b": safe_lattice()},
            reversible={"opt-a": False, "opt-b": True},
            **overrides,
        )

    def test_autonomy_uses_the_optimized_choice_not_the_static_one(self):
        recommendation = self._recommendation()
        # The recommendation autonomy would act on is B, not static A.
        assert recommendation.recommended_option_id == "opt-b"
        eligible, reason = scenario_recommendation_is_autonomy_eligible(
            recommendation=recommendation, policy=policy(),
        )
        assert eligible is True
        assert "the authority policy still decides authority" in reason

    def test_weak_scenario_model_blocks_autonomous_action(self):
        recommendation = self._recommendation()
        strict = policy(minimum_scenario_probability_confidence=0.99)
        # The recommendation itself was built under a permissive policy,
        # so it did change; acting on it now requires a stronger model.
        eligible, reason = scenario_recommendation_is_autonomy_eligible(
            recommendation=recommendation, policy=strict,
        )
        assert eligible is False
        assert "below the" in reason

    def test_research_first_blocks_autonomous_action(self):
        recommendation = self._recommendation(
            information_value=0.64, cost_of_delay=0.05,
        )
        assert recommendation.timing.recommended_action == (
            DecisionTimingAction.RESEARCH_FIRST
        )
        eligible, reason = scenario_recommendation_is_autonomy_eligible(
            recommendation=recommendation, policy=policy(),
        )
        assert eligible is False
        assert "research_first" in reason

    def test_optimizer_cannot_change_authority_policy(self):
        import ast
        import inspect
        import lltpm.coordination.scenario_decisions as mod

        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
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
        source = ast.unparse(tree)

        for forbidden in (
            "maximum_autonomous_risk", "minimum_autonomous_confidence",
            "autonomous_allowed_decision_types",
            "OrganizationalDecisionAuthorityPolicy",
            "can_agent_finalize_decision", "finalize_agent_decision",
        ):
            assert forbidden not in source, (
                f"the scenario optimizer must not touch '{forbidden}' — it "
                f"can narrow autonomy eligibility but never widen scope."
            )


class TestDeterminismAndGuardrails:
    def test_pipeline_is_deterministic(self):
        def run():
            return _pipeline(
                static_scores={"opt-a": 0.86, "opt-b": 0.82},
                lattices={"opt-a": risky_lattice(), "opt-b": safe_lattice()},
                reversible={"opt-a": False, "opt-b": True},
            )

        assert run().model_dump() == run().model_dump()

    def test_no_worker_scoring(self):
        import ast
        import inspect
        import lltpm.coordination.decision_timing as timing
        import lltpm.coordination.decision_utility as utility
        import lltpm.coordination.optionality as opt
        import lltpm.coordination.regret as regret
        import lltpm.coordination.scenario_decisions as decisions

        def executable(module):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
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

        for module in (decisions, utility, regret, opt, timing):
            source = executable(module)
            for forbidden in (
                "employee_score", "performance_score", "productivity",
                "responsiveness", "individual_score",
            ):
                assert forbidden not in source
