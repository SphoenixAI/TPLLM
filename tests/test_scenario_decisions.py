"""Counterfactuals and option scenario analysis."""

from __future__ import annotations

import pytest

from tpllm.coordination import (
    CounterfactualOverreachError,
    DecisionPosture,
    ScenarioDecisionPolicy,
    SevereOutcomeDefinition,
    analyse_option_lattice,
    build_option_counterfactual,
    default_severe_outcomes,
    resolve_posture_weights,
)
from tests._recommendation_fixtures import capsule
from tests._scenario_decision_fixtures import (
    T0,
    assessment,
    base_state,
    risky_lattice,
    safe_lattice,
    utilities,
)


class TestCounterfactualConstruction:
    def _build(self, **overrides):
        caps = capsule()
        return build_option_counterfactual(
            base_state=base_state(),
            capsule=caps,
            option=caps.options[0],
            assessment=assessment(caps.options[0].id),
            as_of=T0,
            **overrides,
        )

    def test_only_decision_dimensions_change(self):
        counterfactual = self._build()
        changed = set(counterfactual.changed_state_fields)
        assert changed <= {"decision_states", "blockers_resolved"}

    def test_approving_does_not_imply_implementation_success(self):
        counterfactual = self._build()
        # Downstream dimensions are explicitly preserved, not asserted.
        assert "kpi_states" in counterfactual.preserved_state_fields
        assert "integration_state" in counterfactual.preserved_state_fields
        assert "milestone_state" in counterfactual.preserved_state_fields
        serialized = str(counterfactual.changed_state_fields)
        assert "integration" not in serialized
        assert "passed" not in serialized

    def test_asserting_downstream_success_is_rejected(self):
        with pytest.raises(
            CounterfactualOverreachError, match="downstream outcome succeed",
        ):
            self._build(
                additional_effects={
                    "integration_state": {"integration": "passed"},
                },
            )

    def test_asserting_a_kpi_outcome_is_rejected(self):
        with pytest.raises(CounterfactualOverreachError, match="may not assert"):
            self._build(additional_effects={"kpi_states": {"k": "healthy"}})

    def test_unknown_dimension_rejected(self):
        with pytest.raises(
            CounterfactualOverreachError, match="not a decision-settable",
        ):
            self._build(additional_effects={"team_morale": "great"})

    def test_additional_decision_effects_allowed(self):
        counterfactual = self._build(
            additional_effects={"decision_states": {"dec-scope": "enterprise"}},
        )
        assert counterfactual.changed_state_fields["decision_states"][
            "dec-scope"
        ] == "enterprise"

    def test_counterfactual_records_provenance(self):
        counterfactual = self._build()
        assert counterfactual.root_temporal_state_id == (
            base_state().state_fingerprint
        )
        assert counterfactual.assumed_decision_effects
        assert counterfactual.evidence_ids

    def test_deterministic(self):
        assert self._build().model_dump() == self._build().model_dump()


class TestOptionScenarioAnalysis:
    def _analyse(self, option_id, lattice, reversible=True):
        return analyse_option_lattice(
            option_id=option_id, lattice=lattice,
            assessment=assessment(option_id, reversible=reversible),
            outcome_utilities=utilities(),
        )

    def test_one_analysis_per_option_lattice(self):
        risky = self._analyse("opt-a", risky_lattice(), reversible=False)
        safe = self._analyse("opt-b", safe_lattice())
        assert risky.lattice_id != safe.lattice_id
        assert risky.option_id == "opt-a"
        assert safe.option_id == "opt-b"

    def test_risk_profiles_differ_as_modeled(self):
        risky = self._analyse("opt-a", risky_lattice(), reversible=False)
        safe = self._analyse("opt-b", safe_lattice())
        assert safe.target_success_probability > (
            risky.target_success_probability
        )
        assert safe.severe_failure_probability < (
            risky.severe_failure_probability
        )
        assert safe.recoverability > risky.recoverability
        assert safe.optionality > risky.optionality

    def test_reversibility_comes_from_the_static_assessment(self):
        assert self._analyse(
            "opt-a", safe_lattice(), reversible=False,
        ).reversibility == 0.0
        assert self._analyse(
            "opt-b", safe_lattice(), reversible=True,
        ).reversibility == 1.0

    def test_unknown_reversibility_is_neutral(self):
        analysis = analyse_option_lattice(
            option_id="opt-x", lattice=safe_lattice(),
            assessment=assessment("opt-x").model_copy(
                update={"reversible": None},
            ),
            outcome_utilities=utilities(),
        )
        assert analysis.reversibility == 0.5

    def test_probability_confidence_distinct_from_probability(self):
        analysis = self._analyse("opt-b", safe_lattice())
        assert analysis.probability_confidence != (
            analysis.target_success_probability
        )
        assert 0.0 < analysis.probability_confidence <= 1.0

    def test_tail_mass_carried_into_the_analysis(self):
        from tests._scenario_decision_fixtures import horizon, option_lattice

        pruned = option_lattice(
            success_weight=0.85, failure_weight=0.15, recovery_weight=0.75,
        )
        analysis = self._analyse("opt-b", pruned)
        assert analysis.tail_probability_mass >= 0.0

    def test_unknowns_surface_for_information_value(self):
        analysis = self._analyse("opt-b", safe_lattice())
        assert "unk-coexistence" in analysis.valuable_information_unknown_ids

    def test_dominant_paths_recorded(self):
        analysis = self._analyse("opt-b", safe_lattice())
        assert analysis.dominant_success_paths
        assert analysis.dominant_failure_paths

    def test_severe_outcome_definition_is_configurable(self):
        custom = [
            SevereOutcomeDefinition(
                id="custom", description="A bespoke severe outcome.",
                outcome_predicates=["never_happens"], severity=0.9,
            )
        ]
        analysis = analyse_option_lattice(
            option_id="opt-b", lattice=safe_lattice(),
            assessment=assessment("opt-b"),
            severe_outcomes=custom, outcome_utilities=utilities(),
        )
        # Nothing matches the bespoke predicate, so severity is zero.
        assert analysis.severe_failure_probability == 0.0
        assert default_severe_outcomes()[0].outcome_predicates == [
            "launch_slips"
        ]

    def test_worst_credible_outcome_described(self):
        analysis = self._analyse("opt-a", risky_lattice(), reversible=False)
        assert analysis.worst_credible_outcome
        assert "slips" in analysis.worst_credible_outcome.lower()


class TestPolicyAndPosture:
    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError, match="sum to approximately 1.0"):
            ScenarioDecisionPolicy(tail_risk_weight=0.9)

    def test_weights_bounded(self):
        with pytest.raises(ValueError, match="within"):
            ScenarioDecisionPolicy(tail_risk_weight=-0.1)

    def test_posture_reweights_and_renormalizes(self):
        optionality = resolve_posture_weights(
            policy=ScenarioDecisionPolicy(
                posture=DecisionPosture.PRESERVE_OPTIONALITY,
            ),
        )
        risk = resolve_posture_weights(
            policy=ScenarioDecisionPolicy(
                posture=DecisionPosture.RISK_AVERSE,
            ),
        )
        assert sum(optionality.values()) == pytest.approx(1.0)
        assert sum(risk.values()) == pytest.approx(1.0)
        assert optionality["reversibility"] > risk["reversibility"]
        assert risk["tail_risk"] > optionality["tail_risk"]

    def test_every_posture_produces_valid_weights(self):
        for posture in DecisionPosture:
            weights = resolve_posture_weights(
                policy=ScenarioDecisionPolicy(posture=posture),
            )
            assert sum(weights.values()) == pytest.approx(1.0)
            assert all(0.0 <= w <= 1.0 for w in weights.values())
