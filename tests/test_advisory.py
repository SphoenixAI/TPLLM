"""Tests for Commit 8 — Asymmetric Advisory & Outcome-Gated Evidence.

The advisory layer runs live and visible as a recommendation, never decides
anything, and earns trust by accumulating an honest, two-sided track record.

Zero live-ranker mutation. Zero governance-state mutation. Advisory only.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from lltpm.advisory import (
    AdvisoryEvaluation,
    AdvisoryOverlay,
    AdvisoryThreshold,
    EpistemicDelta,
    EvidenceDirection,
    GovernanceEvidenceEntry,
    HumanResponse,
    OverrideEvent,
    RefactorSignal,
    RefactorSignalStatus,
    RuleCalibrationLog,
    SurfacingDecision,
    VindicationRecord,
    VindicationState,
    build_rereview_evidence,
    compute_epistemic_delta,
    detect_refactor_signal,
    evaluate_advisory_surfacing,
    expire_vindication,
    is_advisory_eligible,
    open_vindication,
    record_human_response,
    resolve_vindication,
    update_calibration_log,
)
from lltpm.structural.governance import (
    ApprovalExpiryMode,
    CalibrationApprovalRecord,
    GovernanceStatus,
    ReviewTier,
    fingerprint_evidence_bundle,
    is_application_eligible,
)
from lltpm.structural.outcomes import DecisionOutcomeLog, DecisionOutcomeType
from lltpm.structural.stress_test import (
    ProposalScopeSignature,
    StressTestRecommendation,
    StressTestedEvidenceBundle,
)


# ── Fixtures ─────────────────────────────────────────────────────────

def _bundle(**kwargs) -> StressTestedEvidenceBundle:
    defaults = dict(
        id="steb-1",
        calibration_proposal_id="p-adv",
        base_evidence_bundle_id="ceb-1",
        scope_signature_id="scope-1",
        lateral_transfer_report_id="ltr-1",
        fuzz_resilience_report_id="frr-1",
        synthetic_stress_report_id="ssr-1",
        overfit_warning_ids=[],
        lateral_transfer_score=0.85,
        fuzz_resilience_score=0.80,
        synthetic_resilience_score=0.90,
        overall_stress_score=0.85,
        recommendation=StressTestRecommendation.PROMOTABLE_SCOPED_RULE,
    )
    defaults.update(kwargs)
    return StressTestedEvidenceBundle(**defaults)


def _approval(bundle=None, **kwargs) -> CalibrationApprovalRecord:
    if bundle is None:
        bundle = _bundle()
    fp = fingerprint_evidence_bundle(bundle)
    defaults = dict(
        proposal_id="p-adv",
        approved_by="reviewer-1",
        review_tier=ReviewTier.TIER_STANDARD,
        required_tier=ReviewTier.TIER_STANDARD,
        expiry_mode=ApprovalExpiryMode.BY_DATE,
        expires_at=datetime(2026, 12, 1),
        evidence_bundle_fingerprint=fp,
        status=GovernanceStatus.APPROVED,
    )
    defaults.update(kwargs)
    return CalibrationApprovalRecord(**defaults)


def _scope_sig(**kwargs) -> ProposalScopeSignature:
    defaults = dict(
        id="scope-1",
        proposal_id="p-adv",
        origin_team_id="backend",
        origin_domain_id="payments",
    )
    defaults.update(kwargs)
    return ProposalScopeSignature(**defaults)


def _threshold(**kwargs) -> AdvisoryThreshold:
    defaults = dict(
        interruption_threshold=0.10,
        min_bound=0.0,
        max_bound=1.0,
        sourced_from_approval="p-adv/fp-abc",
    )
    defaults.update(kwargs)
    return AdvisoryThreshold(**defaults)


_NOW = datetime(2026, 6, 15, 12, 0, 0)
_IN_SCOPE = {"team_id": "backend", "domain_id": "payments"}
_OUT_OF_SCOPE = {"team_id": "frontend", "domain_id": "marketing"}


def _surfacing_kwargs(bundle=None, record=None, **overrides):
    b = bundle or _bundle()
    r = record or _approval(bundle=b)
    defaults = dict(
        record=r,
        bundle=b,
        baseline_top_risk=0.80,
        calibrated_top_risk=0.50,
        rule_confidence=0.90,
        threshold=_threshold(),
        node_scope=_IN_SCOPE,
        scope_signature=_scope_sig(),
        now=_NOW,
        current_decision_count=0,
        baseline_rank=2,
        calibrated_rank=1,
        calibrated_top_option="opt-bridge",
        justification="Rule shifts top risk down.",
    )
    defaults.update(overrides)
    return defaults


# ── Test 1: Ineligible rule never surfaces ───────────────────────────

class TestIneligibleNeverSurfaces:
    def test_expired_approval_suppressed(self):
        b = _bundle()
        r = _approval(bundle=b, expires_at=datetime(2026, 1, 1))
        result = evaluate_advisory_surfacing(**_surfacing_kwargs(bundle=b, record=r))
        assert result.decision == SurfacingDecision.SUPPRESSED
        assert not result.advisory_eligible

    def test_wrong_tier_suppressed(self):
        b = _bundle()
        r = _approval(
            bundle=b,
            review_tier=ReviewTier.TIER_LIGHT,
            required_tier=ReviewTier.TIER_HEAVY,
        )
        result = evaluate_advisory_surfacing(**_surfacing_kwargs(bundle=b, record=r))
        assert result.decision == SurfacingDecision.SUPPRESSED

    def test_fingerprint_mismatch_suppressed(self):
        b = _bundle()
        r = _approval(bundle=b, evidence_bundle_fingerprint="wrong-fp")
        result = evaluate_advisory_surfacing(**_surfacing_kwargs(bundle=b, record=r))
        assert result.decision == SurfacingDecision.SUPPRESSED


# ── Test 2: Out-of-scope → SUPPRESSED ───────────────────────────────

class TestOutOfScope:
    def test_out_of_scope_suppressed(self):
        result = evaluate_advisory_surfacing(
            **_surfacing_kwargs(node_scope=_OUT_OF_SCOPE),
        )
        assert result.decision == SurfacingDecision.SUPPRESSED
        assert not result.in_scope

    def test_in_scope_eligible(self):
        eligible = is_advisory_eligible(
            record=_approval(),
            bundle=_bundle(),
            node_scope=_IN_SCOPE,
            scope_signature=_scope_sig(),
            now=_NOW,
            current_decision_count=0,
        )
        assert eligible is True


# ── Test 3: Advisory ≠ Application ───────────────────────────────────

class TestAdvisoryNotApplication:
    def test_advisory_eligible_does_not_imply_application(self):
        b = _bundle()
        r = _approval(bundle=b)

        adv = is_advisory_eligible(
            record=r,
            bundle=b,
            node_scope=_IN_SCOPE,
            scope_signature=_scope_sig(),
            now=_NOW,
            current_decision_count=0,
        )
        assert adv is True

        app = is_application_eligible(
            record=r,
            bundle=b,
            now=_NOW,
            current_decision_count=0,
        )
        assert app is True

        b_different = _bundle(id="steb-different")
        r_mismatch = _approval(bundle=b)

        adv2 = is_advisory_eligible(
            record=r_mismatch,
            bundle=b_different,
            node_scope=_IN_SCOPE,
            scope_signature=_scope_sig(),
            now=_NOW,
            current_decision_count=0,
        )
        assert adv2 is False

        app2 = is_application_eligible(
            record=r_mismatch,
            bundle=b_different,
            now=_NOW,
            current_decision_count=0,
        )
        assert app2 is False


# ── Test 4: Epistemic delta deterministic ────────────────────────────

class TestEpistemicDelta:
    def test_delta_is_abs_diff_times_confidence(self):
        ed = compute_epistemic_delta(
            baseline_top_risk=0.80,
            calibrated_top_risk=0.50,
            rule_confidence=0.90,
        )
        assert ed.delta == pytest.approx(0.30)
        assert ed.weighted_score == pytest.approx(0.27)
        assert ed.baseline_top_risk == 0.80
        assert ed.calibrated_top_risk == 0.50

    def test_symmetric_delta(self):
        ed1 = compute_epistemic_delta(
            baseline_top_risk=0.80,
            calibrated_top_risk=0.50,
            rule_confidence=0.90,
        )
        ed2 = compute_epistemic_delta(
            baseline_top_risk=0.50,
            calibrated_top_risk=0.80,
            rule_confidence=0.90,
        )
        assert ed1.delta == ed2.delta
        assert ed1.weighted_score == ed2.weighted_score


# ── Test 5: Small disagreement → SUPPRESSED ──────────────────────────

class TestSmallDisagreementSuppressed:
    def test_below_threshold_suppressed(self):
        result = evaluate_advisory_surfacing(
            **_surfacing_kwargs(
                baseline_top_risk=0.50,
                calibrated_top_risk=0.48,
                rule_confidence=0.90,
                threshold=_threshold(interruption_threshold=0.10),
            ),
        )
        assert result.decision == SurfacingDecision.SUPPRESSED
        assert result.advisory_eligible is True


# ── Test 6: Large disagreement → SURFACED ────────────────────────────

class TestLargeDisagreementSurfaced:
    def test_above_threshold_surfaced(self):
        result = evaluate_advisory_surfacing(**_surfacing_kwargs())
        assert result.decision == SurfacingDecision.SURFACED
        assert result.overlay is not None
        assert result.overlay.proposal_id == "p-adv"
        assert result.overlay.epistemic_delta.weighted_score > 0.10
        assert result.overlay.scope_signature_match is True


# ── Test 7: Governed threshold validation ────────────────────────────

class TestGovernedThreshold:
    def test_bare_float_rejected_by_type(self):
        with pytest.raises(Exception):
            evaluate_advisory_surfacing(
                **_surfacing_kwargs(threshold=0.10),  # type: ignore[arg-type]
            )

    def test_out_of_bounds_raises(self):
        with pytest.raises(ValueError, match="outside bounds"):
            AdvisoryThreshold(
                interruption_threshold=1.5,
                min_bound=0.0,
                max_bound=1.0,
                sourced_from_approval="p-adv/fp",
            )

    def test_empty_source_raises(self):
        with pytest.raises(ValueError, match="sourced_from_approval"):
            AdvisoryThreshold(
                interruption_threshold=0.5,
                min_bound=0.0,
                max_bound=1.0,
                sourced_from_approval="",
            )

    def test_valid_threshold_accepted(self):
        t = AdvisoryThreshold(
            interruption_threshold=0.5,
            min_bound=0.0,
            max_bound=1.0,
            sourced_from_approval="p-adv/fp-123",
        )
        assert t.interruption_threshold == 0.5


# ── Test 8: Concurrence — no override, no vindication ───────────────

class TestConcurrence:
    def test_concurrence_no_event(self):
        overlay = AdvisoryOverlay(
            proposal_id="p-adv",
            baseline_rank=2,
            calibrated_rank=1,
            calibrated_top_option="opt-bridge",
        )
        response, event = record_human_response(
            overlay=overlay,
            human_choice="opt-bridge",
            decision_id="d-1",
        )
        assert response == HumanResponse.CONCURRED
        assert event is None


# ── Test 9: Override → PENDING; rule status unchanged ────────────────

class TestOverrideNotRevocation:
    def test_override_opens_pending(self):
        b = _bundle()
        r = _approval(bundle=b)

        overlay = AdvisoryOverlay(
            proposal_id="p-adv",
            baseline_rank=2,
            calibrated_rank=1,
            calibrated_top_option="opt-bridge",
        )

        response, event = record_human_response(
            overlay=overlay,
            human_choice="opt-other",
            decision_id="d-1",
        )
        assert response == HumanResponse.OVERRIDDEN
        assert event is not None
        assert event.human_choice == "opt-other"

        vr = open_vindication(
            override_event=event,
            resolution_deadline=datetime(2026, 7, 15),
        )
        assert vr.state == VindicationState.PENDING_VINDICATION

        assert r.status == GovernanceStatus.APPROVED


# ── Test 10: RULE_VINDICATED on RISK_MATERIALIZED ────────────────────

class TestRuleVindicated:
    def test_risk_materialized_vindicates_rule(self):
        overlay = AdvisoryOverlay(
            proposal_id="p-adv",
            calibrated_top_option="opt-bridge",
        )
        _, event = record_human_response(
            overlay=overlay, human_choice="opt-other", decision_id="d-1",
        )
        vr = open_vindication(
            override_event=event,
            resolution_deadline=datetime(2026, 7, 15),
        )

        outcome = DecisionOutcomeLog(
            id="dol-1",
            decision_record_id="d-1",
            outcome_type=DecisionOutcomeType.RISK_MATERIALIZED,
            actual_risk_change=-0.3,
        )

        resolved, entry = resolve_vindication(
            vindication=vr,
            decision_outcome=outcome,
            now=_NOW,
        )

        assert resolved.state == VindicationState.RULE_VINDICATED
        assert resolved.evidence_direction == EvidenceDirection.FOR_RULE
        assert entry.direction == EvidenceDirection.FOR_RULE
        assert entry.outcome_label == "risk_materialized"


# ── Test 11: OVERRIDE_VINDICATED on RISK_RESOLVED ────────────────────

class TestOverrideVindicated:
    def test_risk_resolved_vindicates_override(self):
        overlay = AdvisoryOverlay(
            proposal_id="p-adv",
            calibrated_top_option="opt-bridge",
        )
        _, event = record_human_response(
            overlay=overlay, human_choice="opt-other", decision_id="d-1",
        )
        vr = open_vindication(
            override_event=event,
            resolution_deadline=datetime(2026, 7, 15),
        )

        outcome = DecisionOutcomeLog(
            id="dol-1",
            decision_record_id="d-1",
            outcome_type=DecisionOutcomeType.RISK_RESOLVED,
            actual_risk_change=0.5,
        )

        resolved, entry = resolve_vindication(
            vindication=vr,
            decision_outcome=outcome,
            now=_NOW,
        )

        assert resolved.state == VindicationState.OVERRIDE_VINDICATED
        assert resolved.evidence_direction == EvidenceDirection.FOR_HUMAN
        assert entry.direction == EvidenceDirection.FOR_HUMAN
        assert entry.outcome_label == "risk_resolved"


# ── Test 12: Resolution never mutates governance state ───────────────

class TestResolutionNoGovernanceMutation:
    def test_status_unchanged_after_resolution(self):
        b = _bundle()
        r = _approval(bundle=b)
        status_before = r.status
        tier_before = r.review_tier
        fp_before = r.evidence_bundle_fingerprint

        overlay = AdvisoryOverlay(
            proposal_id="p-adv",
            calibrated_top_option="opt-bridge",
        )
        _, event = record_human_response(
            overlay=overlay, human_choice="opt-other", decision_id="d-1",
        )
        vr = open_vindication(
            override_event=event,
            resolution_deadline=datetime(2026, 7, 15),
        )
        outcome = DecisionOutcomeLog(
            id="dol-1",
            decision_record_id="d-1",
            outcome_type=DecisionOutcomeType.RISK_MATERIALIZED,
            actual_risk_change=-0.3,
        )
        resolve_vindication(vindication=vr, decision_outcome=outcome, now=_NOW)

        assert r.status == status_before
        assert r.review_tier == tier_before
        assert r.evidence_bundle_fingerprint == fp_before


# ── Test 13: Deadline → UNRESOLVED_EXPIRED, NO_SIGNAL ────────────────

class TestDeadlineExpiry:
    def test_deadline_no_outcome(self):
        overlay = AdvisoryOverlay(
            proposal_id="p-adv",
            calibrated_top_option="opt-bridge",
        )
        _, event = record_human_response(
            overlay=overlay, human_choice="opt-other", decision_id="d-1",
        )
        vr = open_vindication(
            override_event=event,
            resolution_deadline=datetime(2026, 7, 15),
        )

        expired, entry = expire_vindication(vindication=vr, now=_NOW)

        assert expired.state == VindicationState.UNRESOLVED_EXPIRED
        assert expired.evidence_direction == EvidenceDirection.NO_SIGNAL
        assert entry.direction == EvidenceDirection.NO_SIGNAL
        assert entry.outcome_label == "deadline_expired"


# ── Test 14: Symmetric calibration log ───────────────────────────────

class TestSymmetricLog:
    def test_both_quadrants_increment(self):
        log = RuleCalibrationLog(proposal_id="p-adv")

        vindicated_vr = VindicationRecord(
            proposal_id="p-adv",
            decision_id="d-1",
            state=VindicationState.RULE_VINDICATED,
            evidence_direction=EvidenceDirection.FOR_RULE,
        )
        log = update_calibration_log(
            log=log,
            response=HumanResponse.OVERRIDDEN,
            vindication=vindicated_vr,
        )
        assert log.overridden_rule_vindicated == 1
        assert log.overridden_override_vindicated == 0

        wolf_vr = VindicationRecord(
            proposal_id="p-adv",
            decision_id="d-2",
            state=VindicationState.OVERRIDE_VINDICATED,
            evidence_direction=EvidenceDirection.FOR_HUMAN,
        )
        log = update_calibration_log(
            log=log,
            response=HumanResponse.OVERRIDDEN,
            vindication=wolf_vr,
        )
        assert log.overridden_rule_vindicated == 1
        assert log.overridden_override_vindicated == 1

        assert log.cry_wolf_rate == pytest.approx(0.5)
        assert log.vindication_rate == pytest.approx(0.5)

    def test_cry_wolf_rate_zero_div_safe(self):
        log = RuleCalibrationLog(proposal_id="p-adv")
        assert log.cry_wolf_rate == 0.0
        assert log.vindication_rate == 0.0

    def test_concurrence_increments_good(self):
        log = RuleCalibrationLog(proposal_id="p-adv")
        log = update_calibration_log(
            log=log,
            response=HumanResponse.CONCURRED,
        )
        assert log.concurred_good == 1

    def test_pending_override_increments_pending(self):
        log = RuleCalibrationLog(proposal_id="p-adv")
        pending_vr = VindicationRecord(
            proposal_id="p-adv",
            decision_id="d-1",
            state=VindicationState.PENDING_VINDICATION,
        )
        log = update_calibration_log(
            log=log,
            response=HumanResponse.OVERRIDDEN,
            vindication=pending_vr,
        )
        assert log.pending_count == 1

    def test_unresolved_increments_unresolved(self):
        log = RuleCalibrationLog(proposal_id="p-adv")
        expired_vr = VindicationRecord(
            proposal_id="p-adv",
            decision_id="d-1",
            state=VindicationState.UNRESOLVED_EXPIRED,
        )
        log = update_calibration_log(
            log=log,
            response=HumanResponse.OVERRIDDEN,
            vindication=expired_vr,
        )
        assert log.unresolved_count == 1


# ── Test 15: Refactor signal proposed, never auto-filed ──────────────

class TestRefactorSignal:
    def test_fires_above_threshold(self):
        log = RuleCalibrationLog(proposal_id="p-adv", surfaced_count=15)
        signal = detect_refactor_signal(log=log, window=30, fire_threshold=10)
        assert signal is not None
        assert signal.status == RefactorSignalStatus.PROPOSED
        assert signal.fire_count == 15

    def test_below_threshold_no_signal(self):
        log = RuleCalibrationLog(proposal_id="p-adv", surfaced_count=5)
        signal = detect_refactor_signal(log=log, window=30, fire_threshold=10)
        assert signal is None


# ── Test 16: Rereview evidence has both directions ───────────────────

class TestRereviewEvidence:
    def test_both_directions_present(self):
        log = RuleCalibrationLog(
            proposal_id="p-adv",
            surfaced_count=10,
            overridden_rule_vindicated=3,
            overridden_override_vindicated=2,
            pending_count=1,
            unresolved_count=1,
        )

        entries = [
            GovernanceEvidenceEntry(
                proposal_id="p-adv",
                decision_id="d-1",
                direction=EvidenceDirection.FOR_RULE,
                outcome_label="risk_materialized",
            ),
            GovernanceEvidenceEntry(
                proposal_id="p-adv",
                decision_id="d-2",
                direction=EvidenceDirection.FOR_HUMAN,
                outcome_label="risk_resolved",
            ),
            GovernanceEvidenceEntry(
                proposal_id="p-adv",
                decision_id="d-3",
                direction=EvidenceDirection.NO_SIGNAL,
                outcome_label="risk_unchanged",
            ),
        ]

        evidence = build_rereview_evidence(
            proposal_id="p-adv",
            journal_entries=entries,
            calibration_log=log,
        )

        assert evidence["vindicated_count"] == 3
        assert evidence["cry_wolf_count"] == 2
        assert evidence["cry_wolf_rate"] == pytest.approx(0.4)
        assert evidence["vindication_rate"] == pytest.approx(0.6)
        assert evidence["pending_count"] == 1
        assert len(evidence["for_rule_entries"]) == 1
        assert len(evidence["for_human_entries"]) == 1
        assert len(evidence["no_signal_entries"]) == 1
        assert evidence["total_evidence_entries"] == 3


# ── Test 17: No live-ranker mutation ─────────────────────────────────

class TestNoLiveRankerMutation:
    def test_advisory_source_has_no_mutation_literals(self):
        import inspect
        import lltpm.advisory.surfacing as surf
        import lltpm.advisory.vindication as vind
        import lltpm.advisory.calibration_log as clog

        forbidden = [
            "tradeoff_score =",
            "apply_to_live_ranker",
            ".apply_calibration(",
            "rank_options(",
            "graph.remove_",
            "graph.add_",
        ]

        for mod in [surf, vind, clog]:
            source = inspect.getsource(mod)
            for pattern in forbidden:
                assert pattern not in source, (
                    f"{mod.__name__} must not contain '{pattern}' — "
                    f"advisory is payload-only, zero live-ranker mutation."
                )


# ── Test 18: No governance-state mutation ────────────────────────────

class TestNoGovernanceStateMutation:
    def test_advisory_never_calls_authority_mutators(self):
        import inspect
        import lltpm.advisory.surfacing as surf
        import lltpm.advisory.vindication as vind
        import lltpm.advisory.calibration_log as clog

        forbidden = [
            "transition_status(",
            "issue_approval(",
            "renew_approval(",
            "record.status =",
            ".status = GovernanceStatus.",
            "confidence =",
            "auto_revoke",
            "auto_promote",
            "auto_downgrade",
            "auto_harden",
        ]

        for mod in [surf, vind, clog]:
            source = inspect.getsource(mod)
            for pattern in forbidden:
                assert pattern not in source, (
                    f"{mod.__name__} must not contain '{pattern}' — "
                    f"advisory writes evidence only, never authority."
                )
