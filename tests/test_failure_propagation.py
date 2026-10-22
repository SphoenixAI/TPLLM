"""Failure propagation from coordination failure
to downstream technical and schedule exposure."""

from __future__ import annotations

import pytest

from lltpm.scenarios import (
    MiscommunicationRisk,
    MiscommunicationType,
    ScenarioEventType,
    assess_scenario_coverage,
    enumerate_paths,
    generate_scenario_lattice,
    miscommunication_to_scenario_candidate,
    probability_of_outcome,
    propagate_interaction_failure,
    summarize_propagation,
)
from tests._scenario_fixtures import factors, horizon, temporal_state


def _risk(risk_type=MiscommunicationType.STALE_DECISION_PROPAGATION,
          probability=0.27):
    return MiscommunicationRisk(
        id="risk-mobile-v2", risk_type=risk_type,
        affected_entity_ids=["mobile"], topic="contract-version",
        probability=probability, probability_confidence=0.6, impact=0.65,
        evidence_ids=["ev-launch-doc", "ev-mobile-ticket"],
        explanation=(
            "Mobile continues implementing against v2 because the current "
            "v3 decision did not propagate."
        ),
    )


class TestScenarioConversion:
    def test_miscommunication_becomes_a_scenario_branch(self):
        candidate = miscommunication_to_scenario_candidate(risk=_risk())
        assert candidate.event_type == (
            ScenarioEventType.MISCOMMUNICATION_OCCURS
        )
        assert candidate.controllable is True
        assert candidate.intervention_ids
        assert 0.0 < candidate.base_probability <= 1.0

    def test_branch_probability_derives_from_the_risk(self):
        low = miscommunication_to_scenario_candidate(risk=_risk(probability=0.1))
        high = miscommunication_to_scenario_candidate(
            risk=_risk(probability=0.8),
        )
        assert high.base_probability > low.base_probability


class TestPropagationCascade:
    def test_full_cascade_reaches_the_milestone(self):
        candidates = propagate_interaction_failure(initial_risk=_risk())
        chain = summarize_propagation(candidates)
        # lost decision context -> wrong assumption -> integration failure
        # -> KPI deterioration -> milestone risk
        assert "miscommunication_occurs" in chain
        assert "assumption_invalidated" in chain
        assert "integration_fails" in chain
        assert "kpi_deteriorates" in chain
        assert "milestone_slips" in chain

    def test_cascade_reaches_a_kpi(self):
        candidates = propagate_interaction_failure(initial_risk=_risk())
        kpi_hops = [
            c for c in candidates
            if c.event_type == ScenarioEventType.KPI_DETERIORATES
        ]
        assert kpi_hops, "the cascade must reach downstream KPI exposure"

    def test_probability_decays_along_the_cascade(self):
        candidates = propagate_interaction_failure(initial_risk=_risk())
        probabilities = [c.base_probability for c in candidates]
        # A low-probability miscommunication must not become a
        # high-probability milestone slip.
        assert probabilities[-1] <= probabilities[0]
        assert all(p <= _risk().probability for p in probabilities)

    def test_hops_are_conditionally_chained(self):
        candidates = propagate_interaction_failure(initial_risk=_risk())
        assert candidates[0].prerequisite_state_conditions == []
        for candidate in candidates[1:]:
            assert candidate.prerequisite_state_conditions, (
                "each hop must be conditional on the prior hop"
            )

    def test_terminal_hop_carries_the_outcome_label(self):
        candidates = propagate_interaction_failure(initial_risk=_risk())
        terminal = candidates[-1]
        assert terminal.event_type == ScenarioEventType.MILESTONE_SLIPS
        assert "launch_slips" in terminal.outcome_labels

    def test_only_the_origin_hop_is_directly_controllable(self):
        candidates = propagate_interaction_failure(initial_risk=_risk())
        assert candidates[0].controllable is True
        assert all(not c.controllable for c in candidates[1:])

    def test_authority_confusion_cascades_to_delay(self):
        candidates = propagate_interaction_failure(
            initial_risk=_risk(
                risk_type=MiscommunicationType.AUTHORITY_CONFUSION,
            ),
        )
        chain = summarize_propagation(candidates)
        assert "decision_delayed" in chain
        assert "milestone_slips" in chain

    def test_unmapped_risk_type_still_yields_a_branch(self):
        candidates = propagate_interaction_failure(
            initial_risk=_risk(
                risk_type=MiscommunicationType.DUPLICATE_DECISION_PATH,
            ),
        )
        assert len(candidates) == 1

    def test_empty_summary(self):
        assert summarize_propagation([]) == "No propagation modeled."


class TestPropagationInLattice:
    def _lattice(self):
        class PropagationGenerator:
            def generate_events(self, *, temporal_state, horizon, context):
                return propagate_interaction_failure(initial_risk=_risk())

        return generate_scenario_lattice(
            temporal_state=temporal_state(),
            generator=PropagationGenerator(),
            horizon=horizon(maximum_depth=6),
            extra_factors=factors(),
        )

    def test_cascade_generates_a_real_lattice(self):
        lattice = self._lattice()
        assert len(lattice.states) > 1
        assert lattice.transitions

    def test_cascade_produces_a_slip_probability(self):
        lattice = self._lattice()
        probability, confidence = probability_of_outcome(
            lattice=lattice, outcome_label="launch_slips",
        )
        assert probability > 0.0
        assert 0.0 < confidence <= 1.0

    def test_lateral_and_downstream_directions_are_covered(self):
        coverage = assess_scenario_coverage(lattice=self._lattice())
        # A miscommunication cascade is lateral in origin and downstream
        # in effect.
        assert coverage.lateral_coverage == 1.0
        assert coverage.downstream_coverage == 1.0
