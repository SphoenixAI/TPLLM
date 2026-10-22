"""The six reasoning directions, risk ranking,
sensitivity, value of information, and coverage."""

from __future__ import annotations

import pytest

from tpllm.scenarios import (
    ScenarioDirection,
    ScenarioEventType,
    ScenarioQuestion,
    ScenarioQuestionType,
    answer_scenario_question,
    assess_scenario_coverage,
    calculate_scenario_sensitivity,
    estimate_value_of_information,
    find_alternate_success_paths,
    find_downstream_exposure,
    find_lateral_interactions,
    find_low_probability_high_impact_paths,
    find_most_likely_next,
    find_paths_to_outcome,
    find_upstream_precursors,
    rank_paths_by_quadrant,
    summarize_path_risk,
)
from tests._scenario_fixtures import PROJECT, horizon, lattice


class TestForwardDirection:
    def test_most_likely_next_ranked(self):
        transitions = find_most_likely_next(lattice=lattice())
        assert transitions
        probabilities = [t.conditional_probability for t in transitions]
        assert probabilities == sorted(probabilities, reverse=True)
        assert transitions[0].event_type == ScenarioEventType.DECISION_MADE


class TestBackwardDirection:
    def test_paths_to_outcome_found(self):
        paths = find_paths_to_outcome(
            lattice=lattice(), target_outcome="launch_on_time",
        )
        assert len(paths) >= 3
        assert all("launch_on_time" in p.outcome_labels for p in paths)

    def test_unmodeled_outcome_returns_nothing(self):
        assert find_paths_to_outcome(
            lattice=lattice(), target_outcome="colonize_mars",
        ) == []


class TestUpstreamDirection:
    def test_precursors_ranked_by_share_of_outcome(self):
        precursors = find_upstream_precursors(
            lattice=lattice(), outcome="launch_slips",
        )
        assert precursors
        shares = [p["share_of_outcome"] for p in precursors]
        assert shares == sorted(shares, reverse=True)
        assert all("description" in p for p in precursors)

    def test_upstream_class_events_are_flagged(self):
        precursors = find_upstream_precursors(
            lattice=lattice(), outcome="launch_slips",
        )
        assert any(p["is_upstream_class"] for p in precursors), (
            "a priority change or reopened decision is an upstream cause"
        )


class TestDownstreamDirection:
    def test_exposure_from_a_transition(self):
        lat = lattice()
        approve = next(
            t for t in lat.outgoing(lat.root_state_id)
            if t.event_type == ScenarioEventType.DECISION_MADE
        )
        exposure = find_downstream_exposure(
            lattice=lat, event_or_state_id=approve.id,
        )
        assert exposure
        assert any(e["is_downstream_class"] for e in exposure)
        assert any(e.get("exposed_outcomes") for e in exposure)

    def test_unknown_id_returns_nothing(self):
        assert find_downstream_exposure(
            lattice=lattice(), event_or_state_id="nope",
        ) == []


class TestLateralDirection:
    def test_lateral_interactions_found(self):
        lateral = find_lateral_interactions(lattice=lattice())
        assert lateral
        assert any("Mobile" in item["description"] for item in lateral)


class TestRecoveryDirection:
    def test_alternate_success_paths_surface_recovery_routes(self):
        alternates = find_alternate_success_paths(
            lattice=lattice(), target_outcome="launch_on_time",
        )
        assert alternates
        recovery_kinds = {
            r for p in alternates for r in p.recovery_opportunities
        }
        # A compatibility layer and an enterprise-only scope cut.
        assert "workaround_adopted" in recovery_kinds
        assert "scope_reduces" in recovery_kinds

    def test_direct_success_path_is_not_an_alternate(self):
        lat = lattice()
        all_success = find_paths_to_outcome(
            lattice=lat, target_outcome="launch_on_time",
        )
        alternates = find_alternate_success_paths(
            lattice=lat, target_outcome="launch_on_time",
        )
        assert len(alternates) < len(all_success)


class TestRiskRanking:
    def test_probability_and_impact_stay_separate(self):
        lat = lattice()
        summaries = [
            summarize_path_risk(lattice=lat, path=p)
            for p in rank_paths_by_quadrant(
                lattice=lat, quadrant="most_likely",
            )
        ]
        assert any(
            s.probability > 0.2 and s.impact < 0.5 for s in summaries
        ), "a likely, low-impact path should exist"

    def test_low_probability_high_impact_preserved(self):
        tail = find_low_probability_high_impact_paths(lattice=lattice())
        assert tail, (
            "a rare catastrophic path must remain visible rather than "
            "being buried beneath likely inconveniences"
        )
        for summary in tail:
            assert summary.probability <= 0.15
            assert summary.impact >= 0.6

    def test_quadrant_views(self):
        lat = lattice()
        for quadrant in (
            "most_likely", "most_damaging", "most_preventable",
            "most_uncertain", "best_recovery",
        ):
            assert rank_paths_by_quadrant(lattice=lat, quadrant=quadrant)

    def test_unknown_quadrant_rejected(self):
        with pytest.raises(ValueError, match="Unknown scenario quadrant"):
            rank_paths_by_quadrant(lattice=lattice(), quadrant="vibes")

    def test_recovery_paths_are_more_recoverable(self):
        lat = lattice()
        best = rank_paths_by_quadrant(lattice=lat, quadrant="best_recovery")
        assert summarize_path_risk(
            lattice=lat, path=best[0],
        ).recoverability >= 0.8


class TestSensitivity:
    def test_sensitivity_identifies_influential_factors(self):
        sensitivities = calculate_scenario_sensitivity(
            lattice=lattice(), outcome_label="launch_on_time",
        )
        assert sensitivities
        deltas = [s.absolute_delta for s in sensitivities]
        assert deltas == sorted(deltas, reverse=True)
        assert any(s.absolute_delta > 0 for s in sensitivities)

    def test_explanation_is_readable(self):
        top = calculate_scenario_sensitivity(
            lattice=lattice(), outcome_label="launch_on_time",
        )[0]
        assert "moves P(launch_on_time)" in top.explanation


class TestValueOfInformation:
    def test_unresolved_questions_ranked(self):
        estimates = estimate_value_of_information(
            lattice=lattice(), outcome_label="launch_on_time",
        )
        assert estimates
        assert estimates[0].unknown_id == "unk-coexistence"
        priorities = [e.priority for e in estimates]
        assert priorities == sorted(priorities, reverse=True)

    def test_estimate_explains_itself(self):
        estimate = estimate_value_of_information(
            lattice=lattice(), outcome_label="launch_on_time",
        )[0]
        assert "would sharpen" in estimate.explanation
        assert estimate.current_uncertainty > 0


class TestCoverage:
    def test_full_lattice_covers_all_six_directions(self):
        coverage = assess_scenario_coverage(lattice=lattice())
        assert coverage.upstream_coverage == 1.0
        assert coverage.downstream_coverage == 1.0
        assert coverage.lateral_coverage == 1.0
        assert coverage.recovery_coverage == 1.0
        assert coverage.missing_scenario_classes == []
        assert "All six reasoning directions" in coverage.explanation

    def test_happy_path_only_cannot_claim_coverage(self):
        from tpllm.scenarios import (
            ScenarioEventCandidate, generate_scenario_lattice,
        )
        from tests._scenario_fixtures import factors, temporal_state

        class HappyPathOnly:
            def generate_events(self, *, temporal_state, horizon, context):
                return [
                    ScenarioEventCandidate(
                        id="cand-ok", event_type=ScenarioEventType.DECISION_MADE,
                        description="approved",
                        resulting_state_changes={
                            "dependency_states": {"contract": "approved"},
                        },
                        base_probability=0.9,
                        outcome_labels=["launch_on_time"],
                    ),
                ]

        lat = generate_scenario_lattice(
            temporal_state=temporal_state(), generator=HappyPathOnly(),
            horizon=horizon(), extra_factors=factors(),
        )
        coverage = assess_scenario_coverage(lattice=lat)
        assert coverage.overall_coverage < 0.5
        assert ScenarioDirection.RECOVERY.value in (
            coverage.missing_scenario_classes
        )
        assert ScenarioDirection.LATERAL.value in (
            coverage.missing_scenario_classes
        )
        assert "Unexplored directions" in coverage.explanation


class TestQuestionEngine:
    def _ask(self, question_type, **kwargs):
        return answer_scenario_question(
            question=ScenarioQuestion(
                question_type=question_type, project_id=PROJECT, **kwargs,
            ),
            lattice=lattice(),
        )

    def test_most_likely_next(self):
        answer = self._ask(ScenarioQuestionType.MOST_LIKELY_NEXT)
        assert "Most likely next" in answer.summary
        assert answer.probability is not None
        assert answer.probability_confidence is not None
        # Probability and confidence are reported separately.
        assert answer.probability != answer.probability_confidence

    def test_paths_to_outcome(self):
        answer = self._ask(
            ScenarioQuestionType.PATHS_TO_OUTCOME,
            target_outcome="launch_on_time",
        )
        assert answer.relevant_path_ids
        assert "aggregate probability" in answer.summary

    def test_recovery_options(self):
        answer = self._ask(
            ScenarioQuestionType.RECOVERY_OPTIONS,
            target_outcome="launch_on_time",
        )
        assert "recovery route" in answer.summary
        assert answer.intervention_points

    def test_value_of_information(self):
        answer = self._ask(
            ScenarioQuestionType.VALUE_OF_INFORMATION,
            target_outcome="launch_on_time",
        )
        assert "unk-coexistence" in answer.summary
        assert "unk-coexistence" in answer.unknowns

    def test_low_probability_high_impact(self):
        answer = self._ask(ScenarioQuestionType.LOW_PROBABILITY_HIGH_IMPACT)
        assert "high-impact path" in answer.summary

    def test_every_answer_discloses_residual_mass(self):
        pruned = lattice(hz=horizon(minimum_branch_probability=0.20))
        answer = answer_scenario_question(
            question=ScenarioQuestion(
                question_type=ScenarioQuestionType.MOST_LIKELY_NEXT,
                project_id=PROJECT,
            ),
            lattice=pruned,
        )
        assert answer.omitted_probability_mass > 0

    def test_answer_exposes_no_chain_of_thought(self):
        from tpllm.scenarios import ScenarioAnswer

        fields = set(ScenarioAnswer.model_fields)
        for forbidden in ("chain_of_thought", "reasoning_trace", "thoughts",
                          "internal_monologue"):
            assert forbidden not in fields
        # It exposes auditable structure instead.
        for expected in ("supporting_factor_ids", "counterfactors",
                         "assumptions", "unknowns", "evidence_ids"):
            assert expected in fields

    def test_unhandled_question_type_is_honest(self):
        answer = self._ask(ScenarioQuestionType.WHAT_IF)
        assert "no modeled analysis" in answer.summary
