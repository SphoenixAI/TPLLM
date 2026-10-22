"""AI quality measurement.

Measures the AI's own behavior — evidence coverage, unsupported claims,
unnecessary or repeated questions, self-resolution, contradiction
detection, staleness awareness, structural technical depth, and forecast
calibration. It never scores the people the AI works with.

Technical depth is measured structurally (are the mechanism, interfaces,
failure modes, and observability actually modeled?), never by prose
length. Forecast calibration (Brier score) is only computed when real
launch outcomes exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from tpllm.intelligence.project_understanding import (
    ProjectEvidence,
    ProjectUnderstandingSnapshot,
    UnderstandingClaim,
    UnderstandingClaimStatus,
)
from tpllm.intelligence.launch_readiness import (
    LaunchOutcomeRecord,
    LaunchReadinessSnapshot,
)
from tpllm.intelligence.self_research import (
    AIQualityRiskFlag,
    AIQualityRiskType,
    ClarificationNecessity,
    ClarificationQuestion,
    GapResolutionStatus,
    QuestionHistoryRecord,
    ResearchAttempt,
    ResearchResolution,
    is_repeated_question,
)


# ── Models ─────────────────────────────────────────────────────────────

class TechnicalDepthAssessment(BaseModel):
    project_id: str
    mechanism_depth: float
    architecture_depth: float
    interface_depth: float
    failure_mode_depth: float
    observability_depth: float
    overall_depth: float
    missing_elements: list[str] = Field(default_factory=list)
    explanation: str


class AIQualityMetrics(BaseModel):
    evidence_coverage: float
    technical_depth: float
    self_resolution_rate: float
    clarification_precision: float
    repeated_question_rate: float
    unnecessary_question_rate: float
    contradiction_detection_rate: float
    staleness_awareness_rate: float
    unsupported_claim_rate: float
    overconfident_inference_rate: float
    forecast_calibration_error: Optional[float] = None


class AIQualityReport(BaseModel):
    id: str
    project_id: str
    as_of: datetime
    metrics: AIQualityMetrics
    technical_depth: TechnicalDepthAssessment
    risk_flags: list[AIQualityRiskFlag] = Field(default_factory=list)
    highest_priority_improvements: list[str] = Field(default_factory=list)
    explanation: str


# ── Technical depth ──────────────────────────────────────────────────

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def assess_technical_depth(
    *,
    understanding: ProjectUnderstandingSnapshot,
) -> TechnicalDepthAssessment:
    """Structured field coverage, never prose length."""
    mechanism = understanding.technical_mechanism
    architecture = understanding.architecture

    mechanism_depth = _clamp01(
        (min(1.0, len(mechanism.steps) / 3.0)) * 0.5
        + (0.25 if mechanism.inputs and mechanism.outputs else 0.0)
        + (0.25 if mechanism.mechanism_summary else 0.0)
    )
    architecture_depth = _clamp01(
        min(1.0, len(architecture.components) / 2.0) * 0.5
        + min(1.0, len(architecture.relations) / 2.0) * 0.5
    )
    interface_depth = _clamp01(
        (0.6 if mechanism.data_contracts else 0.0)
        + min(1.0, len(architecture.relations) / 2.0) * 0.4
    )
    failure_mode_depth = _clamp01(
        min(1.0, len(mechanism.known_failure_modes) / 2.0)
    )
    observability_depth = _clamp01(
        1.0 if mechanism.observability_signals else 0.0
    )

    values = [
        mechanism_depth,
        architecture_depth,
        interface_depth,
        failure_mode_depth,
        observability_depth,
    ]
    overall = sum(values) / len(values)

    missing: list[str] = []
    if mechanism_depth < 0.5:
        missing.append("technical mechanism steps (inputs → steps → outputs)")
    if architecture_depth < 0.5:
        missing.append("architecture components and relations")
    if interface_depth < 0.5:
        missing.append("interface contracts and boundaries")
    if failure_mode_depth < 0.5:
        missing.append("failure modes")
    if observability_depth < 0.5:
        missing.append("observability signals")

    if missing:
        explanation = (
            f"Project '{understanding.project_id}' understanding lacks: "
            + "; ".join(missing)
            + ". Depth is structural — narrative alone does not count."
        )
    else:
        explanation = (
            f"Project '{understanding.project_id}' has structurally deep "
            f"understanding across mechanism, architecture, interfaces, "
            f"failure modes, and observability."
        )

    return TechnicalDepthAssessment(
        project_id=understanding.project_id,
        mechanism_depth=mechanism_depth,
        architecture_depth=architecture_depth,
        interface_depth=interface_depth,
        failure_mode_depth=failure_mode_depth,
        observability_depth=observability_depth,
        overall_depth=overall,
        missing_elements=missing,
        explanation=explanation,
    )


# ── Claim quality gate ───────────────────────────────────────────────

_STALE_CLAIM_AGE_DAYS = 90


def assess_claim_quality(
    *,
    claim: UnderstandingClaim,
    evidence_by_id: dict[str, ProjectEvidence],
    as_of: datetime,
) -> list[AIQualityRiskFlag]:
    """Flag unsupported, overconfident, stale-unlabeled, and
    contradiction-ignoring claims."""
    flags: list[AIQualityRiskFlag] = []

    def flag(risk_type: AIQualityRiskType, severity: str, explanation: str) -> None:
        flags.append(
            AIQualityRiskFlag(
                id=f"flag-{risk_type.value}-{claim.id}",
                project_id=claim.project_id,
                risk_type=risk_type,
                severity=severity,
                claim_ids=[claim.id],
                explanation=explanation,
            )
        )

    resolvable = [e for e in claim.evidence_ids if e in evidence_by_id]

    if claim.status == UnderstandingClaimStatus.SUPPORTED:
        if not claim.evidence_ids or (evidence_by_id and not resolvable):
            flag(
                AIQualityRiskType.UNSUPPORTED_CLAIM,
                "high",
                f"Claim '{claim.statement[:80]}' is marked supported but its "
                f"evidence cannot be resolved.",
            )
        if claim.inferred:
            flag(
                AIQualityRiskType.OVERCONFIDENT_INFERENCE,
                "high",
                f"Claim '{claim.statement[:80]}' is an inference presented as "
                f"supported fact.",
            )
        if evidence_by_id and len(resolvable) == 1:
            source = evidence_by_id[resolvable[0]].source
            if claim.confidence > 0.8 and source.reliability < 0.4:
                flag(
                    AIQualityRiskType.OVERCONFIDENT_INFERENCE,
                    "medium",
                    f"Claim '{claim.statement[:80]}' carries high confidence "
                    f"from a single low-reliability source.",
                )

    if claim.confidence >= 1.0:
        flag(
            AIQualityRiskType.OVERCONFIDENT_INFERENCE,
            "high",
            f"Claim '{claim.statement[:80]}' has confidence 1.0 — certainty "
            f"is never warranted.",
        )

    if (
        claim.last_supported_at is not None
        and (as_of - claim.last_supported_at).days > _STALE_CLAIM_AGE_DAYS
        and claim.status
        not in (UnderstandingClaimStatus.STALE, UnderstandingClaimStatus.CONTESTED)
    ):
        flag(
            AIQualityRiskType.STALE_UNDERSTANDING,
            "medium",
            f"Claim '{claim.statement[:80]}' has not been supported in over "
            f"{_STALE_CLAIM_AGE_DAYS} days and is not labeled stale.",
        )

    if (
        claim.contradicting_evidence_ids
        and claim.status != UnderstandingClaimStatus.CONTESTED
    ):
        flag(
            AIQualityRiskType.CONTRADICTION_IGNORED,
            "high",
            f"Claim '{claim.statement[:80]}' has contradicting evidence but "
            f"is not marked contested.",
        )

    return flags


def can_emit_project_claim(
    *,
    claim: UnderstandingClaim,
    evidence_by_id: dict[str, ProjectEvidence],
    as_of: datetime,
) -> bool:
    """False when a claim is unsupported or dangerously overconfident."""
    flags = assess_claim_quality(
        claim=claim, evidence_by_id=evidence_by_id, as_of=as_of
    )
    return not any(f.severity == "high" for f in flags)


# ── Metrics ──────────────────────────────────────────────────────────

def compute_ai_quality_metrics(
    *,
    understanding: ProjectUnderstandingSnapshot,
    research_attempts: list[ResearchAttempt],
    resolutions: list[ResearchResolution],
    clarification_questions: list[ClarificationQuestion],
    question_history: list[QuestionHistoryRecord],
    claim_risk_flags: list[AIQualityRiskFlag],
    readiness_history: list[LaunchReadinessSnapshot],
    launch_outcomes: list[LaunchOutcomeRecord],
) -> AIQualityMetrics:
    """Metrics that evaluate the AI's behavior — never the people it
    works with."""
    # Self-resolution: resolved without a human / researchable gaps.
    researchable = [
        r
        for r in resolutions
        if r.status != GapResolutionStatus.HUMAN_DECISION_REQUIRED
    ]
    resolved = [
        r for r in researchable if r.status == GapResolutionStatus.RESOLVED
    ]
    self_resolution_rate = (
        len(resolved) / len(researchable) if researchable else 0.0
    )

    total_questions = len(clarification_questions)
    necessary = [
        q
        for q in clarification_questions
        if q.necessity
        in (
            ClarificationNecessity.REQUIRED,
            ClarificationNecessity.STRATEGIC_DECISION_REQUIRED,
        )
    ]
    clarification_precision = (
        len(necessary) / total_questions if total_questions else 1.0
    )
    unnecessary = [
        q
        for q in clarification_questions
        if q.necessity == ClarificationNecessity.NOT_NEEDED
    ]
    unnecessary_question_rate = (
        len(unnecessary) / total_questions if total_questions else 0.0
    )
    repeated = [
        q
        for q in clarification_questions
        if is_repeated_question(question=q, history=question_history)
    ]
    repeated_question_rate = (
        len(repeated) / total_questions if total_questions else 0.0
    )

    # Contradiction detection: contested claims covered by a record.
    contested_claims = [
        c
        for c in understanding.claims
        if c.status == UnderstandingClaimStatus.CONTESTED
    ]
    covered_claim_ids = {
        cid
        for contradiction in understanding.contradictions
        for cid in (contradiction.claim_a_id, contradiction.claim_b_id)
    }
    contradiction_detection_rate = (
        sum(1 for c in contested_claims if c.id in covered_claim_ids)
        / len(contested_claims)
        if contested_claims
        else 1.0
    )

    # Staleness awareness: old claims correctly labeled STALE.
    old_claims = [
        c
        for c in understanding.claims
        if c.last_supported_at is not None
        and (understanding.as_of - c.last_supported_at).days > _STALE_CLAIM_AGE_DAYS
    ]
    staleness_awareness_rate = (
        sum(
            1
            for c in old_claims
            if c.status == UnderstandingClaimStatus.STALE
        )
        / len(old_claims)
        if old_claims
        else 1.0
    )

    claim_count = len(understanding.claims)
    unsupported_flagged = {
        cid
        for f in claim_risk_flags
        if f.risk_type == AIQualityRiskType.UNSUPPORTED_CLAIM
        for cid in f.claim_ids
    }
    overconfident_flagged = {
        cid
        for f in claim_risk_flags
        if f.risk_type == AIQualityRiskType.OVERCONFIDENT_INFERENCE
        for cid in f.claim_ids
    }
    unsupported_claim_rate = (
        len(unsupported_flagged) / claim_count if claim_count else 0.0
    )
    overconfident_inference_rate = (
        len(overconfident_flagged) / claim_count if claim_count else 0.0
    )

    # Forecast calibration: Brier score, only when outcomes exist.
    forecast_calibration_error: float | None = None
    if launch_outcomes and readiness_history:
        forecasts_by_launch = {
            snapshot.forecast.launch_id: snapshot.forecast
            for snapshot in readiness_history
        }
        squared_errors: list[float] = []
        for outcome in launch_outcomes:
            if outcome.launched_on_time is None:
                continue
            forecast = forecasts_by_launch.get(outcome.launch_id)
            if forecast is None:
                continue
            actual = 1.0 if outcome.launched_on_time else 0.0
            squared_errors.append(
                (forecast.on_time_probability - actual) ** 2
            )
        if squared_errors:
            forecast_calibration_error = sum(squared_errors) / len(squared_errors)

    depth = assess_technical_depth(understanding=understanding)

    return AIQualityMetrics(
        evidence_coverage=understanding.coverage.overall_coverage,
        technical_depth=depth.overall_depth,
        self_resolution_rate=_clamp01(self_resolution_rate),
        clarification_precision=_clamp01(clarification_precision),
        repeated_question_rate=_clamp01(repeated_question_rate),
        unnecessary_question_rate=_clamp01(unnecessary_question_rate),
        contradiction_detection_rate=_clamp01(contradiction_detection_rate),
        staleness_awareness_rate=_clamp01(staleness_awareness_rate),
        unsupported_claim_rate=_clamp01(unsupported_claim_rate),
        overconfident_inference_rate=_clamp01(overconfident_inference_rate),
        forecast_calibration_error=forecast_calibration_error,
    )


# ── Report ───────────────────────────────────────────────────────────

def build_ai_quality_report(
    *,
    project_id: str,
    understanding: ProjectUnderstandingSnapshot,
    readiness_history: list[LaunchReadinessSnapshot],
    launch_outcomes: list[LaunchOutcomeRecord],
    research_attempts: list[ResearchAttempt],
    resolutions: list[ResearchResolution],
    clarification_questions: list[ClarificationQuestion],
    question_history: list[QuestionHistoryRecord],
    as_of: datetime,
) -> AIQualityReport:
    """Quality report with specific, actionable recommendations about the
    AI's own understanding — never vague 'improve depth' advice."""
    claim_flags: list[AIQualityRiskFlag] = []
    for claim in understanding.claims:
        claim_flags.extend(
            assess_claim_quality(claim=claim, evidence_by_id={}, as_of=as_of)
        )

    depth = assess_technical_depth(understanding=understanding)
    if depth.overall_depth < 0.4:
        claim_flags.append(
            AIQualityRiskFlag(
                id=f"flag-shallow-{project_id}",
                project_id=project_id,
                risk_type=AIQualityRiskType.SHALLOW_TECHNICAL_MODEL,
                severity="high",
                explanation=depth.explanation,
            )
        )

    metrics = compute_ai_quality_metrics(
        understanding=understanding,
        research_attempts=research_attempts,
        resolutions=resolutions,
        clarification_questions=clarification_questions,
        question_history=question_history,
        claim_risk_flags=claim_flags,
        readiness_history=readiness_history,
        launch_outcomes=launch_outcomes,
    )

    improvements: list[str] = []
    for element in depth.missing_elements:
        improvements.append(
            f"The system's model of '{project_id}' lacks {element}. Resolve "
            f"this through internal research before producing another launch "
            f"forecast."
        )
    if metrics.unsupported_claim_rate > 0:
        improvements.append(
            "Unsupported claims exist; re-derive them from evidence or "
            "demote them to unknowns."
        )
    if metrics.repeated_question_rate > 0:
        improvements.append(
            "Repeated questions were asked; consult question history before "
            "asking again."
        )
    if metrics.unnecessary_question_rate > 0:
        improvements.append(
            "Questions were asked whose answers already existed in evidence; "
            "tighten the clarification gate."
        )
    unresolved_contested = [
        r for r in resolutions if r.status == GapResolutionStatus.CONTESTED
    ]
    if unresolved_contested:
        improvements.append(
            f"{len(unresolved_contested)} contested gap(s) remain; surface "
            f"the specific conflicts to their owners rather than averaging "
            f"the evidence."
        )

    explanation = (
        f"AI quality for '{project_id}' as of {as_of.date().isoformat()}: "
        f"evidence coverage {metrics.evidence_coverage:.2f}, technical depth "
        f"{metrics.technical_depth:.2f}, self-resolution "
        f"{metrics.self_resolution_rate:.2f}, clarification precision "
        f"{metrics.clarification_precision:.2f}. These metrics evaluate the "
        f"AI's behavior only."
    )

    return AIQualityReport(
        id=f"aiq-{project_id}-{as_of.isoformat()}",
        project_id=project_id,
        as_of=as_of,
        metrics=metrics,
        technical_depth=depth,
        risk_flags=claim_flags,
        highest_priority_improvements=improvements,
        explanation=explanation,
    )
