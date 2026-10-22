"""Calibration Governance: Gated, Time-Boxed, Tiered Promotion.

Formal approval boundary between a stress-tested calibration proposal and the
right to ever apply it. Human gate is kept. Reviewer fatigue and approval
fossilization are solved *inside* the gate, not by deleting it.

Records, dossiers, and reports only — zero runtime mutation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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
    StressTestRecommendation,
    StressTestStatus,
    StressTestedEvidenceBundle,
)
from tpllm.structural.governance import (
    ApprovalExpiryMode,
    ApprovalValidity,
    BlastRadiusBand,
    BlastRadiusAssessment,
    CalibrationApprovalRecord,
    CalibrationReviewDecision,
    ChangedDecisionRecord,
    GovernanceJournalEntry,
    GovernanceStatus,
    IllegalTransitionError,
    PromotionEligibilityReport,
    RenewalOutcome,
    ReviewContext,
    ReviewDecisionType,
    ReviewDossier,
    ReviewTier,
    assess_blast_radius,
    build_review_dossier,
    compute_promotion_eligibility,
    evaluate_approval_validity,
    fingerprint_evidence_bundle,
    identify_changed_decisions,
    is_application_eligible,
    is_legal_transition,
    issue_approval,
    recommend_review_tier,
    record_review_decision,
    renew_approval,
    run_governance_intake,
    transition_status,
)


# ── Fakes & helpers ───────────────────────────────────────────────────

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


def _decision_override(option_id: str = "reviewer_redistribution"):
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


def _validated_proposal(**kwargs) -> CalibrationProposal:
    defaults = dict(
        id="p-gov",
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


def _promotable_bundle(**kwargs) -> StressTestedEvidenceBundle:
    defaults = dict(
        id="steb-1",
        calibration_proposal_id="p-gov",
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


def _changed_decisions_narrow() -> list[ChangedDecisionRecord]:
    return [
        ChangedDecisionRecord(
            decision_id="d-1",
            original_rank=2,
            adjusted_rank=1,
            rank_delta=1,
            outcome_known=True,
            outcome_label="successful",
        ),
    ]


def _changed_decisions_broad() -> list[ChangedDecisionRecord]:
    return [
        ChangedDecisionRecord(
            decision_id=f"d-{i}",
            original_rank=3,
            adjusted_rank=1,
            rank_delta=2,
            outcome_known=True,
            outcome_label=f"type-{i % 5}",
        )
        for i in range(35)
    ]


def _approval_record(
    bundle: StressTestedEvidenceBundle | None = None,
    expiry_mode: ApprovalExpiryMode = ApprovalExpiryMode.BY_DATE,
    expires_at: datetime | None = None,
    max_decisions: int | None = None,
    decisions_at_approval: int = 0,
    review_tier: ReviewTier = ReviewTier.TIER_STANDARD,
    required_tier: ReviewTier = ReviewTier.TIER_STANDARD,
) -> CalibrationApprovalRecord:
    if bundle is None:
        bundle = _promotable_bundle()
    fp = fingerprint_evidence_bundle(bundle)
    return CalibrationApprovalRecord(
        proposal_id="p-gov",
        approved_by="reviewer-1",
        review_tier=review_tier,
        required_tier=required_tier,
        expiry_mode=expiry_mode,
        expires_at=expires_at,
        max_decisions=max_decisions,
        decisions_at_approval=decisions_at_approval,
        evidence_bundle_fingerprint=fp,
        status=GovernanceStatus.APPROVED,
    )


# ── Test 1: Intake gating — non-promotable blocked ──────────────────

class TestIntakeGating:
    def test_non_promotable_bundle_blocked(self):
        bundle = _promotable_bundle(
            recommendation=StressTestRecommendation.REJECT_AS_OVERFIT,
        )
        proposal = _validated_proposal()
        d = _decision_override()
        outcome = _outcome(d)

        eligibility, dossier, journal = run_governance_intake(
            bundle=bundle,
            proposal=proposal,
            decision_records=[d],
            decision_outcomes=[outcome],
            options_by_decision_id={d.id: _options_bridge_top()},
        )

        assert not eligibility.is_eligible
        assert dossier is None
        assert "promotable_scoped_rule" in journal.reason.lower() or "not" in journal.reason.lower()

    def test_promotable_bundle_eligible(self):
        bundle = _promotable_bundle()
        proposal = _validated_proposal()
        d = _decision_override()
        outcome = _outcome(d)

        eligibility, dossier, journal = run_governance_intake(
            bundle=bundle,
            proposal=proposal,
            decision_records=[d],
            decision_outcomes=[outcome],
            options_by_decision_id={d.id: _options_bridge_top()},
        )

        assert eligibility.is_eligible
        assert dossier is not None
        assert journal.to_status == GovernanceStatus.READY_FOR_HUMAN_APPROVAL


# ── Test 2: Fingerprint determinism ──────────────────────────────────

class TestFingerprintDeterminism:
    def test_same_bundle_same_fingerprint(self):
        b1 = _promotable_bundle()
        b2 = _promotable_bundle()
        assert fingerprint_evidence_bundle(b1) == fingerprint_evidence_bundle(b2)

    def test_different_bundle_different_fingerprint(self):
        b1 = _promotable_bundle()
        b2 = _promotable_bundle(overall_stress_score=0.50)
        assert fingerprint_evidence_bundle(b1) != fingerprint_evidence_bundle(b2)


# ── Test 3: Fingerprint changes on bundle mutation ───────────────────

class TestFingerprintMutation:
    def test_id_change_alters_fingerprint(self):
        b1 = _promotable_bundle()
        b2 = _promotable_bundle(id="steb-different")
        assert fingerprint_evidence_bundle(b1) != fingerprint_evidence_bundle(b2)

    def test_score_change_alters_fingerprint(self):
        b1 = _promotable_bundle()
        b2 = _promotable_bundle(lateral_transfer_score=0.01)
        assert fingerprint_evidence_bundle(b1) != fingerprint_evidence_bundle(b2)

    def test_recommendation_change_alters_fingerprint(self):
        b1 = _promotable_bundle()
        b2 = _promotable_bundle(recommendation=StressTestRecommendation.REJECT_AS_BRITTLE)
        assert fingerprint_evidence_bundle(b1) != fingerprint_evidence_bundle(b2)


# ── Test 4: Blast radius — NARROW band ───────────────────────────────

class TestBlastRadiusNarrow:
    def test_few_decisions_narrow(self):
        bundle = _promotable_bundle(lateral_transfer_score=0.0)
        changed = _changed_decisions_narrow()
        blast = assess_blast_radius(bundle=bundle, changed_decisions=changed)
        assert blast.band == BlastRadiusBand.NARROW
        assert blast.decisions_affected_count == 1
        assert blast.max_rank_delta == 1


# ── Test 5: Blast radius — BROAD band ───────────────────────────────

class TestBlastRadiusBroad:
    def test_many_decisions_broad(self):
        bundle = _promotable_bundle(lateral_transfer_score=0.0)
        changed = _changed_decisions_broad()
        blast = assess_blast_radius(bundle=bundle, changed_decisions=changed)
        assert blast.band == BlastRadiusBand.BROAD
        assert blast.decisions_affected_count == 35

    def test_cross_domain_triggers_broad(self):
        bundle = _promotable_bundle(lateral_transfer_score=0.9)
        changed = _changed_decisions_narrow()
        blast = assess_blast_radius(bundle=bundle, changed_decisions=changed)
        assert blast.band == BlastRadiusBand.BROAD
        assert blast.cross_domain is True

    def test_high_delta_triggers_broad(self):
        bundle = _promotable_bundle(lateral_transfer_score=0.0)
        changed = [
            ChangedDecisionRecord(
                decision_id="d-big",
                original_rank=6,
                adjusted_rank=1,
                rank_delta=5,
                outcome_known=True,
            ),
        ]
        blast = assess_blast_radius(bundle=bundle, changed_decisions=changed)
        assert blast.band == BlastRadiusBand.BROAD


# ── Test 6: Review tier from blast radius ────────────────────────────

class TestReviewTierMapping:
    def test_narrow_yields_light(self):
        blast = BlastRadiusAssessment(band=BlastRadiusBand.NARROW)
        assert recommend_review_tier(blast) == ReviewTier.TIER_LIGHT

    def test_moderate_yields_standard(self):
        blast = BlastRadiusAssessment(band=BlastRadiusBand.MODERATE)
        assert recommend_review_tier(blast) == ReviewTier.TIER_STANDARD

    def test_broad_yields_heavy(self):
        blast = BlastRadiusAssessment(band=BlastRadiusBand.BROAD)
        assert recommend_review_tier(blast) == ReviewTier.TIER_HEAVY

    def test_context_can_only_raise_tier(self):
        blast = BlastRadiusAssessment(band=BlastRadiusBand.NARROW)
        ctx = ReviewContext(tier_override_floor=ReviewTier.TIER_HEAVY)
        assert recommend_review_tier(blast, ctx) == ReviewTier.TIER_HEAVY

    def test_context_cannot_lower_tier(self):
        blast = BlastRadiusAssessment(band=BlastRadiusBand.BROAD)
        ctx = ReviewContext(tier_override_floor=ReviewTier.TIER_LIGHT)
        assert recommend_review_tier(blast, ctx) == ReviewTier.TIER_HEAVY


# ── Test 7: Dossier contains expected fields ─────────────────────────

class TestDossierContents:
    def test_dossier_has_fingerprint_and_tier(self):
        bundle = _promotable_bundle()
        proposal = _validated_proposal()
        d = _decision_override()
        outcome = _outcome(d)

        dossier = build_review_dossier(
            bundle=bundle,
            proposal=proposal,
            decision_records=[d],
            decision_outcomes=[outcome],
            options_by_decision_id={d.id: _options_bridge_top()},
        )

        assert dossier.evidence_bundle_fingerprint
        assert dossier.recommended_tier in (
            ReviewTier.TIER_LIGHT, ReviewTier.TIER_STANDARD, ReviewTier.TIER_HEAVY,
        )
        assert dossier.proposal_id == "p-gov"
        assert dossier.cost_of_being_wrong
        assert dossier.blast_radius.band in (
            BlastRadiusBand.NARROW, BlastRadiusBand.MODERATE, BlastRadiusBand.BROAD,
        )


# ── Test 8: Fingerprint mismatch on review ───────────────────────────

class TestFingerprintMismatchOnReview:
    def test_mismatched_fingerprint_raises(self):
        bundle = _promotable_bundle()
        proposal = _validated_proposal()
        d = _decision_override()
        outcome = _outcome(d)

        dossier = build_review_dossier(
            bundle=bundle,
            proposal=proposal,
            decision_records=[d],
            decision_outcomes=[outcome],
            options_by_decision_id={d.id: _options_bridge_top()},
        )

        with pytest.raises(ValueError, match="fingerprint mismatch"):
            record_review_decision(
                dossier=dossier,
                reviewer_id="reviewer-1",
                decision=ReviewDecisionType.APPROVE,
                review_tier=ReviewTier.TIER_STANDARD,
                current_bundle_fingerprint="wrong-fingerprint",
            )

    def test_matching_fingerprint_succeeds(self):
        bundle = _promotable_bundle()
        proposal = _validated_proposal()
        d = _decision_override()
        outcome = _outcome(d)

        dossier = build_review_dossier(
            bundle=bundle,
            proposal=proposal,
            decision_records=[d],
            decision_outcomes=[outcome],
            options_by_decision_id={d.id: _options_bridge_top()},
        )

        fp = fingerprint_evidence_bundle(bundle)
        decision = record_review_decision(
            dossier=dossier,
            reviewer_id="reviewer-1",
            decision=ReviewDecisionType.APPROVE,
            review_tier=ReviewTier.TIER_STANDARD,
            current_bundle_fingerprint=fp,
        )
        assert decision.proposal_id == "p-gov"
        assert decision.decision == ReviewDecisionType.APPROVE


# ── Test 9: Reject decision prevents approval ───────────────────────

class TestRejectPreventsApproval:
    def test_reject_raises_on_issue_approval(self):
        reject_decision = CalibrationReviewDecision(
            proposal_id="p-gov",
            reviewer_id="reviewer-1",
            decision=ReviewDecisionType.REJECT,
            review_tier=ReviewTier.TIER_STANDARD,
            evidence_bundle_fingerprint="fp-abc",
        )

        with pytest.raises(ValueError, match="REJECT"):
            issue_approval(
                decision=reject_decision,
                expiry_mode=ApprovalExpiryMode.BY_DATE,
                expires_at=datetime.utcnow() + timedelta(days=30),
                required_tier=ReviewTier.TIER_STANDARD,
            )


# ── Test 10: Expiry mode validation ──────────────────────────────────

class TestExpiryModeValidation:
    def test_by_date_requires_expires_at(self):
        approve = CalibrationReviewDecision(
            proposal_id="p-gov",
            reviewer_id="r-1",
            decision=ReviewDecisionType.APPROVE,
            review_tier=ReviewTier.TIER_STANDARD,
            evidence_bundle_fingerprint="fp-1",
        )

        with pytest.raises(ValueError, match="BY_DATE"):
            issue_approval(
                decision=approve,
                expiry_mode=ApprovalExpiryMode.BY_DATE,
                expires_at=None,
                required_tier=ReviewTier.TIER_STANDARD,
            )

    def test_by_count_requires_max_decisions(self):
        approve = CalibrationReviewDecision(
            proposal_id="p-gov",
            reviewer_id="r-1",
            decision=ReviewDecisionType.APPROVE,
            review_tier=ReviewTier.TIER_STANDARD,
            evidence_bundle_fingerprint="fp-1",
        )

        with pytest.raises(ValueError, match="BY_DECISION_COUNT"):
            issue_approval(
                decision=approve,
                expiry_mode=ApprovalExpiryMode.BY_DECISION_COUNT,
                max_decisions=None,
                required_tier=ReviewTier.TIER_STANDARD,
            )

    def test_by_either_requires_at_least_one(self):
        approve = CalibrationReviewDecision(
            proposal_id="p-gov",
            reviewer_id="r-1",
            decision=ReviewDecisionType.APPROVE,
            review_tier=ReviewTier.TIER_STANDARD,
            evidence_bundle_fingerprint="fp-1",
        )

        with pytest.raises(ValueError, match="BY_EITHER"):
            issue_approval(
                decision=approve,
                expiry_mode=ApprovalExpiryMode.BY_EITHER,
                expires_at=None,
                max_decisions=None,
                required_tier=ReviewTier.TIER_STANDARD,
            )


# ── Test 11: Approval validity — date expiry ────────────────────────

class TestApprovalValidityDate:
    def test_unexpired_is_live(self):
        now = datetime(2026, 6, 1, 12, 0, 0)
        record = _approval_record(
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 7, 1, 12, 0, 0),
        )

        validity = evaluate_approval_validity(
            record=record, now=now, current_decision_count=0,
        )
        assert validity.is_live is True
        assert validity.days_remaining is not None
        assert validity.days_remaining > 0

    def test_expired_is_not_live(self):
        now = datetime(2026, 8, 1, 12, 0, 0)
        record = _approval_record(
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 7, 1, 12, 0, 0),
        )

        validity = evaluate_approval_validity(
            record=record, now=now, current_decision_count=0,
        )
        assert validity.is_live is False
        assert validity.status == GovernanceStatus.EXPIRED


# ── Test 12: Approval validity — decision count expiry ───────────────

class TestApprovalValidityCount:
    def test_under_limit_is_live(self):
        record = _approval_record(
            expiry_mode=ApprovalExpiryMode.BY_DECISION_COUNT,
            max_decisions=100,
            decisions_at_approval=0,
        )

        validity = evaluate_approval_validity(
            record=record,
            now=datetime(2026, 6, 1),
            current_decision_count=50,
        )
        assert validity.is_live is True
        assert validity.decisions_remaining == 50

    def test_at_limit_is_expired(self):
        record = _approval_record(
            expiry_mode=ApprovalExpiryMode.BY_DECISION_COUNT,
            max_decisions=100,
            decisions_at_approval=0,
        )

        validity = evaluate_approval_validity(
            record=record,
            now=datetime(2026, 6, 1),
            current_decision_count=100,
        )
        assert validity.is_live is False
        assert validity.decisions_remaining == 0


# ── Test 13: Tier floor enforcement ──────────────────────────────────

class TestTierFloorEnforcement:
    def test_light_review_cannot_approve_heavy_requirement(self):
        approve = CalibrationReviewDecision(
            proposal_id="p-gov",
            reviewer_id="r-1",
            decision=ReviewDecisionType.APPROVE,
            review_tier=ReviewTier.TIER_LIGHT,
            evidence_bundle_fingerprint="fp-1",
        )

        with pytest.raises(ValueError, match="tier"):
            issue_approval(
                decision=approve,
                expiry_mode=ApprovalExpiryMode.BY_DATE,
                expires_at=datetime(2026, 12, 1),
                required_tier=ReviewTier.TIER_HEAVY,
            )

    def test_heavy_review_can_approve_light_requirement(self):
        approve = CalibrationReviewDecision(
            proposal_id="p-gov",
            reviewer_id="r-1",
            decision=ReviewDecisionType.APPROVE,
            review_tier=ReviewTier.TIER_HEAVY,
            evidence_bundle_fingerprint="fp-1",
        )

        record = issue_approval(
            decision=approve,
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 12, 1),
            required_tier=ReviewTier.TIER_LIGHT,
        )
        assert record.status == GovernanceStatus.APPROVED
        assert record.review_tier == ReviewTier.TIER_HEAVY
        assert record.required_tier == ReviewTier.TIER_LIGHT


# ── Test 14: Legal state transitions ────────────────────────────────

class TestLegalTransitions:
    def test_stress_to_ready_legal(self):
        assert is_legal_transition(
            GovernanceStatus.STRESS_TEST_PASSED,
            GovernanceStatus.READY_FOR_HUMAN_APPROVAL,
        ) is True

    def test_ready_to_approved_legal(self):
        assert is_legal_transition(
            GovernanceStatus.READY_FOR_HUMAN_APPROVAL,
            GovernanceStatus.APPROVED,
        ) is True

    def test_ready_to_rejected_legal(self):
        assert is_legal_transition(
            GovernanceStatus.READY_FOR_HUMAN_APPROVAL,
            GovernanceStatus.REJECTED_BY_REVIEWER,
        ) is True

    def test_approved_to_expired_legal(self):
        assert is_legal_transition(
            GovernanceStatus.APPROVED,
            GovernanceStatus.EXPIRED,
        ) is True

    def test_approved_to_revoked_legal(self):
        assert is_legal_transition(
            GovernanceStatus.APPROVED,
            GovernanceStatus.REVOKED,
        ) is True

    def test_expired_to_ready_legal(self):
        assert is_legal_transition(
            GovernanceStatus.EXPIRED,
            GovernanceStatus.READY_FOR_HUMAN_APPROVAL,
        ) is True


# ── Test 15: Illegal transitions raise ───────────────────────────────

class TestIllegalTransitions:
    def test_approved_to_stress_illegal(self):
        assert is_legal_transition(
            GovernanceStatus.APPROVED,
            GovernanceStatus.STRESS_TEST_PASSED,
        ) is False

    def test_rejected_to_approved_illegal(self):
        assert is_legal_transition(
            GovernanceStatus.REJECTED_BY_REVIEWER,
            GovernanceStatus.APPROVED,
        ) is False

    def test_transition_status_raises_on_illegal(self):
        record = _approval_record()
        with pytest.raises(IllegalTransitionError):
            transition_status(
                record=record,
                to_status=GovernanceStatus.STRESS_TEST_PASSED,
                actor="test",
            )

    def test_transition_status_succeeds_on_legal(self):
        record = _approval_record()
        updated, journal = transition_status(
            record=record,
            to_status=GovernanceStatus.EXPIRED,
            actor="system",
            reason="Time expired.",
        )
        assert updated.status == GovernanceStatus.EXPIRED
        assert journal.from_status == GovernanceStatus.APPROVED
        assert journal.to_status == GovernanceStatus.EXPIRED


# ── Test 16: Renewal requires fresh evidence ─────────────────────────

class TestRenewal:
    def test_same_fingerprint_fails_renewal(self):
        bundle = _promotable_bundle()
        old_record = _approval_record(bundle=bundle)

        d = _decision_override()
        outcome = _outcome(d)

        result, new_record = renew_approval(
            old_record=old_record,
            fresh_bundle=bundle,
            fresh_proposal=_validated_proposal(),
            fresh_decision_records=[d],
            fresh_decision_outcomes=[outcome],
            fresh_options_by_decision_id={d.id: _options_bridge_top()},
            reviewer_id="reviewer-2",
            review_tier=ReviewTier.TIER_STANDARD,
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 12, 1),
        )

        assert result == RenewalOutcome.REVALIDATION_FAILED
        assert new_record is None

    def test_fresh_evidence_succeeds_renewal(self):
        old_bundle = _promotable_bundle()
        old_record = _approval_record(bundle=old_bundle)

        fresh_bundle = _promotable_bundle(
            id="steb-fresh",
            overall_stress_score=0.90,
            lateral_transfer_score=0.0,
        )

        d = _decision_override()
        outcome = _outcome(d)

        result, new_record = renew_approval(
            old_record=old_record,
            fresh_bundle=fresh_bundle,
            fresh_proposal=_validated_proposal(),
            fresh_decision_records=[d],
            fresh_decision_outcomes=[outcome],
            fresh_options_by_decision_id={d.id: _options_bridge_top()},
            reviewer_id="reviewer-2",
            review_tier=ReviewTier.TIER_STANDARD,
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 12, 1),
        )

        assert result == RenewalOutcome.RENEWED
        assert new_record is not None
        assert new_record.evidence_bundle_fingerprint == fingerprint_evidence_bundle(fresh_bundle)

    def test_non_promotable_fresh_bundle_fails(self):
        old_bundle = _promotable_bundle()
        old_record = _approval_record(bundle=old_bundle)

        bad_bundle = _promotable_bundle(
            id="steb-bad",
            recommendation=StressTestRecommendation.REJECT_AS_OVERFIT,
        )

        d = _decision_override()
        outcome = _outcome(d)

        result, _ = renew_approval(
            old_record=old_record,
            fresh_bundle=bad_bundle,
            fresh_proposal=_validated_proposal(),
            fresh_decision_records=[d],
            fresh_decision_outcomes=[outcome],
            fresh_options_by_decision_id={d.id: _options_bridge_top()},
            reviewer_id="reviewer-2",
            review_tier=ReviewTier.TIER_STANDARD,
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 12, 1),
        )
        assert result == RenewalOutcome.REVALIDATION_FAILED


# ── Test 17: The single green light ──────────────────────────────────

class TestSingleGreenLight:
    def test_all_conditions_met(self):
        bundle = _promotable_bundle()
        record = _approval_record(
            bundle=bundle,
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 12, 1),
        )
        now = datetime(2026, 6, 15)

        assert is_application_eligible(
            record=record, bundle=bundle, now=now, current_decision_count=0,
        ) is True

    def test_expired_record_blocked(self):
        bundle = _promotable_bundle()
        record = _approval_record(
            bundle=bundle,
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 1, 1),
        )
        now = datetime(2026, 6, 15)

        assert is_application_eligible(
            record=record, bundle=bundle, now=now, current_decision_count=0,
        ) is False

    def test_fingerprint_mismatch_blocked(self):
        bundle = _promotable_bundle()
        record = _approval_record(
            bundle=bundle,
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 12, 1),
        )
        different_bundle = _promotable_bundle(id="steb-different")
        now = datetime(2026, 6, 15)

        assert is_application_eligible(
            record=record, bundle=different_bundle, now=now, current_decision_count=0,
        ) is False

    def test_revoked_status_blocked(self):
        bundle = _promotable_bundle()
        record = _approval_record(
            bundle=bundle,
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 12, 1),
        )
        record.status = GovernanceStatus.REVOKED
        now = datetime(2026, 6, 15)

        assert is_application_eligible(
            record=record, bundle=bundle, now=now, current_decision_count=0,
        ) is False

    def test_tier_mismatch_blocked(self):
        bundle = _promotable_bundle()
        record = _approval_record(
            bundle=bundle,
            expiry_mode=ApprovalExpiryMode.BY_DATE,
            expires_at=datetime(2026, 12, 1),
            review_tier=ReviewTier.TIER_LIGHT,
            required_tier=ReviewTier.TIER_HEAVY,
        )
        now = datetime(2026, 6, 15)

        assert is_application_eligible(
            record=record, bundle=bundle, now=now, current_decision_count=0,
        ) is False


# ── Test 18: BY_EITHER expiry mode ──────────────────────────────────

class TestByEitherExpiry:
    def test_date_expired_count_not(self):
        record = _approval_record(
            expiry_mode=ApprovalExpiryMode.BY_EITHER,
            expires_at=datetime(2026, 1, 1),
            max_decisions=100,
            decisions_at_approval=0,
        )
        validity = evaluate_approval_validity(
            record=record,
            now=datetime(2026, 6, 1),
            current_decision_count=10,
        )
        assert validity.is_live is False

    def test_count_expired_date_not(self):
        record = _approval_record(
            expiry_mode=ApprovalExpiryMode.BY_EITHER,
            expires_at=datetime(2026, 12, 1),
            max_decisions=50,
            decisions_at_approval=0,
        )
        validity = evaluate_approval_validity(
            record=record,
            now=datetime(2026, 6, 1),
            current_decision_count=50,
        )
        assert validity.is_live is False

    def test_neither_expired_is_live(self):
        record = _approval_record(
            expiry_mode=ApprovalExpiryMode.BY_EITHER,
            expires_at=datetime(2026, 12, 1),
            max_decisions=100,
            decisions_at_approval=0,
        )
        validity = evaluate_approval_validity(
            record=record,
            now=datetime(2026, 6, 1),
            current_decision_count=10,
        )
        assert validity.is_live is True
        assert validity.days_remaining is not None
        assert validity.decisions_remaining == 90


# ── Test 19: No runtime mutation — forbidden literals ────────────────

class TestNoRuntimeMutation:
    def test_governance_never_calls_forbidden_patterns(self):
        import inspect
        import tpllm.structural.governance as gov

        source = inspect.getsource(gov)
        forbidden = [
            ".apply_calibration(",
            "graph.remove_",
            "graph.add_",
            "threshold =",
            "metric_weight =",
            "auto_apply",
        ]
        for pattern in forbidden:
            assert pattern not in source, (
                f"governance.py must not contain '{pattern}' — "
                f"this module is non-mutating."
            )
