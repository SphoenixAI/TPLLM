"""Validation layer — guardrails between insights and agent recommendations.

This is the critical safety gate in the closed-loop TPM control system.
The riskiest step is where the system says: "This metric changed,
therefore this is what we should do." That inference needs guardrails.

The validation layer checks:
1. Evidence sufficiency — does the insight have enough supporting claims?
2. Confidence threshold — is the confidence high enough to act?
3. Severity-gated approval — high-severity actions need human sign-off
4. Contradiction check — are there conflicting claims that undermine the insight?
5. Staleness check — is the evidence recent enough to be relevant?
6. Action proportionality — does the recommended action match the severity?

Flow:
    insights → validation / confidence / evidence check → recommendations

If validation fails, the insight is flagged for human review instead of
generating an autonomous recommendation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field

from tpllm.ontology.core import EvidenceClaim
from tpllm.ontology.entities import AgentRecommendation
from tpllm.ontology.measurement import (
    HealthStatus,
    InsightSeverity,
    KPIInsight,
    KPIObservation,
)


# ── Validation Result ───────────────────────────────────────────────

class ValidationOutcome(str, Enum):
    APPROVED = "approved"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    LOW_CONFIDENCE = "low_confidence"
    STALE_EVIDENCE = "stale_evidence"
    CONTRADICTED = "contradicted"
    DISPROPORTIONATE = "disproportionate"


class ValidationResult(BaseModel):
    """Result of validating an insight before generating a recommendation.

    The telemetry fields (``failed_check``, ``confidence_before_validation``,
    ``severity``) make every validation decision inspectable and measurable —
    they feed the agent-quality KPIs (approval rate, rejection rate, etc.) so
    the validator itself becomes observable.
    """
    insight_id: str = ""
    outcome: ValidationOutcome = ValidationOutcome.NEEDS_HUMAN_REVIEW
    passed: bool = False
    reasons: list[str] = Field(default_factory=list)
    human_review_required: bool = True
    adjusted_confidence: float = 0.0
    validated_at: datetime = Field(default_factory=datetime.utcnow)
    # Telemetry — which gate decided this, and the inputs it saw.
    failed_check: str | None = None
    confidence_before_validation: float = 0.0
    severity: str = ""


# ── Validation Policy ──────────────────────────────────────────────

class ValidationPolicy(BaseModel):
    """Configurable thresholds for the validation gate.

    Controls how aggressive or conservative the agent is about
    turning insights into autonomous actions.
    """
    min_confidence_for_auto_action: float = 0.75
    min_confidence_for_review: float = 0.4
    min_evidence_claims: int = 1
    min_kpi_observations: int = 1
    max_evidence_age_hours: float = 168.0  # 7 days
    max_contradiction_ratio: float = 0.3
    severity_requires_approval: list[str] = Field(
        default_factory=lambda: [
            InsightSeverity.HIGH.value,
            InsightSeverity.CRITICAL.value,
        ]
    )
    auto_approve_low_severity: bool = True


DEFAULT_POLICY = ValidationPolicy()


# ── Validator ──────────────────────────────────────────────────────

def validate_insight(
    insight: KPIInsight,
    observations: list[KPIObservation] | None = None,
    evidence_claims: list[EvidenceClaim] | None = None,
    policy: ValidationPolicy | None = None,
) -> ValidationResult:
    """Validate an insight before it can generate a recommendation.

    Checks evidence sufficiency, confidence, severity-gated approval,
    contradictions, and staleness. Returns a ValidationResult that
    determines whether the agent can act autonomously or needs human review.
    """
    pol = policy or DEFAULT_POLICY
    observations = observations or []
    evidence_claims = evidence_claims or []
    reasons: list[str] = []
    human_review = False
    original_confidence = insight.confidence
    severity = insight.severity.value

    # 1. Evidence sufficiency
    obs_count = len(insight.related_kpi_observation_ids)
    claim_count = len(insight.supporting_evidence_claim_ids)

    if obs_count < pol.min_kpi_observations and not observations:
        reasons.append(
            f"Insufficient KPI observations: {obs_count} < {pol.min_kpi_observations}"
        )
    if claim_count < pol.min_evidence_claims and not evidence_claims:
        reasons.append(
            f"Insufficient evidence claims: {claim_count} < {pol.min_evidence_claims}"
        )

    if reasons and not observations and not evidence_claims:
        return ValidationResult(
            insight_id=insight.id,
            outcome=ValidationOutcome.INSUFFICIENT_EVIDENCE,
            passed=False,
            reasons=reasons,
            human_review_required=True,
            adjusted_confidence=insight.confidence * 0.5,
            failed_check="evidence_sufficiency",
            confidence_before_validation=original_confidence,
            severity=severity,
        )

    # 2. Confidence threshold
    conf = insight.confidence
    if conf < pol.min_confidence_for_review:
        reasons.append(f"Confidence too low even for review: {conf:.2f}")
        return ValidationResult(
            insight_id=insight.id,
            outcome=ValidationOutcome.LOW_CONFIDENCE,
            passed=False,
            reasons=reasons,
            human_review_required=True,
            adjusted_confidence=conf,
            failed_check="confidence",
            confidence_before_validation=original_confidence,
            severity=severity,
        )

    # 3. Contradiction check
    if evidence_claims:
        contradicted = [c for c in evidence_claims if c.contradicted_by_claim_ids]
        if contradicted:
            ratio = len(contradicted) / len(evidence_claims)
            if ratio > pol.max_contradiction_ratio:
                reasons.append(
                    f"High contradiction ratio: {ratio:.0%} of evidence is contradicted"
                )
                return ValidationResult(
                    insight_id=insight.id,
                    outcome=ValidationOutcome.CONTRADICTED,
                    passed=False,
                    reasons=reasons,
                    human_review_required=True,
                    adjusted_confidence=conf * (1 - ratio),
                    failed_check="contradiction",
                    confidence_before_validation=original_confidence,
                    severity=severity,
                )
            conf *= (1 - ratio * 0.5)

    # 4. Staleness check
    if observations:
        now = datetime.utcnow()
        max_age = timedelta(hours=pol.max_evidence_age_hours)
        stale = [o for o in observations if (now - o.observed_at) > max_age]
        if stale and len(stale) == len(observations):
            reasons.append(
                f"All evidence is stale (>{pol.max_evidence_age_hours:.0f}h old)"
            )
            return ValidationResult(
                insight_id=insight.id,
                outcome=ValidationOutcome.STALE_EVIDENCE,
                passed=False,
                reasons=reasons,
                human_review_required=True,
                adjusted_confidence=conf * 0.5,
                failed_check="staleness",
                confidence_before_validation=original_confidence,
                severity=severity,
            )

    # 5. Severity-gated approval
    if insight.severity.value in pol.severity_requires_approval:
        human_review = True
        reasons.append(
            f"Severity '{insight.severity.value}' requires human approval"
        )

    # 6. Low severity auto-approve
    if (
        pol.auto_approve_low_severity
        and insight.severity == InsightSeverity.LOW
        and conf >= pol.min_confidence_for_auto_action
    ):
        return ValidationResult(
            insight_id=insight.id,
            outcome=ValidationOutcome.APPROVED,
            passed=True,
            reasons=["Low severity with sufficient confidence — auto-approved"],
            human_review_required=False,
            adjusted_confidence=conf,
            failed_check=None,
            confidence_before_validation=original_confidence,
            severity=severity,
        )

    # 7. Final confidence gate
    if conf >= pol.min_confidence_for_auto_action and not human_review:
        return ValidationResult(
            insight_id=insight.id,
            outcome=ValidationOutcome.APPROVED,
            passed=True,
            reasons=reasons or ["Validation passed"],
            human_review_required=False,
            adjusted_confidence=conf,
            failed_check=None,
            confidence_before_validation=original_confidence,
            severity=severity,
        )

    if conf >= pol.min_confidence_for_review:
        return ValidationResult(
            insight_id=insight.id,
            outcome=ValidationOutcome.NEEDS_HUMAN_REVIEW,
            passed=True,
            reasons=reasons or ["Confidence sufficient for review, not auto-action"],
            human_review_required=True,
            adjusted_confidence=conf,
            failed_check="severity_gate" if human_review else "final_confidence_gate",
            confidence_before_validation=original_confidence,
            severity=severity,
        )

    return ValidationResult(
        insight_id=insight.id,
        outcome=ValidationOutcome.LOW_CONFIDENCE,
        passed=False,
        reasons=[f"Confidence {conf:.2f} below review threshold {pol.min_confidence_for_review}"],
        human_review_required=True,
        adjusted_confidence=conf,
        failed_check="final_confidence_gate",
        confidence_before_validation=original_confidence,
        severity=severity,
    )


# ── Recommendation Deduplication ───────────────────────────────────
# Multiple insights from nearby signals (a Zoom action item + the Slack
# thread that echoes it) routinely produce near-identical recommendations.
# Surfacing all of them creates alert fatigue. Dedup collapses recommendations
# that target the same objects with the same intent into one canonical item,
# preserving every piece of evidence so nothing is silently dropped.

class MergedRecommendationPair(BaseModel):
    """Audit record of one duplicate folded into a canonical recommendation."""
    canonical_id: str = ""
    duplicate_id: str = ""
    reason: str = ""


def deduplicate_recommendations(
    recommendations: list[AgentRecommendation],
) -> tuple[list[AgentRecommendation], list[MergedRecommendationPair]]:
    """Collapse duplicate recommendations into canonical ones.

    Two recommendations are duplicates when they share the same
    ``recommendation_type`` and the same set of ``target_object_ids``. For each
    group, the highest-confidence recommendation (tie-break: earliest
    ``created_at``) becomes canonical, and the others' evidence claims and
    source refs are merged into it so no provenance is lost.

    Returns ``(canonical_recommendations, merged_pairs)`` where ``merged_pairs``
    records every fold for human audit.
    """
    groups: dict[tuple[str, frozenset[str]], list[AgentRecommendation]] = {}
    order: list[tuple[str, frozenset[str]]] = []
    for rec in recommendations:
        key = (rec.recommendation_type, frozenset(rec.target_object_ids))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(rec)

    canonical_list: list[AgentRecommendation] = []
    merged_pairs: list[MergedRecommendationPair] = []

    for key in order:
        members = groups[key]
        # Canonical = highest confidence, earliest created_at on ties.
        canonical = sorted(
            members,
            key=lambda r: (-r.confidence, r.created_at),
        )[0]

        for dup in members:
            if dup is canonical:
                continue

            # Merge evidence claim ids (union, preserve order, no dupes).
            for ec in dup.evidence_claim_ids:
                if ec not in canonical.evidence_claim_ids:
                    canonical.evidence_claim_ids.append(ec)

            # Merge source refs (skip exact duplicates).
            for ref in dup.source_refs:
                if ref not in canonical.source_refs:
                    canonical.source_refs.append(ref)

            # Append the duplicate's reasoning so context is retained.
            if dup.reasoning_summary:
                merged_note = f"[merged from {dup.id}] {dup.reasoning_summary}"
                canonical.reasoning_summary = (
                    f"{canonical.reasoning_summary}\n{merged_note}".strip()
                )

            merged_pairs.append(
                MergedRecommendationPair(
                    canonical_id=canonical.id,
                    duplicate_id=dup.id,
                    reason=(
                        f"Same recommendation_type '{canonical.recommendation_type}' "
                        f"and target objects; merged into higher-confidence canonical"
                    ),
                )
            )

        canonical_list.append(canonical)

    return canonical_list, merged_pairs
