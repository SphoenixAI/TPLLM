"""Tests for Commit 5 — Calibration Regression & Evidence Bundle Suite.

Calibration proposals are treated as configuration patches that must pass
a deterministic regression suite against historical cases before they are
eligible for human review. Nothing is auto-applied.
"""

from __future__ import annotations

from lltpm.structural import (
    CalibrationProposal,
    CalibrationProposalType,
    CalibrationTestCase,
    CalibrationTestCaseStatus,
    CalibrationTestCaseType,
    EvidenceBundleRecommendation,
    InterventionOption,
    InterventionType,
    ProposalStatus,
    RegressionCaseClassification,
    create_calibration_proposal_from_hypothesis,
    map_hypothesis_type_to_proposal_type,
)
from lltpm.structural.calibration import (
    CalibrationHypothesis,
    CalibrationHypothesisStatus,
    CalibrationHypothesisType,
)
from lltpm.structural.calibration_cases import (
    generate_calibration_test_cases,
    run_calibration_test_case,
)
from lltpm.structural.feedback import HumanDecisionType, record_human_decision
from lltpm.structural.outcomes import DecisionOutcomeLog
from lltpm.structural.regression import (
    build_historical_validation_impact,
    build_regression_tested_proposal,
    bundle_calibration_evidence,
    run_regression_test,
    simulate_proposal_on_case,
)


# ── Fakes & helpers ───────────────────────────────────────────────────

class FakeStore:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str, object]] = []

    def save(self, program_id: str, name: str, model: object) -> str:
        self.saved.append((program_id, name, model))
        return name


def _options_bridge_top() -> list[InterventionOption]:
    """Bridge clearly #1, Reviewer #2, Milestone #3."""
    return [
        InterventionOption(
            id="bridge_injection",
            intervention_type=InterventionType.BRIDGE_INJECTION,
            predicted_structural_risk_reduction=0.9, confidence=0.9, disruption_cost="low",
        ),  # tradeoff_score = 0.81
        InterventionOption(
            id="reviewer_redistribution",
            intervention_type=InterventionType.REVIEWER_REDISTRIBUTION,
            predicted_structural_risk_reduction=0.5, confidence=0.8, disruption_cost="medium",
        ),  # tradeoff_score = 0.20
        InterventionOption(
            id="milestone_resequencing",
            intervention_type=InterventionType.MILESTONE_RESEQUENCING,
            predicted_structural_risk_reduction=0.55, confidence=0.6, disruption_cost="high",
        ),  # tradeoff_score = 0.11
    ]


def _decision_override(option_id: str = "reviewer_redistribution"):
    """Human overrode the agent and chose a different option."""
    return record_human_decision(
        "rec-bridge",
        HumanDecisionType.OVERRIDDEN,
        intervention_option_id=option_id,
        signal_id="sig-bottleneck",
        override_text="Org policy forbids mocking this API",
        agent_predicted_risk_reduction=0.9,
        agent_confidence=0.9,
        agent_severity="high",
    )


def _decision_accepted(option_id: str = "bridge_injection"):
    """Human accepted the agent's top recommendation."""
    return record_human_decision(
        "rec-bridge",
        HumanDecisionType.ACCEPTED,
        intervention_option_id=option_id,
        signal_id="sig-bottleneck",
        agent_predicted_risk_reduction=0.9,
        agent_confidence=0.9,
        agent_severity="high",
    )


def _outcome(decision, actual_risk_change: float = 0.6):
    return DecisionOutcomeLog(
        id=f"dol-{decision.id}",
        decision_record_id=decision.id,
        actual_risk_change=actual_risk_change,
    )


def _hypothesis(
    hyp_type: CalibrationHypothesisType = CalibrationHypothesisType.ADJUST_INTERVENTION_COST,
    confidence: float = 0.75,
) -> CalibrationHypothesis:
    return CalibrationHypothesis(
        id="hyp-1",
        hypothesis_type=hyp_type,
        status=CalibrationHypothesisStatus.HYPOTHESIS,
        metric_name_affected="betweenness_centrality",
        summary="Increase bridge injection disruption penalty when constraint present.",
        confidence=confidence,
    )


# ── Test 1: Proposal created from hypothesis ─────────────────────────

class TestProposalCreation:
    def test_proposal_from_hypothesis(self):
        hyp = _hypothesis()
        proposal = create_calibration_proposal_from_hypothesis(hypothesis=hyp)

        assert proposal.status == ProposalStatus.PROPOSED
        assert hyp.id in proposal.source_hypothesis_ids
        assert proposal.proposal_type == CalibrationProposalType.ADJUST_INTERVENTION_COST
        assert proposal.confidence == hyp.confidence
        assert proposal.id.startswith("proposal-")


# ── Test 2: Regression test validates clean proposal ──────────────────

class TestCleanValidation:
    def test_clean_proposal_validated_for_review(self):
        options = _options_bridge_top()
        d1 = _decision_override("reviewer_redistribution")
        d2 = _decision_override("reviewer_redistribution")
        d3 = _decision_override("reviewer_redistribution")
        o1, o2, o3 = _outcome(d1), _outcome(d2), _outcome(d3)

        proposal = CalibrationProposal(
            id="p-clean",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
            proposed_change_summary="Penalize bridge injection when architecture constraint present.",
        )

        report, case_results = run_regression_test(
            proposal=proposal,
            historical_decisions=[d1, d2, d3],
            historical_outcomes=[o1, o2, o3],
            historical_options_by_decision_id={
                d1.id: options, d2.id: options, d3.id: options,
            },
            min_replayable_cases=3,
        )

        assert report.status == ProposalStatus.VALIDATED_FOR_REVIEW
        assert proposal.status == ProposalStatus.VALIDATED_FOR_REVIEW
        assert report.regression_count == 0
        assert report.improved_count > 0
        assert report.replayed_case_count == 3


# ── Test 3: Regression detected blocks promotion ─────────────────────

class TestRegressionBlocks:
    def test_regression_detected_blocks_promotion(self):
        options = _options_bridge_top()

        d_override = _decision_override("reviewer_redistribution")
        o_override = _outcome(d_override)

        d_accepted = _decision_accepted("bridge_injection")
        o_accepted = _outcome(d_accepted, actual_risk_change=0.8)

        proposal = CalibrationProposal(
            id="p-regress",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
            proposed_change_summary="Penalize bridge injection globally.",
        )

        report, case_results = run_regression_test(
            proposal=proposal,
            historical_decisions=[d_override, d_accepted],
            historical_outcomes=[o_override, o_accepted],
            historical_options_by_decision_id={
                d_override.id: options, d_accepted.id: options,
            },
            min_replayable_cases=2,
        )

        assert report.status == ProposalStatus.REGRESSION_DETECTED
        assert proposal.status == ProposalStatus.REGRESSION_DETECTED

        impact = build_historical_validation_impact(
            proposal=proposal, report=report, case_results=case_results,
        )
        bundle = bundle_calibration_evidence(
            proposal=proposal, report=report, impact=impact,
        )
        assert bundle.recommendation == EvidenceBundleRecommendation.REJECT_DUE_TO_REGRESSION


# ── Test 4: Too few cases means needs more data ──────────────────────

class TestNeedsMoreData:
    def test_too_few_cases_needs_more_data(self):
        options = _options_bridge_top()
        d1 = _decision_override()
        o1 = _outcome(d1)

        proposal = CalibrationProposal(
            id="p-few",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
        )

        report, _ = run_regression_test(
            proposal=proposal,
            historical_decisions=[d1],
            historical_outcomes=[o1],
            historical_options_by_decision_id={d1.id: options},
            min_replayable_cases=3,
        )

        assert report.status == ProposalStatus.NEEDS_MORE_DATA
        assert proposal.status == ProposalStatus.NEEDS_MORE_DATA


# ── Test 5: Evidence bundle summarizes result ─────────────────────────

class TestEvidenceBundle:
    def test_evidence_bundle_promote_for_review(self):
        options = _options_bridge_top()
        decisions = [_decision_override() for _ in range(4)]
        outcomes = [_outcome(d) for d in decisions]

        proposal = CalibrationProposal(
            id="p-bundle",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
            proposed_change_summary="Penalize bridge injection.",
        )

        report, case_results = run_regression_test(
            proposal=proposal,
            historical_decisions=decisions,
            historical_outcomes=outcomes,
            historical_options_by_decision_id={d.id: options for d in decisions},
            min_replayable_cases=3,
        )

        impact = build_historical_validation_impact(
            proposal=proposal, report=report, case_results=case_results,
        )
        bundle = bundle_calibration_evidence(
            proposal=proposal, report=report, impact=impact,
        )

        assert bundle.recommendation == EvidenceBundleRecommendation.PROMOTE_FOR_REVIEW
        assert "improved" in bundle.explanation.lower() or str(report.improved_count) in bundle.explanation
        assert str(report.regression_count) in bundle.explanation


# ── Test 6: Historical validation impact calculates match rate ────────

class TestHistoricalImpact:
    def test_match_rate_calculation(self):
        options = _options_bridge_top()
        decisions = [_decision_override() for _ in range(10)]
        outcomes = [_outcome(d) for d in decisions]

        proposal = CalibrationProposal(
            id="p-impact",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
        )

        report, case_results = run_regression_test(
            proposal=proposal,
            historical_decisions=decisions,
            historical_outcomes=outcomes,
            historical_options_by_decision_id={d.id: options for d in decisions},
            min_replayable_cases=3,
        )

        impact = build_historical_validation_impact(
            proposal=proposal, report=report, case_results=case_results,
        )

        assert impact.total_replayable_overrides == 10
        assert impact.matched_human_override_count >= 0
        assert 0.0 <= impact.match_rate <= 1.0
        matched = sum(1 for r in case_results if r.proposal_matched_human_choice)
        assert impact.matched_human_override_count == matched
        assert abs(impact.match_rate - matched / 10) < 0.001


# ── Test 7: Calibration test cases generated only for validated ───────

class TestCalibrationTestCaseGeneration:
    def test_cases_generated_for_validated_proposals(self):
        options = _options_bridge_top()
        decisions = [_decision_override() for _ in range(4)]
        outcomes = [_outcome(d) for d in decisions]

        proposal = CalibrationProposal(
            id="p-cases",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
        )

        report, case_results = run_regression_test(
            proposal=proposal,
            historical_decisions=decisions,
            historical_outcomes=outcomes,
            historical_options_by_decision_id={d.id: options for d in decisions},
            min_replayable_cases=3,
        )

        assert proposal.status == ProposalStatus.VALIDATED_FOR_REVIEW

        test_cases = generate_calibration_test_cases(
            proposal=proposal, case_results=case_results,
        )

        assert len(test_cases) > 0
        for tc in test_cases:
            assert tc.status == CalibrationTestCaseStatus.ACTIVE
            assert tc.test_type == CalibrationTestCaseType.RANK_HUMAN_OPTION_HIGHER

    def test_no_cases_for_regressed_proposals(self):
        options = _options_bridge_top()
        d_override = _decision_override()
        d_accepted = _decision_accepted()

        proposal = CalibrationProposal(
            id="p-regressed",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
            status=ProposalStatus.REGRESSION_DETECTED,
        )

        from lltpm.structural.proposals import RegressionCaseResult
        case_results = [
            RegressionCaseResult(
                id="r1",
                calibration_proposal_id=proposal.id,
                human_decision_record_id=d_override.id,
                classification=RegressionCaseClassification.IMPROVED,
                improved=True,
            ),
        ]

        test_cases = generate_calibration_test_cases(
            proposal=proposal, case_results=case_results,
        )
        assert test_cases == []


# ── Test 8: Calibration test case runner passes valid case ────────────

class TestCaseRunnerPass:
    def test_runner_passes_valid_case(self):
        options = _options_bridge_top()
        decision = _decision_override("reviewer_redistribution")
        outcome = _outcome(decision)

        proposal = CalibrationProposal(
            id="p-pass",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
            status=ProposalStatus.VALIDATED_FOR_REVIEW,
        )

        case = CalibrationTestCase(
            id="ctc-pass",
            calibration_proposal_id=proposal.id,
            source_human_decision_record_id=decision.id,
            test_type=CalibrationTestCaseType.RANK_HUMAN_OPTION_HIGHER,
            expected_human_option_rank_at_or_above=1,
        )

        passed = run_calibration_test_case(
            case=case,
            proposal=proposal,
            decision=decision,
            outcome=outcome,
            intervention_options=options,
        )
        assert passed is True


# ── Test 9: Calibration test case runner fails regression ─────────────

class TestCaseRunnerFail:
    def test_runner_fails_regression(self):
        options = _options_bridge_top()
        decision = _decision_accepted("bridge_injection")
        outcome = _outcome(decision, actual_risk_change=0.8)

        proposal = CalibrationProposal(
            id="p-fail",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
            status=ProposalStatus.VALIDATED_FOR_REVIEW,
        )

        case = CalibrationTestCase(
            id="ctc-fail",
            calibration_proposal_id=proposal.id,
            source_human_decision_record_id=decision.id,
            test_type=CalibrationTestCaseType.NO_REGRESSION_ON_ACCEPTED_TOP,
        )

        passed = run_calibration_test_case(
            case=case,
            proposal=proposal,
            decision=decision,
            outcome=outcome,
            intervention_options=options,
        )
        assert passed is False


# ── Test 10: No auto-apply safety ────────────────────────────────────

class TestNoAutoApply:
    def test_validated_proposal_is_not_applied(self):
        options = _options_bridge_top()
        decisions = [_decision_override() for _ in range(4)]
        outcomes = [_outcome(d) for d in decisions]

        proposal = CalibrationProposal(
            id="p-safety",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
        )

        scores_before = [o.tradeoff_score() for o in options]
        configs_before = [(o.disruption_cost, o.confidence, o.predicted_structural_risk_reduction) for o in options]

        report, _ = run_regression_test(
            proposal=proposal,
            historical_decisions=decisions,
            historical_outcomes=outcomes,
            historical_options_by_decision_id={d.id: options for d in decisions},
            min_replayable_cases=3,
        )

        assert proposal.status == ProposalStatus.VALIDATED_FOR_REVIEW
        assert proposal.status != ProposalStatus.APPLIED

        scores_after = [o.tradeoff_score() for o in options]
        configs_after = [(o.disruption_cost, o.confidence, o.predicted_structural_risk_reduction) for o in options]
        assert scores_before == scores_after
        assert configs_before == configs_after


# ── Test 11: Deterministic scoring ────────────────────────────────────

class TestDeterministicScoring:
    def test_same_inputs_same_results(self):
        options = _options_bridge_top()
        decisions = [_decision_override() for _ in range(4)]
        outcomes = [_outcome(d) for d in decisions]
        opts_map = {d.id: options for d in decisions}

        proposal_1 = CalibrationProposal(
            id="p-det-1",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
        )
        proposal_2 = CalibrationProposal(
            id="p-det-2",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
        )

        r1, cr1 = run_regression_test(
            proposal=proposal_1,
            historical_decisions=decisions,
            historical_outcomes=outcomes,
            historical_options_by_decision_id=opts_map,
        )
        r2, cr2 = run_regression_test(
            proposal=proposal_2,
            historical_decisions=decisions,
            historical_outcomes=outcomes,
            historical_options_by_decision_id=opts_map,
        )

        assert r1.improved_count == r2.improved_count
        assert r1.regression_count == r2.regression_count
        assert r1.unchanged_count == r2.unchanged_count
        assert r1.inconclusive_count == r2.inconclusive_count
        assert r1.status == r2.status
        assert proposal_1.status == proposal_2.status

        for c1, c2 in zip(cr1, cr2):
            assert c1.classification == c2.classification
            assert c1.live_human_option_rank == c2.live_human_option_rank
            assert c1.proposal_human_option_rank == c2.proposal_human_option_rank


# ── Test 12: Regressed proposals remain diagnostically reviewable ─────

class TestRegressedDiagnostic:
    def test_regressed_proposal_has_evidence_bundle(self):
        options = _options_bridge_top()
        d_override = _decision_override()
        d_accepted = _decision_accepted()
        o_override = _outcome(d_override)
        o_accepted = _outcome(d_accepted, actual_risk_change=0.8)

        proposal = CalibrationProposal(
            id="p-diag",
            proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
            target_intervention_type="bridge_injection",
            proposed_delta=0.75,
        )

        report, case_results = run_regression_test(
            proposal=proposal,
            historical_decisions=[d_override, d_accepted],
            historical_outcomes=[o_override, o_accepted],
            historical_options_by_decision_id={
                d_override.id: options, d_accepted.id: options,
            },
            min_replayable_cases=2,
        )

        assert report.status == ProposalStatus.REGRESSION_DETECTED

        impact = build_historical_validation_impact(
            proposal=proposal, report=report, case_results=case_results,
        )
        bundle = bundle_calibration_evidence(
            proposal=proposal, report=report, impact=impact,
        )

        assert bundle.recommendation == EvidenceBundleRecommendation.REJECT_DUE_TO_REGRESSION
        assert proposal.status != ProposalStatus.APPLIED
        assert bundle.id
        assert bundle.explanation
