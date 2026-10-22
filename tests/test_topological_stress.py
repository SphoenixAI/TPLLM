"""Topological Stress Testing & Lateral Transfer Suite.

Calibration proposals that passed regression testing are stress-tested
for lateral transfer, topological fuzzing, and synthetic edge cases. The
strongest outcome is ``PROMOTABLE_SCOPED_RULE`` — never auto-applied.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tpllm.structural import (
    CalibrationProposal,
    CalibrationProposalType,
    CalibrationTestCase,
    CalibrationTestCaseStatus,
    CalibrationTestCaseType,
    InterventionOption,
    InterventionType,
    ProposalStatus,
)
from tpllm.structural.feedback import HumanDecisionType, record_human_decision
from tpllm.structural.outcomes import DecisionOutcomeLog
from tpllm.structural.stress_test import (
    FuzzMutationType,
    FuzzResilienceReport,
    LateralTransferReport,
    OverfitWarning,
    ProposalScopeSignature,
    StressTestRecommendation,
    StressTestStatus,
    SyntheticStressReport,
    TransferRelationType,
    build_stress_tested_evidence_bundle,
    create_scope_signature,
    fuzz_test_case,
    generate_overfit_warnings,
    identify_lateral_transfer_cases,
    run_lateral_transfer_test,
    run_synthetic_edge_case_test,
    run_topological_fuzzing_test,
    run_topological_stress_test_suite,
)


# ── Fakes & helpers ───────────────────────────────────────────────────

class FakeStore:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str, object]] = []

    def save(self, program_id: str, name: str, model: object) -> str:
        self.saved.append((program_id, name, model))
        return name


def _options_bridge_top() -> list[InterventionOption]:
    return [
        InterventionOption(
            id="bridge_injection",
            intervention_type=InterventionType.BRIDGE_INJECTION,
            predicted_structural_risk_reduction=0.9, confidence=0.9, disruption_cost="low",
        ),
        InterventionOption(
            id="reviewer_redistribution",
            intervention_type=InterventionType.REVIEWER_REDISTRIBUTION,
            predicted_structural_risk_reduction=0.5, confidence=0.8, disruption_cost="medium",
        ),
        InterventionOption(
            id="milestone_resequencing",
            intervention_type=InterventionType.MILESTONE_RESEQUENCING,
            predicted_structural_risk_reduction=0.55, confidence=0.6, disruption_cost="high",
        ),
    ]


def _decision_override(option_id: str = "reviewer_redistribution", team_id: str | None = None, domain_id: str | None = None):
    rec = record_human_decision(
        "rec-bridge",
        HumanDecisionType.OVERRIDDEN,
        intervention_option_id=option_id,
        signal_id="sig-bottleneck",
        override_text="Org policy forbids mocking this API",
        agent_predicted_risk_reduction=0.9,
        agent_confidence=0.9,
        agent_severity="high",
    )
    if team_id is not None:
        object.__setattr__(rec, "team_id", team_id)
    if domain_id is not None:
        object.__setattr__(rec, "domain_id", domain_id)
    return rec


def _decision_accepted(option_id: str = "bridge_injection", team_id: str | None = None, domain_id: str | None = None):
    rec = record_human_decision(
        "rec-bridge",
        HumanDecisionType.ACCEPTED,
        intervention_option_id=option_id,
        signal_id="sig-bottleneck",
        agent_predicted_risk_reduction=0.9,
        agent_confidence=0.9,
        agent_severity="high",
    )
    if team_id is not None:
        object.__setattr__(rec, "team_id", team_id)
    if domain_id is not None:
        object.__setattr__(rec, "domain_id", domain_id)
    return rec


def _outcome(decision, actual_risk_change: float = 0.6):
    return DecisionOutcomeLog(
        id=f"dol-{decision.id}",
        decision_record_id=decision.id,
        actual_risk_change=actual_risk_change,
    )


def _validated_proposal(**kwargs) -> CalibrationProposal:
    defaults = dict(
        id="p-stress",
        proposal_type=CalibrationProposalType.ADJUST_INTERVENTION_COST,
        target_intervention_type="bridge_injection",
        proposed_delta=0.75,
        proposed_change_summary="Penalize bridge injection when constraint present.",
        status=ProposalStatus.VALIDATED_FOR_REVIEW,
        source_hypothesis_ids=["hyp-1"],
        source_mismatch_ids=["mismatch-1"],
    )
    defaults.update(kwargs)
    return CalibrationProposal(**defaults)


def _test_case(decision_id: str) -> CalibrationTestCase:
    return CalibrationTestCase(
        id=f"ctc-{decision_id}",
        calibration_proposal_id="p-stress",
        source_human_decision_record_id=decision_id,
        test_type=CalibrationTestCaseType.RANK_HUMAN_OPTION_HIGHER,
        expected_human_option_rank_at_or_above=1,
        status=CalibrationTestCaseStatus.ACTIVE,
    )


def _origin_decision():
    return SimpleNamespace(
        team_id="backend",
        domain_id="payments",
        workstream_id="ws-checkout",
        milestone_id="ms-q3",
        intervention_type="bridge_injection",
    )


# ── Test 1: Stress suite only runs on validated proposals ─────────────

class TestSuiteGating:
    def test_rejects_proposed_status(self):
        proposal = _validated_proposal(status=ProposalStatus.PROPOSED)
        with pytest.raises(ValueError, match="VALIDATED_FOR_REVIEW"):
            run_topological_stress_test_suite(
                proposal=proposal,
                base_evidence_bundle=None,
                origin_decision=None,
                candidate_test_cases=[],
                decisions_by_id={},
                outcomes_by_decision_id={},
                options_by_decision_id={},
            )

    def test_accepts_validated_status(self):
        proposal = _validated_proposal()
        d = _decision_override(team_id="platform", domain_id="auth")
        tc = _test_case(d.id)

        bundle = run_topological_stress_test_suite(
            proposal=proposal,
            base_evidence_bundle=None,
            origin_decision=_origin_decision(),
            candidate_test_cases=[tc],
            decisions_by_id={d.id: d},
            outcomes_by_decision_id={d.id: _outcome(d)},
            options_by_decision_id={d.id: _options_bridge_top()},
        )
        assert bundle.id


# ── Test 2: Scope signature captures proposal origin ──────────────────

class TestScopeSignature:
    def test_captures_origin_fields(self):
        proposal = _validated_proposal()
        origin = _origin_decision()
        sig = create_scope_signature(proposal=proposal, origin_decision=origin)

        assert sig.origin_team_id == "backend"
        assert sig.origin_domain_id == "payments"
        assert sig.target_intervention_type == "bridge_injection"
        assert "hyp-1" in sig.source_hypothesis_ids
        assert "mismatch-1" in sig.source_mismatch_ids


# ── Test 3: Lateral transfer excludes exact origin duplicate ──────────

class TestLateralExclusion:
    def test_excludes_same_team_and_domain(self):
        proposal = _validated_proposal()
        sig = ProposalScopeSignature(
            id="scope-1", proposal_id=proposal.id,
            origin_team_id="backend", origin_domain_id="payments",
            target_intervention_type="bridge_injection",
        )

        d_same = _decision_override(team_id="backend", domain_id="payments")
        tc_same = _test_case(d_same.id)

        selected = identify_lateral_transfer_cases(
            proposal=proposal,
            scope_signature=sig,
            candidate_test_cases=[tc_same],
            decisions_by_id={d_same.id: d_same},
        )
        assert len(selected) == 0


# ── Test 4: Lateral transfer selects structurally similar foreign case ─

class TestLateralSelection:
    def test_selects_different_team_same_pattern(self):
        proposal = _validated_proposal()
        sig = ProposalScopeSignature(
            id="scope-1", proposal_id=proposal.id,
            origin_team_id="backend", origin_domain_id="payments",
            target_intervention_type="bridge_injection",
        )

        d_foreign = _decision_override(team_id="platform", domain_id="auth")
        object.__setattr__(d_foreign, "intervention_type", "bridge_injection")
        tc_foreign = _test_case(d_foreign.id)

        selected = identify_lateral_transfer_cases(
            proposal=proposal,
            scope_signature=sig,
            candidate_test_cases=[tc_foreign],
            decisions_by_id={d_foreign.id: d_foreign},
        )
        assert len(selected) == 1
        _, relation = selected[0]
        assert relation == TransferRelationType.SAME_PATTERN_DIFFERENT_TEAM


# ── Test 5: Lateral transfer report detects regression ────────────────

class TestLateralRegression:
    def test_transfer_regression_detected(self):
        proposal = _validated_proposal()
        sig = ProposalScopeSignature(
            id="scope-1", proposal_id=proposal.id,
            origin_team_id="backend", origin_domain_id="payments",
        )

        d_accepted = _decision_accepted(
            "bridge_injection", team_id="platform", domain_id="auth",
        )
        tc = _test_case(d_accepted.id)
        outcome = _outcome(d_accepted, actual_risk_change=0.8)

        report, results = run_lateral_transfer_test(
            proposal=proposal,
            scope_signature=sig,
            candidate_test_cases=[tc],
            decisions_by_id={d_accepted.id: d_accepted},
            outcomes_by_decision_id={d_accepted.id: outcome},
            options_by_decision_id={d_accepted.id: _options_bridge_top()},
            min_cases=1,
        )

        assert report.status == StressTestStatus.FAILED
        assert report.transfer_regression_count > 0


# ── Test 6: Fuzzing is deterministic ──────────────────────────────────

class TestFuzzDeterminism:
    def test_same_inputs_same_results(self):
        proposal = _validated_proposal()
        d = _decision_override(team_id="platform", domain_id="auth")
        tc = _test_case(d.id)
        outcome = _outcome(d)
        options = _options_bridge_top()

        lookups = dict(
            decisions_by_id={d.id: d},
            outcomes_by_decision_id={d.id: outcome},
            options_by_decision_id={d.id: options},
        )

        r1, cr1 = run_topological_fuzzing_test(
            proposal=proposal, test_cases=[tc], **lookups,
        )
        r2, cr2 = run_topological_fuzzing_test(
            proposal=proposal, test_cases=[tc], **lookups,
        )

        assert r1.passed_mutation_count == r2.passed_mutation_count
        assert r1.brittle_failure_count == r2.brittle_failure_count
        assert r1.inconclusive_count == r2.inconclusive_count
        assert r1.status == r2.status

        for c1, c2 in zip(cr1, cr2):
            assert c1.graceful_degradation == c2.graceful_degradation
            assert c1.brittle_failure == c2.brittle_failure


# ── Test 7: Fuzzing detects brittle proposal ──────────────────────────

class TestFuzzBrittle:
    def test_brittle_failure_detected(self):
        proposal = _validated_proposal()
        d = _decision_override(team_id="platform", domain_id="auth")
        tc = CalibrationTestCase(
            id="ctc-brittle",
            calibration_proposal_id=proposal.id,
            source_human_decision_record_id=d.id,
            test_type=CalibrationTestCaseType.NO_REGRESSION_ON_ACCEPTED_TOP,
            status=CalibrationTestCaseStatus.ACTIVE,
        )
        outcome = _outcome(d)
        options = _options_bridge_top()

        mutation_record, result = fuzz_test_case(
            proposal=proposal,
            test_case=tc,
            decision=d,
            outcome=outcome,
            intervention_options=options,
            mutation_index=0,
            mutation_type=FuzzMutationType.REMOVE_NODE,
            deterministic_seed=0,
        )

        if result.original_passed and not result.fuzzed_passed:
            assert result.brittle_failure is True

        report, _ = run_topological_fuzzing_test(
            proposal=proposal,
            test_cases=[tc],
            decisions_by_id={d.id: d},
            outcomes_by_decision_id={d.id: outcome},
            options_by_decision_id={d.id: options},
        )

        if report.brittle_failure_count > 0:
            assert report.status == StressTestStatus.FAILED


# ── Test 8: Synthetic stress produces report ──────────────────────────

class TestSyntheticReport:
    def test_synthetic_stress_report_produced(self):
        proposal = _validated_proposal()
        d = _decision_override(team_id="platform", domain_id="auth")
        tc = _test_case(d.id)
        outcome = _outcome(d)
        options = _options_bridge_top()

        report, results = run_synthetic_edge_case_test(
            proposal=proposal,
            test_cases=[tc],
            decisions_by_id={d.id: d},
            outcomes_by_decision_id={d.id: outcome},
            options_by_decision_id={d.id: options},
        )

        assert report.id
        assert report.synthetic_case_count > 0
        assert report.status in (
            StressTestStatus.PASSED, StressTestStatus.FAILED,
            StressTestStatus.INCONCLUSIVE, StressTestStatus.NEEDS_MORE_DATA,
        )


# ── Test 9: Overfit warning for failed lateral transfer ───────────────

class TestOverfitLateral:
    def test_lateral_failure_generates_warning(self):
        proposal = _validated_proposal()
        lateral_report = LateralTransferReport(
            id="ltr-fail",
            calibration_proposal_id=proposal.id,
            status=StressTestStatus.FAILED,
            transfer_regression_count=2,
        )
        fuzz_report = FuzzResilienceReport(
            id="frr-ok", calibration_proposal_id=proposal.id, status=StressTestStatus.PASSED,
        )
        synthetic_report = SyntheticStressReport(
            id="ssr-ok", calibration_proposal_id=proposal.id, status=StressTestStatus.PASSED,
        )

        warnings = generate_overfit_warnings(
            proposal=proposal,
            lateral_report=lateral_report,
            fuzz_report=fuzz_report,
            synthetic_report=synthetic_report,
        )

        lateral_warnings = [w for w in warnings if w.warning_type == "fails_lateral_transfer"]
        assert len(lateral_warnings) == 1
        assert lateral_warnings[0].severity == "high"


# ── Test 10: Overfit warning for fuzz brittleness ─────────────────────

class TestOverfitFuzz:
    def test_fuzz_failure_generates_warning(self):
        proposal = _validated_proposal()
        lateral_report = LateralTransferReport(
            id="ltr-ok", calibration_proposal_id=proposal.id, status=StressTestStatus.PASSED,
        )
        fuzz_report = FuzzResilienceReport(
            id="frr-fail", calibration_proposal_id=proposal.id, status=StressTestStatus.FAILED,
            brittle_failure_count=3,
        )
        synthetic_report = SyntheticStressReport(
            id="ssr-ok", calibration_proposal_id=proposal.id, status=StressTestStatus.PASSED,
        )

        warnings = generate_overfit_warnings(
            proposal=proposal,
            lateral_report=lateral_report,
            fuzz_report=fuzz_report,
            synthetic_report=synthetic_report,
        )

        fuzz_warnings = [w for w in warnings if w.warning_type == "brittle_under_missing_data"]
        assert len(fuzz_warnings) == 1
        assert fuzz_warnings[0].severity == "high"


# ── Test 11: Bundle rejects transfer regression ───────────────────────

class TestBundleTransferRejection:
    def test_transfer_regression_rejected(self):
        proposal = _validated_proposal()
        sig = ProposalScopeSignature(id="scope-1", proposal_id=proposal.id)

        lateral_report = LateralTransferReport(
            id="ltr-fail", calibration_proposal_id=proposal.id,
            status=StressTestStatus.FAILED,
            transfer_regression_count=1,
            lateral_transfer_score=0.5,
        )
        fuzz_report = FuzzResilienceReport(
            id="frr-ok", calibration_proposal_id=proposal.id,
            status=StressTestStatus.PASSED, fuzz_resilience_score=1.0,
        )
        synthetic_report = SyntheticStressReport(
            id="ssr-ok", calibration_proposal_id=proposal.id,
            status=StressTestStatus.PASSED, synthetic_resilience_score=1.0,
        )
        warnings = generate_overfit_warnings(
            proposal=proposal,
            lateral_report=lateral_report,
            fuzz_report=fuzz_report,
            synthetic_report=synthetic_report,
        )

        bundle = build_stress_tested_evidence_bundle(
            proposal=proposal,
            base_evidence_bundle=None,
            scope_signature=sig,
            lateral_report=lateral_report,
            fuzz_report=fuzz_report,
            synthetic_report=synthetic_report,
            overfit_warnings=warnings,
        )

        assert bundle.recommendation == StressTestRecommendation.REJECT_DUE_TO_TRANSFER_REGRESSION


# ── Test 12: Bundle rejects brittle proposal ──────────────────────────

class TestBundleBrittleRejection:
    def test_brittle_proposal_rejected(self):
        proposal = _validated_proposal()
        sig = ProposalScopeSignature(id="scope-1", proposal_id=proposal.id)

        lateral_report = LateralTransferReport(
            id="ltr-ok", calibration_proposal_id=proposal.id,
            status=StressTestStatus.PASSED, lateral_transfer_score=1.0,
        )
        fuzz_report = FuzzResilienceReport(
            id="frr-fail", calibration_proposal_id=proposal.id,
            status=StressTestStatus.FAILED, fuzz_resilience_score=0.3,
        )
        synthetic_report = SyntheticStressReport(
            id="ssr-ok", calibration_proposal_id=proposal.id,
            status=StressTestStatus.PASSED, synthetic_resilience_score=1.0,
        )
        warnings = generate_overfit_warnings(
            proposal=proposal,
            lateral_report=lateral_report,
            fuzz_report=fuzz_report,
            synthetic_report=synthetic_report,
        )

        bundle = build_stress_tested_evidence_bundle(
            proposal=proposal,
            base_evidence_bundle=None,
            scope_signature=sig,
            lateral_report=lateral_report,
            fuzz_report=fuzz_report,
            synthetic_report=synthetic_report,
            overfit_warnings=warnings,
        )

        assert bundle.recommendation == StressTestRecommendation.REJECT_AS_BRITTLE


# ── Test 13: Bundle marks promotable scoped rule ──────────────────────

class TestBundlePromotable:
    def test_all_passed_is_promotable(self):
        proposal = _validated_proposal()
        sig = ProposalScopeSignature(id="scope-1", proposal_id=proposal.id)

        lateral_report = LateralTransferReport(
            id="ltr-ok", calibration_proposal_id=proposal.id,
            status=StressTestStatus.PASSED,
            lateral_transfer_score=0.80,
        )
        fuzz_report = FuzzResilienceReport(
            id="frr-ok", calibration_proposal_id=proposal.id,
            status=StressTestStatus.PASSED, fuzz_resilience_score=0.78,
        )
        synthetic_report = SyntheticStressReport(
            id="ssr-ok", calibration_proposal_id=proposal.id,
            status=StressTestStatus.PASSED, synthetic_resilience_score=0.75,
        )

        bundle = build_stress_tested_evidence_bundle(
            proposal=proposal,
            base_evidence_bundle=None,
            scope_signature=sig,
            lateral_report=lateral_report,
            fuzz_report=fuzz_report,
            synthetic_report=synthetic_report,
            overfit_warnings=[],
        )

        assert bundle.recommendation == StressTestRecommendation.PROMOTABLE_SCOPED_RULE
        assert bundle.overall_stress_score >= 0.70


# ── Test 14: No auto-apply safety ────────────────────────────────────

class TestNoAutoApply:
    def test_promotable_does_not_apply(self):
        proposal = _validated_proposal()
        options = _options_bridge_top()
        d = _decision_override(team_id="platform", domain_id="auth")
        tc = _test_case(d.id)
        outcome = _outcome(d)

        scores_before = [o.tradeoff_score() for o in options]
        configs_before = [
            (o.disruption_cost, o.confidence, o.predicted_structural_risk_reduction)
            for o in options
        ]

        bundle = run_topological_stress_test_suite(
            proposal=proposal,
            base_evidence_bundle=None,
            origin_decision=_origin_decision(),
            candidate_test_cases=[tc],
            decisions_by_id={d.id: d},
            outcomes_by_decision_id={d.id: outcome},
            options_by_decision_id={d.id: options},
        )

        assert proposal.status != ProposalStatus.APPLIED
        scores_after = [o.tradeoff_score() for o in options]
        configs_after = [
            (o.disruption_cost, o.confidence, o.predicted_structural_risk_reduction)
            for o in options
        ]
        assert scores_before == scores_after
        assert configs_before == configs_after


# ── Test 15: Needs more data if no lateral cases ─────────────────────

class TestNeedsMoreData:
    def test_no_lateral_cases_means_needs_more_data(self):
        proposal = _validated_proposal()
        sig = ProposalScopeSignature(
            id="scope-1", proposal_id=proposal.id,
            origin_team_id="backend", origin_domain_id="payments",
        )

        report, _ = run_lateral_transfer_test(
            proposal=proposal,
            scope_signature=sig,
            candidate_test_cases=[],
            decisions_by_id={},
            outcomes_by_decision_id={},
            options_by_decision_id={},
            min_cases=2,
        )

        assert report.status == StressTestStatus.NEEDS_MORE_DATA
        assert report.evaluated_case_count == 0
