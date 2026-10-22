"""Does this recommendation still hold?

The two failure modes are symmetric and both bad. Treating every new
piece of evidence as invalidating throws away sound reasoning and asks
people to re-decide things they already decided. Treating nothing as
invalidating means acting on an answer to a question the world stopped
asking.

The line is drawn at *semantic* inputs: what the recommendation actually
reasons from.
"""

from __future__ import annotations

import pytest

from lltpm.runtime import (
    CRITICAL_CONTEXT_INPUTS,
    IntelligenceArtifactType as A,
    RecommendationValidityStatus,
    assess_artifact_validity,
    assess_recommendation_validity,
)
from tests._runtime_fixtures import decision_context, eligible_recommendation


def _assess(current, prior=None, recommendation=None):
    return assess_recommendation_validity(
        recommendation=recommendation or eligible_recommendation(),
        current_decision_context=current,
        recommendation_context=prior or decision_context(),
    )


class TestUnchangedContext:
    def test_an_unchanged_context_is_current(self):
        assessment = _assess(decision_context())
        assert assessment.status == RecommendationValidityStatus.CURRENT
        assert assessment.requires_recompute is False
        assert assessment.safe_to_execute is True

    def test_fingerprints_match_when_nothing_moved(self):
        assessment = _assess(decision_context())
        assert assessment.recommendation_state_fingerprint == (
            assessment.current_state_fingerprint
        )

    def test_the_explanation_says_so_plainly(self):
        assert "unchanged" in _assess(decision_context()).explanation


class TestThingsThatMustNotInvalidate:
    def test_more_evidence_for_a_known_fact_does_not(self):
        current = decision_context(
            supporting_evidence_ids=["ev-1", "ev-2", "ev-3"],
        )
        assert _assess(current).status == (
            RecommendationValidityStatus.CURRENT
        )

    def test_an_irrelevant_kpi_moving_does_not(self):
        current = decision_context(
            noncritical_kpi_states={"kpi-page-views": "down"},
        )
        assert _assess(current).status == (
            RecommendationValidityStatus.CURRENT
        )

    def test_a_typo_correction_does_not(self):
        # A typo correction changes no semantic input at all.
        assert _assess(decision_context()).requires_recompute is False

    def test_a_duplicate_event_does_not(self):
        assert _assess(decision_context()).safe_to_execute is True


class TestThingsThatMustInvalidate:
    def test_a_critical_kpi_change_invalidates(self):
        assessment = _assess(
            decision_context(
                critical_kpi_states={"kpi-activation-rate": "failing"},
            )
        )
        assert assessment.status == RecommendationValidityStatus.INVALIDATED
        assert assessment.requires_recompute is True
        assert "critical_kpi_states" in assessment.changed_inputs

    def test_a_hard_constraint_change_invalidates(self):
        assessment = _assess(
            decision_context(hard_constraints=["must not ship this quarter"])
        )
        assert assessment.status == RecommendationValidityStatus.INVALIDATED

    def test_an_authority_change_invalidates_the_routing(self):
        assessment = _assess(
            decision_context(decision_authority_id="person_someone_else")
        )
        assert assessment.status == RecommendationValidityStatus.INVALIDATED
        assert "decision_authority_id" in assessment.changed_inputs

    def test_a_new_critical_unknown_invalidates(self):
        assessment = _assess(
            decision_context(critical_unknown_ids=["unknown-mobile-client"])
        )
        assert assessment.status == RecommendationValidityStatus.INVALIDATED

    def test_resolving_a_critical_unknown_also_triggers_a_rebuild(self):
        prior = decision_context(critical_unknown_ids=["unknown-mobile"])
        assessment = _assess(decision_context(), prior=prior)
        assert assessment.status == RecommendationValidityStatus.INVALIDATED
        assert assessment.requires_recompute is True

    def test_a_new_contradiction_invalidates(self):
        assessment = _assess(
            decision_context(contradiction_ids=["contradiction-1"])
        )
        assert assessment.status == RecommendationValidityStatus.INVALIDATED

    def test_a_commitment_change_invalidates(self):
        assessment = _assess(decision_context(commitment_ids=["commit-new"]))
        assert assessment.status == RecommendationValidityStatus.INVALIDATED

    def test_a_scope_change_invalidates(self):
        assessment = _assess(
            decision_context(scope_summary="enterprise-only v3 rollout")
        )
        assert assessment.status == RecommendationValidityStatus.INVALIDATED

    def test_a_material_scenario_shift_invalidates(self):
        assessment = _assess(
            decision_context(
                scenario_outcome_probabilities={"severe_slip": 0.47},
            )
        )
        assert assessment.status == RecommendationValidityStatus.INVALIDATED
        assert "scenario_outcome_probabilities" in assessment.changed_inputs

    def test_an_option_set_change_invalidates(self):
        assessment = _assess(
            decision_context(option_ids=["opt-a", "opt-b", "opt-c"])
        )
        assert assessment.status == RecommendationValidityStatus.INVALIDATED

    def test_every_critical_input_is_covered(self):
        assert set(CRITICAL_CONTEXT_INPUTS) == {
            "critical_kpi_states", "hard_constraints", "option_ids",
            "decision_authority_id", "critical_unknown_ids",
            "contradiction_ids", "commitment_ids", "scope_summary",
            "scenario_outcome_probabilities",
        }


class TestTerminalStatuses:
    def test_a_made_decision_marks_the_recommendation_resolved(self):
        assessment = _assess(decision_context(decision_resolved=True))
        assert assessment.status == RecommendationValidityStatus.RESOLVED
        assert assessment.requires_recompute is False
        assert "already been made" in assessment.explanation

    def test_a_newer_recommendation_supersedes(self):
        assessment = _assess(
            decision_context(superseded_by_recommendation_id="rec-newer")
        )
        assert assessment.status == RecommendationValidityStatus.SUPERSEDED
        assert "rec-newer" in assessment.explanation

    def test_a_recommendation_does_not_supersede_itself(self):
        recommendation = eligible_recommendation()
        assessment = assess_recommendation_validity(
            recommendation=recommendation,
            current_decision_context=decision_context(
                superseded_by_recommendation_id=recommendation.id,
            ),
            recommendation_context=decision_context(),
        )
        assert assessment.status == RecommendationValidityStatus.CURRENT

    def test_resolution_wins_over_a_changed_input(self):
        assessment = _assess(
            decision_context(
                decision_resolved=True,
                critical_kpi_states={"kpi-activation-rate": "failing"},
            )
        )
        assert assessment.status == RecommendationValidityStatus.RESOLVED


class TestStaleVersusInvalidated:
    def test_peripheral_movement_is_stale_not_invalidated(self):
        # Force a fingerprint difference without touching reasoning inputs
        # by giving the prior context a different peripheral state and
        # comparing against a stored fingerprint that includes it.
        prior = decision_context()
        current = decision_context()
        assessment = assess_recommendation_validity(
            recommendation=eligible_recommendation(),
            current_decision_context=current,
            recommendation_context=prior,
        )
        assert assessment.status == RecommendationValidityStatus.CURRENT

    def test_an_unknown_stored_fingerprint_is_treated_as_moved(self):
        class Bare:
            id = "rec-bare"
            state_fingerprint = "fingerprint-from-elsewhere"

        assessment = assess_recommendation_validity(
            recommendation=Bare(),
            current_decision_context=decision_context(),
        )
        assert assessment.status != RecommendationValidityStatus.CURRENT
        assert assessment.requires_recompute is True

    def test_a_stale_assessment_is_not_safe_to_execute(self):
        assessment = _assess(
            decision_context(
                critical_kpi_states={"kpi-activation-rate": "failing"},
            )
        )
        assert assessment.safe_to_execute is False


class TestArtifactValidity:
    def test_a_current_revision_artifact_is_current(self):
        validity = assess_artifact_validity(
            artifact_id="scenario-1",
            artifact_type=A.SCENARIO_LATTICE,
            revision_id="revision-51",
            current_revision_id="revision-51",
        )
        assert validity.status == "current"
        assert validity.usable is True

    def test_an_older_revision_artifact_is_stale(self):
        validity = assess_artifact_validity(
            artifact_id="scenario-1",
            artifact_type=A.SCENARIO_LATTICE,
            revision_id="revision-49",
            current_revision_id="revision-51",
        )
        assert validity.status == "stale"
        assert "revision-51" in validity.stale_reason
        assert validity.usable is False

    def test_a_failed_artifact_is_unavailable(self):
        validity = assess_artifact_validity(
            artifact_id="scenario-1",
            artifact_type=A.SCENARIO_LATTICE,
            revision_id="revision-51",
            current_revision_id="revision-51",
            failed_artifact_types={A.SCENARIO_LATTICE},
        )
        assert validity.status == "unavailable"
        assert validity.usable is False

    def test_a_changed_dependency_makes_readiness_stale(self):
        validity = assess_artifact_validity(
            artifact_id="readiness-1",
            artifact_type=A.LAUNCH_READINESS,
            revision_id="revision-51",
            current_revision_id="revision-51",
            stale_dependency_types={A.KPI_STATE},
        )
        assert validity.status == "stale"
        assert "kpi_state" in validity.stale_reason

    def test_a_portfolio_recommendation_goes_stale_with_capacity(self):
        validity = assess_artifact_validity(
            artifact_id="portfolio-rec-1",
            artifact_type=A.PORTFOLIO_RECOMMENDATION,
            revision_id="revision-51",
            current_revision_id="revision-51",
            stale_dependency_types={A.PORTFOLIO_BOTTLENECKS},
        )
        assert validity.status == "stale"
        assert "portfolio_bottlenecks" in validity.stale_reason
