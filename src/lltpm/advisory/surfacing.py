"""Asymmetric advisory surfacing — overlay + epistemic delta.

The advisory layer is a pure external *decoration* on the outgoing intervention
payload. If this code disappeared, the ranker would behave identically.

This module never mutates governance state, live ranking logic, or authority.
It does not call ``transition_status``, ``issue_approval``, ``renew_approval``,
or any Commit 7 authority mutator.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from lltpm.structural.governance import (
    CalibrationApprovalRecord,
    GovernanceStatus,
    evaluate_approval_validity,
    fingerprint_evidence_bundle,
)
from lltpm.structural.stress_test import (
    ProposalScopeSignature,
    StressTestedEvidenceBundle,
)


# ── Enums ──────────────────────────────────────────────────────────────

class SurfacingDecision(str, Enum):
    SURFACED = "surfaced"
    SUPPRESSED = "suppressed"


# ── Models ─────────────────────────────────────────────────────────────

_TIER_RANK: dict[str, int] = {
    "tier_light": 0,
    "tier_standard": 1,
    "tier_heavy": 2,
}


class AdvisoryThreshold(BaseModel):
    """The governed interruption parameter — never a bare float."""
    interruption_threshold: float
    min_bound: float
    max_bound: float
    sourced_from_approval: str

    @model_validator(mode="after")
    def _check_bounds(self) -> "AdvisoryThreshold":
        if not self.sourced_from_approval:
            raise ValueError("sourced_from_approval must not be empty.")
        if not (self.min_bound <= self.interruption_threshold <= self.max_bound):
            raise ValueError(
                f"interruption_threshold {self.interruption_threshold} "
                f"outside bounds [{self.min_bound}, {self.max_bound}]."
            )
        return self


class EpistemicDelta(BaseModel):
    """Measured disagreement between baseline and calibrated view."""
    baseline_top_risk: float = 0.0
    calibrated_top_risk: float = 0.0
    delta: float = 0.0
    rule_confidence: float = 0.0
    weighted_score: float = 0.0


class AdvisoryOverlay(BaseModel):
    """Payload decoration — present only when surfaced."""
    proposal_id: str = ""
    baseline_rank: int = 0
    calibrated_rank: int = 0
    calibrated_top_option: str = ""
    justification: str = ""
    epistemic_delta: EpistemicDelta = Field(default_factory=EpistemicDelta)
    scope_signature_match: bool = True


class AdvisoryEvaluation(BaseModel):
    """Result of deciding whether to speak."""
    proposal_id: str = ""
    decision: SurfacingDecision = SurfacingDecision.SUPPRESSED
    overlay: AdvisoryOverlay | None = None
    reason: str = ""
    advisory_eligible: bool = False
    in_scope: bool = False


# ── Scope check ──────────────────────────────────────────────────────

def _is_in_scope(
    node_scope: dict[str, str],
    scope_signature: ProposalScopeSignature,
) -> bool:
    """Node context falls within the rule's approved scope."""
    checks: list[bool] = []
    if scope_signature.origin_team_id:
        checks.append(node_scope.get("team_id") == scope_signature.origin_team_id)
    if scope_signature.origin_domain_id:
        checks.append(node_scope.get("domain_id") == scope_signature.origin_domain_id)
    if not checks:
        return True
    return all(checks)


# ── Functions ────────────────────────────────────────────────────────

def is_advisory_eligible(
    *,
    record: CalibrationApprovalRecord,
    bundle: StressTestedEvidenceBundle,
    node_scope: dict[str, str],
    scope_signature: ProposalScopeSignature,
    now: datetime,
    current_decision_count: int,
) -> bool:
    """Advisory eligibility: governance validity + scope.

    Reuses Commit 7 validity primitive. Passing this does NOT imply
    ``is_application_eligible``.
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

    if _TIER_RANK.get(record.review_tier.value, 0) < _TIER_RANK.get(
        record.required_tier.value, 0
    ):
        return False

    if record.evidence_bundle_fingerprint != fingerprint_evidence_bundle(bundle):
        return False

    if not _is_in_scope(node_scope, scope_signature):
        return False

    return True


def compute_epistemic_delta(
    *,
    baseline_top_risk: float,
    calibrated_top_risk: float,
    rule_confidence: float,
) -> EpistemicDelta:
    """Deterministic: delta = abs(baseline - calibrated), weighted by confidence."""
    delta = abs(baseline_top_risk - calibrated_top_risk)
    return EpistemicDelta(
        baseline_top_risk=baseline_top_risk,
        calibrated_top_risk=calibrated_top_risk,
        delta=delta,
        rule_confidence=rule_confidence,
        weighted_score=delta * rule_confidence,
    )


def evaluate_advisory_surfacing(
    *,
    record: CalibrationApprovalRecord,
    bundle: StressTestedEvidenceBundle,
    baseline_top_risk: float,
    calibrated_top_risk: float,
    rule_confidence: float,
    threshold: AdvisoryThreshold,
    node_scope: dict[str, str],
    scope_signature: ProposalScopeSignature,
    now: datetime,
    current_decision_count: int,
    baseline_rank: int = 1,
    calibrated_rank: int = 1,
    calibrated_top_option: str = "",
    justification: str = "",
) -> AdvisoryEvaluation:
    """Decide whether to surface the advisory overlay.

    Order: (1) eligibility, (2) epistemic delta, (3) threshold comparison.
    The threshold must arrive as an ``AdvisoryThreshold`` object — bare
    floats are rejected at the type level.
    """
    in_scope = _is_in_scope(node_scope, scope_signature)

    eligible = is_advisory_eligible(
        record=record,
        bundle=bundle,
        node_scope=node_scope,
        scope_signature=scope_signature,
        now=now,
        current_decision_count=current_decision_count,
    )

    if not eligible:
        reasons: list[str] = []
        if record.status != GovernanceStatus.APPROVED:
            reasons.append(f"status={record.status.value}")
        else:
            validity = evaluate_approval_validity(
                record=record, now=now,
                current_decision_count=current_decision_count,
            )
            if not validity.is_live:
                reasons.append("approval not live")
            if _TIER_RANK.get(record.review_tier.value, 0) < _TIER_RANK.get(
                record.required_tier.value, 0
            ):
                reasons.append("tier insufficient")
            if record.evidence_bundle_fingerprint != fingerprint_evidence_bundle(bundle):
                reasons.append("fingerprint mismatch")
        if not in_scope:
            reasons.append("out of scope")
        return AdvisoryEvaluation(
            proposal_id=record.proposal_id,
            decision=SurfacingDecision.SUPPRESSED,
            reason="; ".join(reasons) or "ineligible",
            advisory_eligible=False,
            in_scope=in_scope,
        )

    ed = compute_epistemic_delta(
        baseline_top_risk=baseline_top_risk,
        calibrated_top_risk=calibrated_top_risk,
        rule_confidence=rule_confidence,
    )

    if ed.weighted_score <= threshold.interruption_threshold:
        return AdvisoryEvaluation(
            proposal_id=record.proposal_id,
            decision=SurfacingDecision.SUPPRESSED,
            reason=(
                f"weighted_score {ed.weighted_score:.4f} "
                f"<= threshold {threshold.interruption_threshold}"
            ),
            advisory_eligible=True,
            in_scope=True,
        )

    overlay = AdvisoryOverlay(
        proposal_id=record.proposal_id,
        baseline_rank=baseline_rank,
        calibrated_rank=calibrated_rank,
        calibrated_top_option=calibrated_top_option,
        justification=justification or (
            f"Calibrated view shifts top risk from "
            f"{baseline_top_risk:.2f} to {calibrated_top_risk:.2f} "
            f"(delta={ed.delta:.2f}, confidence={rule_confidence:.2f})."
        ),
        epistemic_delta=ed,
        scope_signature_match=True,
    )

    return AdvisoryEvaluation(
        proposal_id=record.proposal_id,
        decision=SurfacingDecision.SURFACED,
        overlay=overlay,
        reason=(
            f"weighted_score {ed.weighted_score:.4f} "
            f"> threshold {threshold.interruption_threshold}"
        ),
        advisory_eligible=True,
        in_scope=True,
    )
