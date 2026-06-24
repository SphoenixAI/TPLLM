"""Calibration governance — gated, time-boxed, tiered promotion.

Builds the formal approval boundary between a stress-tested calibration
proposal and the right to ever apply it. The human gate is kept. The two
real failure modes of a naive gate — reviewer fatigue and approval
fossilization — are solved *inside* the gate, not by deleting it.

This module produces **records, dossiers, and reports only**. It performs
**zero runtime mutation**. Nothing here applies a calibration to the live
ranker. Application remains a separate, later, explicit commit.

Invariants (non-negotiable):
    1. Non-mutating. No code path writes to or biases the live ranking.
    2. Single green light. ``is_application_eligible()`` is the sole
       authority on whether a proposal may be applied.
    3. No permanent approvals. Every approval carries an expiry.
    4. Approval is bound to evidence. A fingerprint mismatch voids it.
    5. Tiered by blast radius. A broad proposal cannot clear light review.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from lltpm.structural.regression import simulate_proposal_on_case
from lltpm.structural.stress_test import (
    StressTestRecommendation,
    StressTestedEvidenceBundle,
)


# ── Enums ──────────────────────────────────────────────────────────────

class GovernanceStatus(str, Enum):
    STRESS_TEST_PASSED = "stress_test_passed"
    READY_FOR_HUMAN_APPROVAL = "ready_for_human_approval"
    APPROVED = "approved"
    REJECTED_BY_REVIEWER = "rejected_by_reviewer"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ReviewTier(str, Enum):
    TIER_LIGHT = "tier_light"
    TIER_STANDARD = "tier_standard"
    TIER_HEAVY = "tier_heavy"


_TIER_RANK: dict[str, int] = {
    ReviewTier.TIER_LIGHT: 0,
    ReviewTier.TIER_STANDARD: 1,
    ReviewTier.TIER_HEAVY: 2,
}


class BlastRadiusBand(str, Enum):
    NARROW = "narrow"
    MODERATE = "moderate"
    BROAD = "broad"


class ReviewDecisionType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalExpiryMode(str, Enum):
    BY_DATE = "by_date"
    BY_DECISION_COUNT = "by_decision_count"
    BY_EITHER = "by_either"


class RenewalOutcome(str, Enum):
    RENEWED = "renewed"
    REVALIDATION_FAILED = "revalidation_failed"
    LAPSED = "lapsed"


# ── Tunable constants ─────────────────────────────────────────────────

NARROW_MAX_DECISIONS = 5
NARROW_DELTA = 2
BROAD_MIN_DECISIONS = 30
BROAD_DELTA = 5


# ── Custom exception ─────────────────────────────────────────────────

class IllegalTransitionError(Exception):
    pass


# ── Models ─────────────────────────────────────────────────────────────

class ChangedDecisionRecord(BaseModel):
    """One historical decision the proposal would change."""
    decision_id: str = ""
    original_rank: int | None = None
    original_choice: str | None = None
    adjusted_rank: int | None = None
    adjusted_choice: str | None = None
    rank_delta: int = 0
    outcome_known: bool = False
    outcome_label: str | None = None


class BlastRadiusAssessment(BaseModel):
    """How broadly a proposal affects past decisions."""
    scope_breadth: int = 0
    decisions_affected_count: int = 0
    max_rank_delta: int = 0
    cross_domain: bool = False
    band: BlastRadiusBand = BlastRadiusBand.NARROW


class ReviewDossier(BaseModel):
    """Decision-grade summary for human review — the fatigue fix."""
    proposal_id: str = ""
    plain_summary: str = ""
    changed_decisions: list[ChangedDecisionRecord] = Field(default_factory=list)
    stress_failures_summary: str = ""
    blast_radius: BlastRadiusAssessment = Field(default_factory=BlastRadiusAssessment)
    recommended_tier: ReviewTier = ReviewTier.TIER_STANDARD
    cost_of_being_wrong: str = ""
    evidence_bundle_fingerprint: str = ""


class ReviewContext(BaseModel):
    """Optional strategic input from a human — can only raise the tier."""
    note: str = ""
    tier_override_floor: ReviewTier | None = None


class PromotionEligibilityReport(BaseModel):
    """Can this proposal proceed to human review?"""
    proposal_id: str = ""
    is_eligible: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)
    required_tier: ReviewTier = ReviewTier.TIER_STANDARD
    evidence_bundle_fingerprint: str = ""


class CalibrationReviewDecision(BaseModel):
    """A reviewer's verdict on a calibration proposal."""
    proposal_id: str = ""
    reviewer_id: str = ""
    decision: ReviewDecisionType = ReviewDecisionType.REJECT
    decided_at: datetime = Field(default_factory=datetime.utcnow)
    rationale: str = ""
    review_tier: ReviewTier = ReviewTier.TIER_STANDARD
    evidence_bundle_fingerprint: str = ""


class CalibrationApprovalRecord(BaseModel):
    """A time-boxed, evidence-bound approval. Never permanent."""
    proposal_id: str = ""
    approved_by: str = ""
    approved_at: datetime = Field(default_factory=datetime.utcnow)
    review_tier: ReviewTier = ReviewTier.TIER_STANDARD
    required_tier: ReviewTier = ReviewTier.TIER_STANDARD
    expiry_mode: ApprovalExpiryMode = ApprovalExpiryMode.BY_DATE
    expires_at: datetime | None = None
    max_decisions: int | None = None
    decisions_at_approval: int = 0
    evidence_bundle_fingerprint: str = ""
    status: GovernanceStatus = GovernanceStatus.APPROVED


class ApprovalValidity(BaseModel):
    """Current liveness of an approval."""
    is_live: bool = False
    status: GovernanceStatus = GovernanceStatus.EXPIRED
    reason: str = ""
    days_remaining: float | None = None
    decisions_remaining: int | None = None


class GovernanceJournalEntry(BaseModel):
    """Append-only audit record of every status transition."""
    proposal_id: str = ""
    from_status: GovernanceStatus | None = None
    to_status: GovernanceStatus = GovernanceStatus.STRESS_TEST_PASSED
    at: datetime = Field(default_factory=datetime.utcnow)
    actor: str = ""
    reason: str = ""


# ── Fingerprinting ───────────────────────────────────────────────────

def fingerprint_evidence_bundle(bundle: StressTestedEvidenceBundle) -> str:
    """Deterministic SHA-256 over the bundle's identity-bearing fields."""
    identity = {
        "id": bundle.id,
        "calibration_proposal_id": bundle.calibration_proposal_id,
        "base_evidence_bundle_id": bundle.base_evidence_bundle_id,
        "scope_signature_id": bundle.scope_signature_id,
        "lateral_transfer_report_id": bundle.lateral_transfer_report_id,
        "fuzz_resilience_report_id": bundle.fuzz_resilience_report_id,
        "synthetic_stress_report_id": bundle.synthetic_stress_report_id,
        "overfit_warning_ids": sorted(bundle.overfit_warning_ids),
        "lateral_transfer_score": bundle.lateral_transfer_score,
        "fuzz_resilience_score": bundle.fuzz_resilience_score,
        "synthetic_resilience_score": bundle.synthetic_resilience_score,
        "overall_stress_score": bundle.overall_stress_score,
        "recommendation": bundle.recommendation.value,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── Changed decisions ─────────────────────────────────────────────────

def identify_changed_decisions(
    *,
    bundle: StressTestedEvidenceBundle,
    proposal: object,
    decision_records: list[object],
    decision_outcomes: list[object],
    options_by_decision_id: dict[str, list[object]],
) -> list[ChangedDecisionRecord]:
    """Replay the proposal against historical decisions; emit one record per
    decision whose ranking the rule alters. Deterministic."""
    outcome_by_decision: dict[str, object] = {}
    for outcome in decision_outcomes:
        did = getattr(outcome, "decision_record_id", None)
        if did:
            outcome_by_decision[did] = outcome

    changed: list[ChangedDecisionRecord] = []
    for decision in decision_records:
        did = getattr(decision, "id", None)
        if not did:
            continue
        options = options_by_decision_id.get(did, [])
        if not options:
            continue

        result = simulate_proposal_on_case(
            proposal=proposal,
            decision=decision,
            outcome=outcome_by_decision.get(did),
            intervention_options=options,
        )
        if result is None:
            continue

        live_rank = result.live_human_option_rank
        proposal_rank = result.proposal_human_option_rank
        if live_rank is None or proposal_rank is None or live_rank == proposal_rank:
            continue

        outcome_obj = outcome_by_decision.get(did)
        changed.append(ChangedDecisionRecord(
            decision_id=did,
            original_rank=live_rank,
            adjusted_rank=proposal_rank,
            rank_delta=live_rank - proposal_rank,
            outcome_known=outcome_obj is not None,
            outcome_label=str(getattr(outcome_obj, "outcome_type", None)) if outcome_obj else None,
        ))

    return changed


# ── Blast radius ──────────────────────────────────────────────────────

def assess_blast_radius(
    *,
    bundle: StressTestedEvidenceBundle,
    changed_decisions: list[ChangedDecisionRecord],
) -> BlastRadiusAssessment:
    """Derive blast radius band from changed decisions and bundle scope."""
    count = len(changed_decisions)
    max_delta = max((abs(cd.rank_delta) for cd in changed_decisions), default=0)

    cross_domain = bool(
        getattr(bundle, "lateral_transfer_score", 0) > 0
        and len(getattr(bundle, "overfit_warning_ids", []) or []) == 0
        and count > 0
    )

    distinct_outcomes = len({cd.outcome_label for cd in changed_decisions if cd.outcome_label})
    scope_breadth = max(distinct_outcomes, 1) if count > 0 else 0

    if cross_domain or count >= BROAD_MIN_DECISIONS or max_delta >= BROAD_DELTA:
        band = BlastRadiusBand.BROAD
    elif count <= NARROW_MAX_DECISIONS and not cross_domain and max_delta <= NARROW_DELTA:
        band = BlastRadiusBand.NARROW
    else:
        band = BlastRadiusBand.MODERATE

    return BlastRadiusAssessment(
        scope_breadth=scope_breadth,
        decisions_affected_count=count,
        max_rank_delta=max_delta,
        cross_domain=cross_domain,
        band=band,
    )


# ── Review tier ───────────────────────────────────────────────────────

def recommend_review_tier(
    blast_radius: BlastRadiusAssessment,
    context: ReviewContext | None = None,
) -> ReviewTier:
    """NARROW→LIGHT, MODERATE→STANDARD, BROAD→HEAVY. Context can only raise."""
    _BAND_TO_TIER = {
        BlastRadiusBand.NARROW: ReviewTier.TIER_LIGHT,
        BlastRadiusBand.MODERATE: ReviewTier.TIER_STANDARD,
        BlastRadiusBand.BROAD: ReviewTier.TIER_HEAVY,
    }
    tier = _BAND_TO_TIER.get(blast_radius.band, ReviewTier.TIER_STANDARD)

    if context and context.tier_override_floor:
        floor = context.tier_override_floor
        if _TIER_RANK.get(floor, 0) > _TIER_RANK.get(tier, 0):
            tier = floor

    return tier


# ── Review dossier ────────────────────────────────────────────────────

def build_review_dossier(
    *,
    bundle: StressTestedEvidenceBundle,
    proposal: object,
    decision_records: list[object],
    decision_outcomes: list[object],
    options_by_decision_id: dict[str, list[object]],
    context: ReviewContext | None = None,
) -> ReviewDossier:
    """Assemble the decision-grade summary for human review."""
    fingerprint = fingerprint_evidence_bundle(bundle)

    changed = identify_changed_decisions(
        bundle=bundle,
        proposal=proposal,
        decision_records=decision_records,
        decision_outcomes=decision_outcomes,
        options_by_decision_id=options_by_decision_id,
    )

    blast = assess_blast_radius(bundle=bundle, changed_decisions=changed)
    tier = recommend_review_tier(blast, context)

    stress_failures: list[str] = []
    for wid in bundle.overfit_warning_ids:
        stress_failures.append(f"Overfit warning: {wid}")
    if bundle.lateral_transfer_score < 0.5 and bundle.lateral_transfer_score > 0:
        stress_failures.append(
            f"Low lateral transfer score: {bundle.lateral_transfer_score:.2f}"
        )
    if bundle.fuzz_resilience_score < 0.7 and bundle.fuzz_resilience_score > 0:
        stress_failures.append(
            f"Low fuzz resilience: {bundle.fuzz_resilience_score:.2f}"
        )

    proposal_summary = getattr(proposal, "proposed_change_summary", "") or ""
    cost_of_wrong = (
        f"If this rule is applied and is wrong, it will affect "
        f"{blast.decisions_affected_count} historical decisions with a max "
        f"rank shift of {blast.max_rank_delta}. "
        f"Blast radius: {blast.band.value}."
    )

    return ReviewDossier(
        proposal_id=getattr(proposal, "id", ""),
        plain_summary=proposal_summary,
        changed_decisions=changed,
        stress_failures_summary="; ".join(stress_failures) if stress_failures else "No stress failures detected.",
        blast_radius=blast,
        recommended_tier=tier,
        cost_of_being_wrong=cost_of_wrong,
        evidence_bundle_fingerprint=fingerprint,
    )


# ── Promotion eligibility ────────────────────────────────────────────

def compute_promotion_eligibility(
    *,
    bundle: StressTestedEvidenceBundle,
    proposal: object,
    decision_records: list[object],
    decision_outcomes: list[object],
    options_by_decision_id: dict[str, list[object]],
    context: ReviewContext | None = None,
) -> PromotionEligibilityReport:
    """Block if the bundle is not promotable, or has zero evidence."""
    fingerprint = fingerprint_evidence_bundle(bundle)
    blocking: list[str] = []

    if bundle.recommendation != StressTestRecommendation.PROMOTABLE_SCOPED_RULE:
        blocking.append(
            f"Bundle recommendation is '{bundle.recommendation.value}', "
            f"not 'promotable_scoped_rule'."
        )

    changed = identify_changed_decisions(
        bundle=bundle,
        proposal=proposal,
        decision_records=decision_records,
        decision_outcomes=decision_outcomes,
        options_by_decision_id=options_by_decision_id,
    )

    if not changed:
        blocking.append("Zero changed decisions — nothing to govern.")

    blast = assess_blast_radius(bundle=bundle, changed_decisions=changed)
    tier = recommend_review_tier(blast, context)

    return PromotionEligibilityReport(
        proposal_id=getattr(proposal, "id", ""),
        is_eligible=len(blocking) == 0,
        blocking_reasons=blocking,
        required_tier=tier,
        evidence_bundle_fingerprint=fingerprint,
    )


# ── Governance intake orchestrator ────────────────────────────────────

def run_governance_intake(
    *,
    bundle: StressTestedEvidenceBundle,
    proposal: object,
    decision_records: list[object],
    decision_outcomes: list[object],
    options_by_decision_id: dict[str, list[object]],
    context: ReviewContext | None = None,
) -> tuple[PromotionEligibilityReport, ReviewDossier | None, GovernanceJournalEntry]:
    """If eligible: build dossier, transition STRESS_TEST_PASSED →
    READY_FOR_HUMAN_APPROVAL. If not: no dossier, no transition."""
    eligibility = compute_promotion_eligibility(
        bundle=bundle,
        proposal=proposal,
        decision_records=decision_records,
        decision_outcomes=decision_outcomes,
        options_by_decision_id=options_by_decision_id,
        context=context,
    )

    if not eligibility.is_eligible:
        journal = GovernanceJournalEntry(
            proposal_id=eligibility.proposal_id,
            from_status=GovernanceStatus.STRESS_TEST_PASSED,
            to_status=GovernanceStatus.STRESS_TEST_PASSED,
            actor="governance_intake",
            reason="; ".join(eligibility.blocking_reasons),
        )
        return eligibility, None, journal

    dossier = build_review_dossier(
        bundle=bundle,
        proposal=proposal,
        decision_records=decision_records,
        decision_outcomes=decision_outcomes,
        options_by_decision_id=options_by_decision_id,
        context=context,
    )

    journal = GovernanceJournalEntry(
        proposal_id=eligibility.proposal_id,
        from_status=GovernanceStatus.STRESS_TEST_PASSED,
        to_status=GovernanceStatus.READY_FOR_HUMAN_APPROVAL,
        actor="governance_intake",
        reason="Eligible for human review.",
    )

    return eligibility, dossier, journal


# ── Review decision recording ─────────────────────────────────────────

def record_review_decision(
    *,
    dossier: ReviewDossier,
    reviewer_id: str,
    decision: ReviewDecisionType,
    review_tier: ReviewTier,
    rationale: str = "",
    current_bundle_fingerprint: str = "",
) -> CalibrationReviewDecision:
    """Capture a reviewer's verdict. Raises on fingerprint mismatch."""
    if current_bundle_fingerprint and current_bundle_fingerprint != dossier.evidence_bundle_fingerprint:
        raise ValueError(
            "Evidence bundle fingerprint mismatch: the bundle has changed "
            "since this dossier was built. Review is void."
        )

    return CalibrationReviewDecision(
        proposal_id=dossier.proposal_id,
        reviewer_id=reviewer_id,
        decision=decision,
        rationale=rationale,
        review_tier=review_tier,
        evidence_bundle_fingerprint=dossier.evidence_bundle_fingerprint,
    )


# ── Approval issuance ─────────────────────────────────────────────────

def issue_approval(
    *,
    decision: CalibrationReviewDecision,
    expiry_mode: ApprovalExpiryMode,
    expires_at: datetime | None = None,
    max_decisions: int | None = None,
    decisions_at_approval: int = 0,
    required_tier: ReviewTier,
) -> CalibrationApprovalRecord:
    """Issue a time-boxed approval. Raises on missing expiry or tier violation."""
    if decision.decision != ReviewDecisionType.APPROVE:
        raise ValueError("Cannot issue approval for a REJECT decision.")

    if expiry_mode == ApprovalExpiryMode.BY_DATE and expires_at is None:
        raise ValueError("BY_DATE expiry mode requires expires_at.")
    if expiry_mode == ApprovalExpiryMode.BY_DECISION_COUNT and max_decisions is None:
        raise ValueError("BY_DECISION_COUNT expiry mode requires max_decisions.")
    if expiry_mode == ApprovalExpiryMode.BY_EITHER and expires_at is None and max_decisions is None:
        raise ValueError("BY_EITHER expiry mode requires at least one of expires_at or max_decisions.")

    if _TIER_RANK.get(decision.review_tier, 0) < _TIER_RANK.get(required_tier, 0):
        raise ValueError(
            f"Reviewer reviewed at {decision.review_tier.value} but "
            f"required tier is {required_tier.value}. Cannot approve."
        )

    return CalibrationApprovalRecord(
        proposal_id=decision.proposal_id,
        approved_by=decision.reviewer_id,
        review_tier=decision.review_tier,
        required_tier=required_tier,
        expiry_mode=expiry_mode,
        expires_at=expires_at,
        max_decisions=max_decisions,
        decisions_at_approval=decisions_at_approval,
        evidence_bundle_fingerprint=decision.evidence_bundle_fingerprint,
        status=GovernanceStatus.APPROVED,
    )


# ── Approval validity ─────────────────────────────────────────────────

def evaluate_approval_validity(
    *,
    record: CalibrationApprovalRecord,
    now: datetime,
    current_decision_count: int,
) -> ApprovalValidity:
    """Check whether an approval is still live."""
    if record.status != GovernanceStatus.APPROVED:
        return ApprovalValidity(
            is_live=False,
            status=record.status,
            reason=f"Approval status is {record.status.value}, not approved.",
        )

    date_expired = False
    count_expired = False
    days_remaining: float | None = None
    decisions_remaining: int | None = None

    if record.expiry_mode in (ApprovalExpiryMode.BY_DATE, ApprovalExpiryMode.BY_EITHER):
        if record.expires_at is not None:
            delta = (record.expires_at - now).total_seconds()
            days_remaining = max(0.0, delta / 86400)
            if now >= record.expires_at:
                date_expired = True

    if record.expiry_mode in (ApprovalExpiryMode.BY_DECISION_COUNT, ApprovalExpiryMode.BY_EITHER):
        if record.max_decisions is not None:
            used = current_decision_count - record.decisions_at_approval
            decisions_remaining = max(0, record.max_decisions - used)
            if used >= record.max_decisions:
                count_expired = True

    if record.expiry_mode == ApprovalExpiryMode.BY_DATE:
        expired = date_expired
    elif record.expiry_mode == ApprovalExpiryMode.BY_DECISION_COUNT:
        expired = count_expired
    else:
        expired = date_expired or count_expired

    if expired:
        return ApprovalValidity(
            is_live=False,
            status=GovernanceStatus.EXPIRED,
            reason="Approval has expired.",
            days_remaining=days_remaining,
            decisions_remaining=decisions_remaining,
        )

    return ApprovalValidity(
        is_live=True,
        status=GovernanceStatus.APPROVED,
        reason="Approval is live.",
        days_remaining=days_remaining,
        decisions_remaining=decisions_remaining,
    )


# ── State machine ─────────────────────────────────────────────────────

_LEGAL_TRANSITIONS: set[tuple[GovernanceStatus, GovernanceStatus]] = {
    (GovernanceStatus.STRESS_TEST_PASSED, GovernanceStatus.READY_FOR_HUMAN_APPROVAL),
    (GovernanceStatus.READY_FOR_HUMAN_APPROVAL, GovernanceStatus.APPROVED),
    (GovernanceStatus.READY_FOR_HUMAN_APPROVAL, GovernanceStatus.REJECTED_BY_REVIEWER),
    (GovernanceStatus.APPROVED, GovernanceStatus.EXPIRED),
    (GovernanceStatus.APPROVED, GovernanceStatus.REVOKED),
    (GovernanceStatus.EXPIRED, GovernanceStatus.READY_FOR_HUMAN_APPROVAL),
}


def is_legal_transition(from_status: GovernanceStatus, to_status: GovernanceStatus) -> bool:
    return (from_status, to_status) in _LEGAL_TRANSITIONS


def transition_status(
    *,
    record: CalibrationApprovalRecord,
    to_status: GovernanceStatus,
    actor: str,
    reason: str = "",
) -> tuple[CalibrationApprovalRecord, GovernanceJournalEntry]:
    """Transition status and emit a journal entry. Raises on illegal transition."""
    if not is_legal_transition(record.status, to_status):
        raise IllegalTransitionError(
            f"Illegal transition: {record.status.value} → {to_status.value}"
        )

    journal = GovernanceJournalEntry(
        proposal_id=record.proposal_id,
        from_status=record.status,
        to_status=to_status,
        actor=actor,
        reason=reason,
    )

    record.status = to_status
    return record, journal


# ── Renewal ───────────────────────────────────────────────────────────

def renew_approval(
    *,
    old_record: CalibrationApprovalRecord,
    fresh_bundle: StressTestedEvidenceBundle,
    fresh_proposal: object,
    fresh_decision_records: list[object],
    fresh_decision_outcomes: list[object],
    fresh_options_by_decision_id: dict[str, list[object]],
    reviewer_id: str,
    review_tier: ReviewTier,
    expiry_mode: ApprovalExpiryMode,
    expires_at: datetime | None = None,
    max_decisions: int | None = None,
    decisions_at_approval: int = 0,
    context: ReviewContext | None = None,
) -> tuple[RenewalOutcome, CalibrationApprovalRecord | None]:
    """Renewal is revalidation, not extension. Requires a fresh promotable bundle."""
    if fresh_bundle.recommendation != StressTestRecommendation.PROMOTABLE_SCOPED_RULE:
        return RenewalOutcome.REVALIDATION_FAILED, None

    fresh_fingerprint = fingerprint_evidence_bundle(fresh_bundle)
    if fresh_fingerprint == old_record.evidence_bundle_fingerprint:
        return RenewalOutcome.REVALIDATION_FAILED, None

    if old_record.status in (GovernanceStatus.APPROVED, GovernanceStatus.EXPIRED):
        old_record.status = GovernanceStatus.EXPIRED

    eligibility = compute_promotion_eligibility(
        bundle=fresh_bundle,
        proposal=fresh_proposal,
        decision_records=fresh_decision_records,
        decision_outcomes=fresh_decision_outcomes,
        options_by_decision_id=fresh_options_by_decision_id,
        context=context,
    )

    if not eligibility.is_eligible:
        return RenewalOutcome.REVALIDATION_FAILED, None

    review_decision = CalibrationReviewDecision(
        proposal_id=old_record.proposal_id,
        reviewer_id=reviewer_id,
        decision=ReviewDecisionType.APPROVE,
        review_tier=review_tier,
        evidence_bundle_fingerprint=fresh_fingerprint,
        rationale="Renewal with fresh evidence.",
    )

    try:
        new_record = issue_approval(
            decision=review_decision,
            expiry_mode=expiry_mode,
            expires_at=expires_at,
            max_decisions=max_decisions,
            decisions_at_approval=decisions_at_approval,
            required_tier=eligibility.required_tier,
        )
    except ValueError:
        return RenewalOutcome.REVALIDATION_FAILED, None

    return RenewalOutcome.RENEWED, new_record


# ── The single green light ────────────────────────────────────────────

def is_application_eligible(
    *,
    record: CalibrationApprovalRecord,
    bundle: StressTestedEvidenceBundle,
    now: datetime,
    current_decision_count: int,
) -> bool:
    """The sole authority on whether a proposal may be applied.

    Returns True iff ALL hold:
        1. record.status == APPROVED
        2. The approval is live (not expired by date or decision count)
        3. review_tier >= required_tier
        4. evidence_bundle_fingerprint matches the current bundle
    Any failure → False.
    """
    if record.status != GovernanceStatus.APPROVED:
        return False

    validity = evaluate_approval_validity(
        record=record,
        now=now,
        current_decision_count=current_decision_count,
    )
    if not validity.is_live:
        return False

    if _TIER_RANK.get(record.review_tier, 0) < _TIER_RANK.get(record.required_tier, 0):
        return False

    if record.evidence_bundle_fingerprint != fingerprint_evidence_bundle(bundle):
        return False

    return True
