"""Tests for Commit 9 — Bounded Autonomous Application & Auto-Revert (rung three).

The only layer permitted to mutate. A governed-approved calibration becomes
active and actually changes which intervention is chosen — guarded by the
single green light, a kill switch, bounded autonomy, mandatory reversibility,
governed outcome monitoring, and removal-only auto-revert.

It never grants governance authority and never edits the live ranker source.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from lltpm.application import (
    ActiveCalibrationRecord,
    ApplicationDecision,
    ApplicationLedger,
    ApplicationOutcomeWindow,
    ApplicationStatus,
    AutonomyBudget,
    BlockReason,
    KillSwitch,
    KillSwitchState,
    LedgerEntryType,
    MonitorVerdict,
    RegressionThreshold,
    RevertTrigger,
    append_application,
    append_revert,
    apply_calibration,
    arm_kill_switch,
    auto_revert_if_regressing,
    build_application_audit,
    can_apply,
    engage_kill_switch,
    evaluate_application_health,
    record_application_outcome,
    revert_application,
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
    StressTestRecommendation,
    StressTestedEvidenceBundle,
)


# ── Fixtures ─────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 15, 12, 0, 0)


def _bundle(**kwargs) -> StressTestedEvidenceBundle:
    defaults = dict(
        id="steb-1",
        calibration_proposal_id="p-app",
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
        proposal_id="p-app",
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


def _budget(**kwargs) -> AutonomyBudget:
    defaults = dict(
        max_active_applications=1,
        max_applications_per_window=2,
        window_decisions=50,
        sourced_from_approval="p-app/fp-abc",
    )
    defaults.update(kwargs)
    return AutonomyBudget(**defaults)


def _kill_switch() -> KillSwitch:
    return KillSwitch(state=KillSwitchState.ARMED)


def _threshold(**kwargs) -> RegressionThreshold:
    defaults = dict(
        max_regression_rate=0.40,
        min_samples=3,
        sourced_from_approval="p-app/fp-abc",
    )
    defaults.update(kwargs)
    return RegressionThreshold(**defaults)


def _outcome(outcome_type: DecisionOutcomeType, did="d-1") -> DecisionOutcomeLog:
    return DecisionOutcomeLog(
        id=f"dol-{did}",
        decision_record_id=did,
        outcome_type=outcome_type,
        actual_risk_change=0.0,
    )


def _apply(**overrides):
    kwargs = dict(
        record=_approval(),
        bundle=_bundle(),
        kill_switch=_kill_switch(),
        budget=_budget(),
        now=_NOW,
        current_decision_count=0,
    )
    kwargs.update(overrides)
    return apply_calibration(**kwargs)


# ── Test 1: Application requires the single green light ──────────────

class TestRequiresGreenLight:
    def test_ineligible_blocked(self):
        b = _bundle()
        r = _approval(bundle=b, expires_at=datetime(2026, 1, 1))  # expired
        result, budget = _apply(record=r, bundle=b)
        assert result.decision == ApplicationDecision.BLOCKED
        assert result.block_reason == BlockReason.NOT_APPLICATION_ELIGIBLE
        assert result.active_record is None

    def test_eligible_applied(self):
        result, budget = _apply()
        assert result.decision == ApplicationDecision.APPLIED
        assert result.active_record is not None
        assert result.active_record.status == ApplicationStatus.ACTIVE

    def test_blocked_leaves_budget_unchanged(self):
        b = _bundle()
        r = _approval(bundle=b, evidence_bundle_fingerprint="wrong")
        start = _budget()
        result, budget = _apply(record=r, bundle=b, budget=start)
        assert result.decision == ApplicationDecision.BLOCKED
        assert budget.current_active == start.current_active
        assert budget.applied_in_window == start.applied_in_window


# ── Test 2: Kill switch halts all application; manual override wins ──

class TestKillSwitch:
    def test_engaged_blocks_eligible_application(self):
        ks = engage_kill_switch(
            kill_switch=_kill_switch(),
            engaged_by="oncall",
            reason="incident",
            now=_NOW,
        )
        result, budget = _apply(kill_switch=ks)
        assert result.decision == ApplicationDecision.BLOCKED
        assert result.block_reason == BlockReason.KILL_SWITCH_ENGAGED

    def test_kill_switch_wins_over_eligibility(self):
        # Even a perfectly eligible rule is blocked.
        ks = engage_kill_switch(
            kill_switch=_kill_switch(),
            engaged_by="oncall",
            reason="freeze",
            now=_NOW,
        )
        assert is_application_eligible(
            record=_approval(), bundle=_bundle(),
            now=_NOW, current_decision_count=0,
        ) is True
        result, _ = _apply(kill_switch=ks)
        assert result.decision == ApplicationDecision.BLOCKED

    def test_rearm_releases(self):
        ks = engage_kill_switch(
            kill_switch=_kill_switch(),
            engaged_by="oncall", reason="x", now=_NOW,
        )
        ks2 = arm_kill_switch(kill_switch=ks)
        assert ks2.state == KillSwitchState.ARMED
        result, _ = _apply(kill_switch=ks2)
        assert result.decision == ApplicationDecision.APPLIED


# ── Test 3: Bounded autonomy ─────────────────────────────────────────

class TestBoundedAutonomy:
    def test_budget_exhausted_blocks(self):
        full = _budget(max_active_applications=1, current_active=1)
        result, budget = _apply(budget=full)
        assert result.decision == ApplicationDecision.BLOCKED
        assert result.block_reason == BlockReason.AUTONOMY_BUDGET_EXHAUSTED

    def test_window_cap_blocks(self):
        capped = _budget(max_applications_per_window=1, applied_in_window=1)
        result, budget = _apply(budget=capped)
        assert result.decision == ApplicationDecision.BLOCKED
        assert result.block_reason == BlockReason.AUTONOMY_BUDGET_EXHAUSTED

    def test_apply_consumes_budget(self):
        start = _budget()
        result, budget = _apply(budget=start)
        assert result.decision == ApplicationDecision.APPLIED
        assert budget.current_active == start.current_active + 1
        assert budget.applied_in_window == start.applied_in_window + 1

    def test_budget_rejects_nonpositive(self):
        with pytest.raises(ValueError, match="max_active_applications"):
            AutonomyBudget(max_active_applications=0, sourced_from_approval="x")

    def test_budget_requires_source(self):
        with pytest.raises(ValueError, match="sourced_from_approval"):
            AutonomyBudget(sourced_from_approval="")


# ── Test 4: Already-active blocks duplicate application ──────────────

class TestAlreadyActive:
    def test_already_active_blocked(self):
        result, budget = _apply(already_active=True)
        assert result.decision == ApplicationDecision.BLOCKED
        assert result.block_reason == BlockReason.ALREADY_ACTIVE


# ── Test 5: Reversibility snapshot captured before mutation ──────────

class TestReversibility:
    def test_snapshot_captured(self):
        result, _ = _apply()
        assert result.active_record is not None
        snap = result.active_record.reversibility_snapshot
        assert snap.proposal_id == "p-app"
        assert snap.captured_at == _NOW

    def test_snapshot_records_prior_calibration(self):
        result, _ = _apply(
            prior_active_calibration_id="active-old",
        )
        # prior id given but not already_active => applied, snapshot restores prior
        assert result.decision == ApplicationDecision.APPLIED
        snap = result.active_record.reversibility_snapshot
        assert snap.prior_active_calibration_id == "active-old"


# ── Test 6: can_apply composes gates in correct order ───────────────

class TestCanApplyOrder:
    def test_kill_switch_checked_before_eligibility(self):
        ks = engage_kill_switch(
            kill_switch=_kill_switch(), engaged_by="x", reason="y", now=_NOW,
        )
        # Even with an ineligible record, kill switch reason is returned first.
        bad = _approval(expires_at=datetime(2026, 1, 1))
        allowed, reason, _ = can_apply(
            record=bad, bundle=_bundle(), kill_switch=ks,
            budget=_budget(), now=_NOW, current_decision_count=0,
        )
        assert allowed is False
        assert reason == BlockReason.KILL_SWITCH_ENGAGED

    def test_all_pass(self):
        allowed, reason, _ = can_apply(
            record=_approval(), bundle=_bundle(), kill_switch=_kill_switch(),
            budget=_budget(), now=_NOW, current_decision_count=0,
        )
        assert allowed is True
        assert reason is None


# ── Test 7: Governed regression threshold ───────────────────────────

class TestGovernedThreshold:
    def test_out_of_bounds_raises(self):
        with pytest.raises(ValueError, match="outside bounds"):
            RegressionThreshold(
                max_regression_rate=1.5, sourced_from_approval="x",
            )

    def test_empty_source_raises(self):
        with pytest.raises(ValueError, match="sourced_from_approval"):
            RegressionThreshold(max_regression_rate=0.4, sourced_from_approval="")

    def test_bad_min_samples_raises(self):
        with pytest.raises(ValueError, match="min_samples"):
            RegressionThreshold(
                max_regression_rate=0.4, min_samples=0,
                sourced_from_approval="x",
            )


# ── Test 8: Outcome recording is deterministic ──────────────────────

class TestOutcomeRecording:
    def test_regression_counted(self):
        w = ApplicationOutcomeWindow(
            active_calibration_id="active-1", proposal_id="p-app",
        )
        w = record_application_outcome(
            window=w, decision_outcome=_outcome(DecisionOutcomeType.RISK_MATERIALIZED),
        )
        assert w.regression_count == 1
        assert w.healthy_count == 0

    def test_healthy_counted(self):
        w = ApplicationOutcomeWindow(proposal_id="p-app")
        w = record_application_outcome(
            window=w, decision_outcome=_outcome(DecisionOutcomeType.RISK_RESOLVED),
        )
        assert w.healthy_count == 1

    def test_neutral_counted(self):
        w = ApplicationOutcomeWindow(proposal_id="p-app")
        w = record_application_outcome(
            window=w, decision_outcome=_outcome(DecisionOutcomeType.RISK_UNCHANGED),
        )
        assert w.neutral_count == 1
        assert w.regression_rate == 0.0


# ── Test 9: Health verdict — insufficient data ──────────────────────

class TestHealthInsufficientData:
    def test_too_few_samples(self):
        w = ApplicationOutcomeWindow(
            proposal_id="p-app", regression_count=1, healthy_count=1,
        )
        report = evaluate_application_health(window=w, threshold=_threshold(min_samples=3))
        assert report.verdict == MonitorVerdict.INSUFFICIENT_DATA
        assert report.recommend_revert is False


# ── Test 10: Health verdict — healthy ───────────────────────────────

class TestHealthHealthy:
    def test_below_threshold_healthy(self):
        w = ApplicationOutcomeWindow(
            proposal_id="p-app", regression_count=1, healthy_count=4,
        )
        report = evaluate_application_health(window=w, threshold=_threshold())
        assert report.verdict == MonitorVerdict.HEALTHY
        assert report.recommend_revert is False
        assert report.regression_rate == pytest.approx(0.2)


# ── Test 11: Health verdict — regressing ────────────────────────────

class TestHealthRegressing:
    def test_above_threshold_regressing(self):
        w = ApplicationOutcomeWindow(
            proposal_id="p-app", regression_count=4, healthy_count=1,
        )
        report = evaluate_application_health(window=w, threshold=_threshold())
        assert report.verdict == MonitorVerdict.REGRESSING
        assert report.recommend_revert is True
        assert report.regression_rate == pytest.approx(0.8)


# ── Test 12: Auto-revert triggers on regression, removal-only ───────

class TestAutoRevert:
    def test_regression_auto_reverts(self):
        active = ActiveCalibrationRecord(
            id="active-1", proposal_id="p-app",
            status=ApplicationStatus.ACTIVE,
        )
        w = ApplicationOutcomeWindow(
            active_calibration_id="active-1", proposal_id="p-app",
            regression_count=4, healthy_count=1,
        )
        reverted, revert_record = auto_revert_if_regressing(
            active_record=active, window=w, threshold=_threshold(), now=_NOW,
        )
        assert reverted.status == ApplicationStatus.REVERTED
        assert revert_record is not None
        assert revert_record.trigger == RevertTrigger.OUTCOME_REGRESSION

    def test_healthy_does_not_revert(self):
        active = ActiveCalibrationRecord(
            id="active-1", proposal_id="p-app",
            status=ApplicationStatus.ACTIVE,
        )
        w = ApplicationOutcomeWindow(
            active_calibration_id="active-1", proposal_id="p-app",
            regression_count=1, healthy_count=4,
        )
        reverted, revert_record = auto_revert_if_regressing(
            active_record=active, window=w, threshold=_threshold(), now=_NOW,
        )
        assert reverted.status == ApplicationStatus.ACTIVE
        assert revert_record is None

    def test_insufficient_data_does_not_revert(self):
        active = ActiveCalibrationRecord(
            id="active-1", proposal_id="p-app",
            status=ApplicationStatus.ACTIVE,
        )
        w = ApplicationOutcomeWindow(
            proposal_id="p-app", regression_count=1, healthy_count=0,
        )
        reverted, revert_record = auto_revert_if_regressing(
            active_record=active, window=w, threshold=_threshold(min_samples=3), now=_NOW,
        )
        assert reverted.status == ApplicationStatus.ACTIVE
        assert revert_record is None


# ── Test 13: Auto-revert never grants authority ─────────────────────

class TestAutoRevertRemovalOnly:
    def test_revert_only_moves_to_reverted(self):
        # Whatever the input, revert can only produce REVERTED — never
        # ACTIVE-up, never a governance grant.
        active = ActiveCalibrationRecord(
            id="active-1", proposal_id="p-app",
            status=ApplicationStatus.ACTIVE,
        )
        reverted, _ = revert_application(
            active_record=active, trigger=RevertTrigger.MANUAL, now=_NOW,
        )
        assert reverted.status == ApplicationStatus.REVERTED
        # The proposal_id and id are preserved; nothing is granted.
        assert reverted.proposal_id == "p-app"

    def test_revert_does_not_touch_governance_status(self):
        b = _bundle()
        r = _approval(bundle=b)
        status_before = r.status
        active = ActiveCalibrationRecord(
            id="active-1", proposal_id="p-app", status=ApplicationStatus.ACTIVE,
        )
        revert_application(
            active_record=active, trigger=RevertTrigger.OUTCOME_REGRESSION, now=_NOW,
        )
        assert r.status == status_before  # governance approval untouched


# ── Test 14: Application does not mutate governance approval ─────────

class TestApplicationNoGovernanceMutation:
    def test_approval_unchanged_after_apply(self):
        b = _bundle()
        r = _approval(bundle=b)
        status_before = r.status
        tier_before = r.review_tier
        result, _ = _apply(record=r, bundle=b)
        assert result.decision == ApplicationDecision.APPLIED
        assert r.status == status_before
        assert r.review_tier == tier_before


# ── Test 15: Append-only ledger ─────────────────────────────────────

class TestLedger:
    def test_applied_and_reverted_recorded(self):
        ledger = ApplicationLedger()
        result, _ = _apply()
        ledger = append_application(ledger=ledger, result=result, now=_NOW)
        assert len(ledger.entries) == 1
        assert ledger.entries[0].entry_type == LedgerEntryType.APPLIED

        active = result.active_record
        reverted, revert_record = revert_application(
            active_record=active, trigger=RevertTrigger.OUTCOME_REGRESSION, now=_NOW,
        )
        ledger = append_revert(ledger=ledger, revert_record=revert_record)
        assert len(ledger.entries) == 2
        assert ledger.entries[1].entry_type == LedgerEntryType.REVERTED

    def test_append_is_immutable(self):
        ledger = ApplicationLedger()
        result, _ = _apply()
        ledger2 = append_application(ledger=ledger, result=result, now=_NOW)
        assert len(ledger.entries) == 0  # original untouched
        assert len(ledger2.entries) == 1

    def test_blocked_recorded(self):
        ks = engage_kill_switch(
            kill_switch=_kill_switch(), engaged_by="x", reason="y", now=_NOW,
        )
        result, _ = _apply(kill_switch=ks)
        ledger = append_application(
            ledger=ApplicationLedger(), result=result, now=_NOW,
        )
        assert ledger.entries[0].entry_type == LedgerEntryType.BLOCKED


# ── Test 16: Audit assembly for re-review ───────────────────────────

class TestAudit:
    def test_audit_counts(self):
        ledger = ApplicationLedger()
        applied, _ = _apply()
        ledger = append_application(ledger=ledger, result=applied, now=_NOW)

        ks = engage_kill_switch(
            kill_switch=_kill_switch(), engaged_by="x", reason="y", now=_NOW,
        )
        blocked, _ = _apply(kill_switch=ks)
        ledger = append_application(ledger=ledger, result=blocked, now=_NOW)

        active = applied.active_record
        _, revert_record = revert_application(
            active_record=active, trigger=RevertTrigger.OUTCOME_REGRESSION, now=_NOW,
        )
        ledger = append_revert(ledger=ledger, revert_record=revert_record)

        audit = build_application_audit(proposal_id="p-app", ledger=ledger)
        assert audit["applied_count"] == 1
        assert audit["blocked_count"] == 1
        assert audit["reverted_count"] == 1
        assert audit["total_entries"] == 3


# ── Test 17: No-live-ranker-mutation guardrail ──────────────────────

class TestNoLiveRankerMutation:
    def test_no_ranker_mutation_literals(self):
        import inspect
        import lltpm.application.control as ctrl
        import lltpm.application.monitor as mon
        import lltpm.application.ledger as led

        forbidden = [
            "tradeoff_score =",
            "apply_to_live_ranker",
            ".apply_calibration(",  # no recursive live mutation of ranker source
            "rank_options(",
            "graph.remove_",
            "graph.add_",
        ]
        for mod in [ctrl, mon, led]:
            source = inspect.getsource(mod)
            for pattern in forbidden:
                assert pattern not in source, (
                    f"{mod.__name__} must not contain '{pattern}'."
                )


# ── Test 18: No-governance-authority-grant guardrail ────────────────

class TestNoGovernanceAuthorityGrant:
    def test_no_authority_grant_literals(self):
        import inspect
        import lltpm.application.control as ctrl
        import lltpm.application.monitor as mon
        import lltpm.application.ledger as led

        forbidden = [
            "transition_status(",
            "issue_approval(",
            "renew_approval(",
            ".status = GovernanceStatus.APPROVED",
            "record.status =",
            "auto_promote",
            "auto_harden",
        ]
        for mod in [ctrl, mon, led]:
            source = inspect.getsource(mod)
            for pattern in forbidden:
                assert pattern not in source, (
                    f"{mod.__name__} must not contain '{pattern}' — "
                    f"application never grants governance authority."
                )
