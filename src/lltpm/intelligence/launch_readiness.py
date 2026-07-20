"""Predictive launch readiness scorecard (Commit 12).

Consumes the Commit 11 ProjectUnderstandingSnapshot plus ontology and
structural signals to produce dimension-level readiness, overall
readiness, confidence, blockers, trend, on-time probability, and
goal-completion estimates — every score traceable to evidence.

Core invariants:
- Readiness and confidence are different numbers and are never collapsed.
- Every scorecard and forecast carries ``as_of``; evidence observed after
  it never contributes (no future leakage).
- The initial predictor is honestly labeled HEURISTIC_V1 — a transparent
  formula, not a trained model. Training schemas exist for a future
  learned predictor and reject target leakage.
- Blockers must affect score and forecast.
- Ticket-completion counts and meeting optimism are capped so they cannot
  dominate readiness over concrete integration evidence.
- This layer informs decisions; it never imports from the application
  layer and never grants application authority.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel, Field, model_validator

from lltpm.intelligence.project_understanding import (
    ClaimContradiction,
    ProjectUnderstandingSnapshot,
    ProjectUnknown,
    ProjectUnknownKind,
    ResearchPriority,
    UnderstandingClaimStatus,
    UnderstandingClaimType,
)


# ── Enums ──────────────────────────────────────────────────────────────

class LaunchReadinessDimensionType(str, Enum):
    SCOPE_CLARITY = "scope_clarity"
    DEPENDENCY_HEALTH = "dependency_health"
    OWNERSHIP_CLARITY = "ownership_clarity"
    TECHNICAL_CONFIDENCE = "technical_confidence"
    INTEGRATION_READINESS = "integration_readiness"
    VALIDATION_READINESS = "validation_readiness"
    DECISION_HEALTH = "decision_health"
    OPERATIONAL_READINESS = "operational_readiness"
    BUSINESS_ALIGNMENT = "business_alignment"
    LAUNCH_BLOCKER_HEALTH = "launch_blocker_health"


class ReadinessSignalDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class LaunchRiskBand(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class LaunchForecastMethod(str, Enum):
    HEURISTIC_V1 = "heuristic_v1"
    LEARNED_MODEL = "learned_model"


class LaunchBlockerSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceFreshnessBand(str, Enum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    UNKNOWN = "unknown"


# ── Launch definition ────────────────────────────────────────────────

class LaunchDefinition(BaseModel):
    """A launch cannot be scored meaningfully without a target date,
    required scope, and success criteria; missing scope/criteria lower
    confidence and emit blockers (the target date is structurally
    required)."""
    id: str
    project_id: str
    launch_name: str
    target_date: datetime
    forecast_horizon_start: datetime
    required_scope: list[str] = Field(default_factory=list)
    excluded_scope: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    operational_requirements: list[str] = Field(default_factory=list)
    critical_dependency_ids: list[str] = Field(default_factory=list)
    business_criticality: str = "standard"
    evidence_ids: list[str] = Field(default_factory=list)


# ── Evidence contribution ────────────────────────────────────────────

class ReadinessEvidenceContribution(BaseModel):
    id: str
    project_id: str
    launch_id: str
    dimension: LaunchReadinessDimensionType
    direction: ReadinessSignalDirection
    strength: float
    confidence: float
    source_reliability: float
    freshness_factor: float
    evidence_ids: list[str] = Field(default_factory=list)
    observed_at: datetime
    rationale: str
    blocker_candidate: bool = False

    @model_validator(mode="after")
    def _check_bounds(self) -> "ReadinessEvidenceContribution":
        for name in ("strength", "confidence", "source_reliability", "freshness_factor"):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be within [0, 1], got {value}.")
        if not self.rationale:
            raise ValueError("Every contribution must have a rationale.")
        return self


# ── Dimension model ──────────────────────────────────────────────────

class LaunchReadinessDimension(BaseModel):
    dimension: LaunchReadinessDimensionType
    score: float
    confidence: float
    evidence_count: int
    positive_evidence_ids: list[str] = Field(default_factory=list)
    negative_evidence_ids: list[str] = Field(default_factory=list)
    contradiction_count: int = 0
    stale_evidence_count: int = 0
    top_positive_factors: list[str] = Field(default_factory=list)
    top_risks: list[str] = Field(default_factory=list)
    recommended_next_action: Optional[str] = None

    @model_validator(mode="after")
    def _check_bounds(self) -> "LaunchReadinessDimension":
        if not (0.0 <= self.score <= 100.0):
            raise ValueError("score must be within [0, 100].")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be within [0, 1].")
        return self


# ── Blocker model ────────────────────────────────────────────────────

class LaunchBlocker(BaseModel):
    id: str
    project_id: str
    launch_id: str
    title: str
    description: str
    severity: LaunchBlockerSeverity
    affected_dimension: LaunchReadinessDimensionType
    dependency_ids: list[str] = Field(default_factory=list)
    owner_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    estimated_impact_days: Optional[float] = None
    confidence: float = 0.0
    resolved: bool = False

    @model_validator(mode="after")
    def _check_evidence(self) -> "LaunchBlocker":
        if not self.evidence_ids:
            raise ValueError("A blocker must have evidence.")
        return self


# ── Scorecard model ──────────────────────────────────────────────────

class LaunchReadinessScorecard(BaseModel):
    id: str
    project_id: str
    launch_id: str
    as_of: datetime
    overall_score: float
    overall_confidence: float
    dimensions: list[LaunchReadinessDimension] = Field(default_factory=list)
    blockers: list[LaunchBlocker] = Field(default_factory=list)
    critical_unknown_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    top_reasons_for_score: list[str] = Field(default_factory=list)
    highest_leverage_actions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ── Forecasting models ───────────────────────────────────────────────

class LaunchFeatureVector(BaseModel):
    project_id: str
    launch_id: str
    as_of: datetime
    dimension_scores: dict[str, float]
    dimension_confidences: dict[str, float]
    critical_blocker_count: int
    high_blocker_count: int
    unresolved_critical_unknown_count: int
    contradiction_count: int
    dependency_blast_radius: float = 0.0
    decision_bottleneck_score: float = 0.0
    evidence_freshness: float = 0.0
    readiness_trend_slope: float = 0.0
    days_until_target: int = 0


class LaunchForecast(BaseModel):
    id: str
    project_id: str
    launch_id: str
    as_of: datetime
    method: LaunchForecastMethod
    on_time_probability: float
    expected_slip_days: float
    lower_slip_days: float
    upper_slip_days: float
    risk_band: LaunchRiskBand
    confidence: float
    principal_risk_drivers: list[str] = Field(default_factory=list)
    principal_positive_drivers: list[str] = Field(default_factory=list)
    feature_vector: LaunchFeatureVector


class GoalEstimate(BaseModel):
    id: str
    project_id: str
    launch_id: str
    goal_id: str
    goal_name: str
    completion_probability: float
    estimated_completion_date: Optional[datetime] = None
    lower_completion_date: Optional[datetime] = None
    upper_completion_date: Optional[datetime] = None
    confidence: float
    blocker_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class LaunchReadinessSnapshot(BaseModel):
    id: str
    scorecard: LaunchReadinessScorecard
    forecast: LaunchForecast
    goal_estimates: list[GoalEstimate] = Field(default_factory=list)


class LaunchReadinessTrend(BaseModel):
    project_id: str
    launch_id: str
    previous_snapshot_id: str
    current_snapshot_id: str
    score_delta: float
    confidence_delta: float
    dimension_deltas: dict[str, float]
    improving_dimensions: list[str] = Field(default_factory=list)
    deteriorating_dimensions: list[str] = Field(default_factory=list)
    newly_opened_blocker_ids: list[str] = Field(default_factory=list)
    newly_resolved_blocker_ids: list[str] = Field(default_factory=list)
    summary: str


# ── Future training models ───────────────────────────────────────────

class LaunchOutcomeRecord(BaseModel):
    id: str
    project_id: str
    launch_id: str
    target_date: datetime
    actual_launch_date: Optional[datetime] = None
    launched_on_time: Optional[bool] = None
    slip_days: Optional[float] = None
    scope_delivered: list[str] = Field(default_factory=list)
    scope_removed: list[str] = Field(default_factory=list)
    major_incident_count: int = 0
    outcome_observed_at: datetime


class LaunchTrainingExample(BaseModel):
    feature_vector: LaunchFeatureVector
    outcome: LaunchOutcomeRecord
    evidence_cutoff_at: datetime


def validate_launch_training_example(example: LaunchTrainingExample) -> None:
    """Reject target leakage before any learned model ever trains."""
    fv = example.feature_vector
    outcome = example.outcome
    if outcome.outcome_observed_at <= fv.as_of:
        raise ValueError(
            "Outcome must be observed after the prediction as_of — the "
            "feature vector would otherwise contain the target."
        )
    if example.evidence_cutoff_at > outcome.outcome_observed_at:
        raise ValueError(
            "Evidence cutoff after the launch outcome leaks post-launch "
            "evidence into the features."
        )
    if fv.as_of > example.evidence_cutoff_at:
        raise ValueError(
            "Feature vector as_of is beyond the evidence cutoff — "
            "post-cutoff evidence leaked into features."
        )
    if (
        outcome.launched_on_time is True
        and outcome.slip_days is not None
        and outcome.slip_days > 0
    ):
        raise ValueError("Malformed outcome: on-time launch with positive slip.")


# ── Predictor interface ──────────────────────────────────────────────

class LaunchReadinessPredictor(Protocol):
    def predict(
        self,
        *,
        scorecard: LaunchReadinessScorecard,
        feature_vector: LaunchFeatureVector,
    ) -> LaunchForecast:
        ...


# ── Scoring configuration ────────────────────────────────────────────

def _default_dimension_weights() -> dict[str, float]:
    return {d.value: 0.1 for d in LaunchReadinessDimensionType}


class ReadinessScoringConfig(BaseModel):
    dimension_weights: dict[str, float] = Field(
        default_factory=_default_dimension_weights
    )
    contradiction_penalty_per_item: float = 3.0
    stale_evidence_penalty: float = 1.5
    critical_blocker_penalty: float = 20.0
    high_blocker_penalty: float = 8.0
    freshness_half_life_days: float = 21.0
    minimum_evidence_per_dimension: int = 2


# ── Helpers ──────────────────────────────────────────────────────────

_DIRECTION_VALUE = {
    ReadinessSignalDirection.POSITIVE: 1.0,
    ReadinessSignalDirection.NEGATIVE: -1.0,
    ReadinessSignalDirection.NEUTRAL: 0.0,
}

# Vanity/optimism caps: these evidence kinds cannot dominate readiness.
_TICKET_COMPLETION_STRENGTH_CAP = 0.3
_MEETING_OPTIMISM_STRENGTH_CAP = 0.4
_MEETING_OPTIMISM_RELIABILITY_CAP = 0.5

_STALE_FRESHNESS_THRESHOLD = 0.3

_UNKNOWN_DIMENSION_MAP: dict[ProjectUnknownKind, LaunchReadinessDimensionType] = {
    ProjectUnknownKind.CONFLICTING_EVIDENCE: LaunchReadinessDimensionType.SCOPE_CLARITY,
    ProjectUnknownKind.MISSING_DEPENDENCY: LaunchReadinessDimensionType.DEPENDENCY_HEALTH,
    ProjectUnknownKind.MISSING_OWNER: LaunchReadinessDimensionType.OWNERSHIP_CLARITY,
    ProjectUnknownKind.MISSING_TECHNICAL_MECHANISM: LaunchReadinessDimensionType.TECHNICAL_CONFIDENCE,
    ProjectUnknownKind.MISSING_SUCCESS_CRITERION: LaunchReadinessDimensionType.VALIDATION_READINESS,
    ProjectUnknownKind.MISSING_COMPANY_ALIGNMENT: LaunchReadinessDimensionType.BUSINESS_ALIGNMENT,
    ProjectUnknownKind.STALE_UNDERSTANDING: LaunchReadinessDimensionType.TECHNICAL_CONFIDENCE,
    ProjectUnknownKind.AMBIGUOUS_TERM: LaunchReadinessDimensionType.SCOPE_CLARITY,
    ProjectUnknownKind.MISSING_FACT: LaunchReadinessDimensionType.TECHNICAL_CONFIDENCE,
}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def freshness_factor(
    observed_at: datetime, as_of: datetime, half_life_days: float
) -> float:
    age_days = max(0.0, (as_of - observed_at).days)
    return math.exp(-age_days * math.log(2) / half_life_days)


def freshness_band(factor: float) -> EvidenceFreshnessBand:
    if factor >= 0.7:
        return EvidenceFreshnessBand.FRESH
    if factor >= 0.4:
        return EvidenceFreshnessBand.AGING
    if factor > 0.0:
        return EvidenceFreshnessBand.STALE
    return EvidenceFreshnessBand.UNKNOWN


# ── Evidence extraction ──────────────────────────────────────────────

def extract_readiness_evidence(
    *,
    understanding: ProjectUnderstandingSnapshot,
    launch: LaunchDefinition,
    ontology_records: list[object],
    structural_artifacts: list[object],
    as_of: datetime,
) -> list[ReadinessEvidenceContribution]:
    """Map understanding, launch definition, and structural/ontology
    artifacts into readiness contributions.

    Rules: evidence after ``as_of`` is excluded; every contribution has a
    rationale; repeated identical evidence does not multiply influence;
    meeting optimism and ticket completion are capped so they cannot
    outweigh concrete integration evidence.
    """
    contributions: list[ReadinessEvidenceContribution] = []
    seen: set[tuple[str, str, str]] = set()
    counter = 0
    half_life = ReadinessScoringConfig().freshness_half_life_days
    understanding_freshness = _clamp01(
        max(understanding.coverage.evidence_freshness, 0.1)
    )

    def add(
        dimension: LaunchReadinessDimensionType,
        direction: ReadinessSignalDirection,
        strength: float,
        rationale: str,
        evidence_ids: list[str],
        *,
        confidence: float = 0.6,
        reliability: float = 0.7,
        fresh: float | None = None,
        observed_at: datetime | None = None,
        blocker_candidate: bool = False,
    ) -> None:
        nonlocal counter
        key = (dimension.value, direction.value, rationale)
        if key in seen:
            return  # repeated copies of the same evidence do not multiply
        seen.add(key)
        counter += 1
        contributions.append(
            ReadinessEvidenceContribution(
                id=f"rec-{launch.id}-{counter}",
                project_id=understanding.project_id,
                launch_id=launch.id,
                dimension=dimension,
                direction=direction,
                strength=_clamp01(strength),
                confidence=_clamp01(confidence),
                source_reliability=_clamp01(reliability),
                freshness_factor=_clamp01(
                    fresh if fresh is not None else understanding_freshness
                ),
                evidence_ids=evidence_ids or [launch.id],
                observed_at=observed_at or as_of,
                rationale=rationale,
                blocker_candidate=blocker_candidate,
            )
        )

    D = LaunchReadinessDimensionType
    P = ReadinessSignalDirection.POSITIVE
    N = ReadinessSignalDirection.NEGATIVE

    # Launch definition completeness.
    if launch.required_scope:
        add(D.SCOPE_CLARITY, P, 0.7, "Required launch scope is defined.",
            launch.evidence_ids)
    else:
        add(D.SCOPE_CLARITY, N, 0.8, "Required launch scope is undefined.",
            [launch.id], blocker_candidate=True)
    if launch.excluded_scope:
        add(D.SCOPE_CLARITY, P, 0.4, "Non-goals are explicitly defined.",
            launch.evidence_ids)
    if launch.success_criteria:
        add(D.VALIDATION_READINESS, P, 0.6,
            "Launch success criteria are explicit.", launch.evidence_ids)
        add(D.SCOPE_CLARITY, P, 0.4,
            "Success criteria sharpen the launch scope.", launch.evidence_ids)
    else:
        add(D.VALIDATION_READINESS, N, 0.8,
            "Launch success criteria are missing.", [launch.id],
            blocker_candidate=True)

    # Understanding-derived signals.
    if understanding.purpose.one_sentence_purpose:
        add(D.BUSINESS_ALIGNMENT, P, 0.4,
            "Project purpose is evidence-backed.",
            understanding.purpose.evidence_ids)
    if understanding.significance.company_goal_links:
        add(D.BUSINESS_ALIGNMENT, P, 0.8,
            "Company goal link with an explicit mechanism exists.",
            understanding.significance.evidence_ids)
    else:
        add(D.BUSINESS_ALIGNMENT, N, 0.7,
            "No mechanistic company goal link is evidenced.", [launch.id])

    mechanism = understanding.technical_mechanism
    if mechanism.steps:
        add(D.TECHNICAL_CONFIDENCE, P,
            min(1.0, len(mechanism.steps) / 3.0) * 0.8,
            "Technical mechanism steps are understood.",
            mechanism.evidence_ids)
    if mechanism.known_failure_modes:
        add(D.TECHNICAL_CONFIDENCE, P, 0.5,
            "Failure modes are documented.", mechanism.evidence_ids)
    for question in mechanism.unresolved_mechanism_questions[:3]:
        add(D.TECHNICAL_CONFIDENCE, N, 0.4,
            f"Unresolved mechanism question: {question}",
            mechanism.evidence_ids)

    architecture = understanding.architecture
    if architecture.components:
        owned = sum(1 for c in architecture.components if c.owner_ids)
        ratio = owned / len(architecture.components)
        if ratio >= 0.99:
            add(D.OWNERSHIP_CLARITY, P, 0.7,
                "All architecture components have owners.",
                architecture.evidence_ids)
        elif ratio > 0:
            add(D.OWNERSHIP_CLARITY, P, 0.4 * ratio,
                "Some architecture components have owners.",
                architecture.evidence_ids)
    if architecture.relations:
        add(D.DEPENDENCY_HEALTH, P, 0.4,
            "Dependency relations are explicitly represented.",
            architecture.evidence_ids)

    # Unknowns → negative signals on their mapped dimension.
    for unknown in understanding.unknowns:
        dimension = _UNKNOWN_DIMENSION_MAP.get(
            unknown.kind, D.SCOPE_CLARITY
        )
        strength = {
            ResearchPriority.CRITICAL: 0.9,
            ResearchPriority.HIGH: 0.7,
            ResearchPriority.MEDIUM: 0.45,
            ResearchPriority.LOW: 0.25,
        }[unknown.priority]
        add(dimension, N, strength,
            f"Unresolved unknown: {unknown.question}",
            [unknown.id],
            blocker_candidate=unknown.priority
            in (ResearchPriority.CRITICAL, ResearchPriority.HIGH))

    # Contested scope claims.
    for claim in understanding.claims:
        if (
            claim.claim_type == UnderstandingClaimType.SCOPE
            and claim.status == UnderstandingClaimStatus.CONTESTED
        ):
            add(D.SCOPE_CLARITY, N, 0.8,
                f"Scope claim is contested: {claim.statement}",
                claim.evidence_ids, blocker_candidate=True)

    # Launch blocker health starts high; blockers reduce it at scoring.
    add(D.LAUNCH_BLOCKER_HEALTH, P, 0.9,
        "Baseline: no additional blocker evidence beyond listed blockers.",
        [launch.id])

    # Ontology / structural artifacts (duck-typed).
    for record in list(ontology_records) + list(structural_artifacts):
        raw_dimension = getattr(record, "readiness_dimension", None)
        if raw_dimension is None:
            continue
        try:
            dimension = LaunchReadinessDimensionType(
                getattr(raw_dimension, "value", raw_dimension)
            )
        except ValueError:
            continue
        observed_at = getattr(record, "observed_at", None) or as_of
        if observed_at > as_of:
            continue  # future evidence is never used
        raw_direction = getattr(record, "direction", "neutral")
        try:
            direction = ReadinessSignalDirection(
                getattr(raw_direction, "value", raw_direction)
            )
        except ValueError:
            direction = ReadinessSignalDirection.NEUTRAL
        strength = _clamp01(float(getattr(record, "strength", 0.5)))
        reliability = _clamp01(float(getattr(record, "reliability", 0.7)))
        kind = str(getattr(record, "evidence_kind", ""))
        if kind == "ticket_completion":
            strength = min(strength, _TICKET_COMPLETION_STRENGTH_CAP)
        if kind == "meeting_optimism":
            strength = min(strength, _MEETING_OPTIMISM_STRENGTH_CAP)
            reliability = min(reliability, _MEETING_OPTIMISM_RELIABILITY_CAP)
        record_id = str(
            getattr(record, "evidence_id", None) or getattr(record, "id", "artifact")
        )
        add(
            dimension,
            direction,
            strength,
            str(getattr(record, "rationale", f"Artifact signal {record_id}.")),
            [record_id],
            confidence=_clamp01(float(getattr(record, "confidence", 0.6))),
            reliability=reliability,
            fresh=freshness_factor(observed_at, as_of, half_life),
            observed_at=observed_at,
            blocker_candidate=bool(getattr(record, "blocker_candidate", False)),
        )

    return contributions


# ── Blocker identification ───────────────────────────────────────────

def identify_launch_blockers(
    *,
    understanding: ProjectUnderstandingSnapshot,
    launch: LaunchDefinition,
    contributions: list[ReadinessEvidenceContribution],
    structural_artifacts: list[object],
    as_of: datetime,
) -> list[LaunchBlocker]:
    """Blockers from critical unknowns, contested scope, missing launch
    fields, and blocker-candidate evidence. Deduplicated by canonical
    subject — five messages about the same unresolved contract yield one
    blocker."""
    blockers: list[LaunchBlocker] = []
    seen_titles: set[str] = set()
    counter = 0

    def add(
        title: str,
        description: str,
        severity: LaunchBlockerSeverity,
        dimension: LaunchReadinessDimensionType,
        evidence_ids: list[str],
        confidence: float = 0.6,
    ) -> None:
        nonlocal counter
        key = title.strip().lower()
        if key in seen_titles:
            return
        seen_titles.add(key)
        counter += 1
        blockers.append(
            LaunchBlocker(
                id=f"blocker-{launch.id}-{counter}",
                project_id=understanding.project_id,
                launch_id=launch.id,
                title=title,
                description=description,
                severity=severity,
                affected_dimension=dimension,
                evidence_ids=evidence_ids or [launch.id],
                confidence=_clamp01(confidence),
            )
        )

    if not launch.success_criteria:
        add(
            "Launch success criteria are missing",
            "The launch cannot be validated without explicit success criteria.",
            LaunchBlockerSeverity.HIGH,
            LaunchReadinessDimensionType.VALIDATION_READINESS,
            [launch.id],
        )
    if not launch.required_scope:
        add(
            "Required launch scope is undefined",
            "Readiness cannot be assessed against an undefined scope.",
            LaunchBlockerSeverity.HIGH,
            LaunchReadinessDimensionType.SCOPE_CLARITY,
            [launch.id],
        )

    for unknown in understanding.unknowns:
        if unknown.priority == ResearchPriority.CRITICAL:
            add(
                f"Critical unknown: {unknown.question}"[:120],
                unknown.why_it_matters,
                LaunchBlockerSeverity.CRITICAL,
                _UNKNOWN_DIMENSION_MAP.get(
                    unknown.kind, LaunchReadinessDimensionType.SCOPE_CLARITY
                ),
                [unknown.id],
                confidence=0.7,
            )

    for contradiction in understanding.contradictions:
        if contradiction.resolved:
            continue
        add(
            f"Contested evidence: {contradiction.contradiction_type}",
            contradiction.explanation,
            LaunchBlockerSeverity.HIGH,
            LaunchReadinessDimensionType.SCOPE_CLARITY,
            contradiction.evidence_ids or [contradiction.id],
            confidence=0.7,
        )

    for contribution in contributions:
        if not contribution.blocker_candidate:
            continue
        if contribution.direction != ReadinessSignalDirection.NEGATIVE:
            continue
        severity = (
            LaunchBlockerSeverity.CRITICAL
            if contribution.strength >= 0.85
            else LaunchBlockerSeverity.HIGH
        )
        add(
            contribution.rationale[:120],
            contribution.rationale,
            severity,
            contribution.dimension,
            contribution.evidence_ids,
            confidence=contribution.confidence,
        )

    return blockers


# ── Dimension scoring ────────────────────────────────────────────────

def score_readiness_dimension(
    *,
    dimension: LaunchReadinessDimensionType,
    contributions: list[ReadinessEvidenceContribution],
    contradictions: list[ClaimContradiction],
    blockers: list[LaunchBlocker],
    config: ReadinessScoringConfig,
) -> LaunchReadinessDimension:
    """Evidence-weighted score plus contradiction/staleness/blocker
    penalties. Confidence derives from coverage, source quality,
    freshness, and contradictions — never from the score itself."""
    relevant = [c for c in contributions if c.dimension == dimension]

    weighted_signal = sum(
        _DIRECTION_VALUE[c.direction]
        * c.strength
        * c.confidence
        * c.source_reliability
        * c.freshness_factor
        for c in relevant
    )
    weight_total = sum(
        c.confidence * c.source_reliability * c.freshness_factor
        for c in relevant
    )
    normalized_signal = weighted_signal / weight_total if weight_total else 0.0
    score = 50.0 + 50.0 * normalized_signal

    contradiction_count = len(contradictions)
    score -= config.contradiction_penalty_per_item * contradiction_count

    stale_count = sum(
        1 for c in relevant if c.freshness_factor < _STALE_FRESHNESS_THRESHOLD
    )
    score -= config.stale_evidence_penalty * stale_count

    for blocker in blockers:
        if blocker.resolved or blocker.affected_dimension != dimension:
            continue
        if blocker.severity == LaunchBlockerSeverity.CRITICAL:
            score -= config.critical_blocker_penalty
        elif blocker.severity == LaunchBlockerSeverity.HIGH:
            score -= config.high_blocker_penalty

    score = _clamp(score, 0.0, 100.0)

    if relevant:
        coverage = min(
            1.0, len(relevant) / max(1, config.minimum_evidence_per_dimension)
        )
        avg_quality = sum(
            c.confidence * c.source_reliability for c in relevant
        ) / len(relevant)
        avg_freshness = sum(c.freshness_factor for c in relevant) / len(relevant)
        contradiction_factor = 1.0 / (1.0 + 0.25 * contradiction_count)
        confidence = _clamp01(
            coverage * avg_quality * avg_freshness * contradiction_factor
        )
    else:
        confidence = 0.0

    positive = [c for c in relevant if c.direction == ReadinessSignalDirection.POSITIVE]
    negative = [c for c in relevant if c.direction == ReadinessSignalDirection.NEGATIVE]

    return LaunchReadinessDimension(
        dimension=dimension,
        score=score,
        confidence=confidence,
        evidence_count=len(relevant),
        positive_evidence_ids=[e for c in positive for e in c.evidence_ids],
        negative_evidence_ids=[e for c in negative for e in c.evidence_ids],
        contradiction_count=contradiction_count,
        stale_evidence_count=stale_count,
        top_positive_factors=[c.rationale for c in positive[:3]],
        top_risks=[c.rationale for c in negative[:3]],
        recommended_next_action=(
            negative[0].rationale if negative else None
        ),
    )


# ── Overall score ────────────────────────────────────────────────────

_GLOBAL_CRITICAL_BLOCKER_FACTOR = 0.25
_GLOBAL_HIGH_BLOCKER_FACTOR = 0.10


def compute_overall_readiness(
    *,
    dimensions: list[LaunchReadinessDimension],
    blockers: list[LaunchBlocker],
    config: ReadinessScoringConfig,
) -> tuple[float, float]:
    """Weighted mean of dimension scores minus global blocker penalties;
    confidence is the weighted mean of dimension confidence multiplied by
    dimension coverage. Score and confidence are returned separately —
    never multiplied together."""
    if not dimensions:
        return 0.0, 0.0

    total_weight = 0.0
    weighted_score = 0.0
    weighted_confidence = 0.0
    for dim in dimensions:
        weight = config.dimension_weights.get(dim.dimension.value, 0.1)
        total_weight += weight
        weighted_score += weight * dim.score
        weighted_confidence += weight * dim.confidence

    score = weighted_score / total_weight if total_weight else 0.0
    confidence = weighted_confidence / total_weight if total_weight else 0.0

    unresolved = [b for b in blockers if not b.resolved]
    criticals = sum(
        1 for b in unresolved if b.severity == LaunchBlockerSeverity.CRITICAL
    )
    highs = sum(1 for b in unresolved if b.severity == LaunchBlockerSeverity.HIGH)
    score -= (
        criticals * config.critical_blocker_penalty * _GLOBAL_CRITICAL_BLOCKER_FACTOR
        + highs * config.high_blocker_penalty * _GLOBAL_HIGH_BLOCKER_FACTOR
    )

    evidenced = sum(1 for d in dimensions if d.evidence_count > 0)
    coverage = evidenced / len(dimensions)

    return _clamp(score, 0.0, 100.0), _clamp01(confidence * coverage)


# ── Feature vector ───────────────────────────────────────────────────

def build_launch_feature_vector(
    *,
    scorecard: LaunchReadinessScorecard,
    understanding: ProjectUnderstandingSnapshot,
    structural_artifacts: list[object],
    previous_snapshots: list[LaunchReadinessSnapshot],
    launch: LaunchDefinition,
) -> LaunchFeatureVector:
    unresolved = [b for b in scorecard.blockers if not b.resolved]
    critical_count = sum(
        1 for b in unresolved if b.severity == LaunchBlockerSeverity.CRITICAL
    )
    high_count = sum(
        1 for b in unresolved if b.severity == LaunchBlockerSeverity.HIGH
    )

    blast_radius = 0.0
    bottleneck = 0.0
    for record in structural_artifacts:
        blast_radius = max(
            blast_radius, float(getattr(record, "blast_radius", 0.0))
        )
        bottleneck = max(
            bottleneck, float(getattr(record, "decision_bottleneck_score", 0.0))
        )

    trend_slope = 0.0
    if previous_snapshots:
        previous = previous_snapshots[-1]
        trend_slope = (
            scorecard.overall_score - previous.scorecard.overall_score
        )

    return LaunchFeatureVector(
        project_id=scorecard.project_id,
        launch_id=scorecard.launch_id,
        as_of=scorecard.as_of,
        dimension_scores={
            d.dimension.value: d.score for d in scorecard.dimensions
        },
        dimension_confidences={
            d.dimension.value: d.confidence for d in scorecard.dimensions
        },
        critical_blocker_count=critical_count,
        high_blocker_count=high_count,
        unresolved_critical_unknown_count=len(scorecard.critical_unknown_ids),
        contradiction_count=len(scorecard.contradiction_ids),
        dependency_blast_radius=_clamp01(blast_radius),
        decision_bottleneck_score=_clamp01(bottleneck),
        evidence_freshness=understanding.coverage.evidence_freshness,
        readiness_trend_slope=trend_slope,
        days_until_target=(launch.target_date - scorecard.as_of).days,
    )


# ── Heuristic predictor ──────────────────────────────────────────────

class HeuristicLaunchReadinessPredictor:
    """Transparent HEURISTIC_V1 formula. Not a trained model, and not
    labeled as one. Confidence is capped when no historical launch
    outcomes exist to calibrate against."""

    SLIP_SCALE_DAYS = 25.0
    HEURISTIC_CONFIDENCE_CAP = 0.75

    def __init__(self, *, historical_outcomes: list[LaunchOutcomeRecord] | None = None):
        self.historical_outcomes = historical_outcomes or []

    def predict(
        self,
        *,
        scorecard: LaunchReadinessScorecard,
        feature_vector: LaunchFeatureVector,
    ) -> LaunchForecast:
        readiness_term = (scorecard.overall_score - 60.0) / 12.0
        trend_term = feature_vector.readiness_trend_slope / 8.0
        blocker_term = (
            feature_vector.critical_blocker_count * 0.9
            + feature_vector.high_blocker_count * 0.35
        )
        unknown_term = feature_vector.unresolved_critical_unknown_count * 0.4
        staleness_term = (1.0 - feature_vector.evidence_freshness) * 0.8

        logit = (
            readiness_term + trend_term - blocker_term - unknown_term - staleness_term
        )
        on_time_probability = 1.0 / (1.0 + math.exp(-logit))

        expected_slip = max(
            0.0, (0.75 - on_time_probability) * self.SLIP_SCALE_DAYS
        )
        lower_slip = expected_slip * 0.4
        upper_slip = expected_slip * 2.0

        if on_time_probability >= 0.80:
            band = LaunchRiskBand.LOW
        elif on_time_probability >= 0.60:
            band = LaunchRiskBand.MODERATE
        elif on_time_probability >= 0.35:
            band = LaunchRiskBand.HIGH
        else:
            band = LaunchRiskBand.CRITICAL

        confidence = scorecard.overall_confidence
        if not self.historical_outcomes:
            confidence = min(confidence, self.HEURISTIC_CONFIDENCE_CAP)

        risk_drivers: list[str] = []
        if feature_vector.critical_blocker_count:
            risk_drivers.append(
                f"{feature_vector.critical_blocker_count} unresolved critical "
                f"blocker(s)"
            )
        if feature_vector.high_blocker_count:
            risk_drivers.append(
                f"{feature_vector.high_blocker_count} high-severity blocker(s)"
            )
        if feature_vector.unresolved_critical_unknown_count:
            risk_drivers.append(
                f"{feature_vector.unresolved_critical_unknown_count} unresolved "
                f"critical unknown(s)"
            )
        if feature_vector.evidence_freshness < 0.5:
            risk_drivers.append("evidence is aging or stale")

        positive_drivers: list[str] = []
        if scorecard.overall_score >= 60:
            positive_drivers.append("overall readiness above baseline")
        if feature_vector.readiness_trend_slope > 0:
            positive_drivers.append("readiness trend is improving")

        return LaunchForecast(
            id=f"forecast-{scorecard.launch_id}-{scorecard.as_of.isoformat()}",
            project_id=scorecard.project_id,
            launch_id=scorecard.launch_id,
            as_of=scorecard.as_of,
            method=LaunchForecastMethod.HEURISTIC_V1,
            on_time_probability=_clamp01(on_time_probability),
            expected_slip_days=expected_slip,
            lower_slip_days=lower_slip,
            upper_slip_days=upper_slip,
            risk_band=band,
            confidence=_clamp01(confidence),
            principal_risk_drivers=risk_drivers,
            principal_positive_drivers=positive_drivers,
            feature_vector=feature_vector,
        )


# ── Goal estimates ───────────────────────────────────────────────────

_GOAL_KEYWORD_DIMENSIONS: list[tuple[tuple[str, ...], list[LaunchReadinessDimensionType]]] = [
    (
        ("contract", "integration", "api"),
        [
            LaunchReadinessDimensionType.INTEGRATION_READINESS,
            LaunchReadinessDimensionType.TECHNICAL_CONFIDENCE,
        ],
    ),
    (
        ("operational", "handoff", "runbook", "on-call", "oncall"),
        [
            LaunchReadinessDimensionType.OPERATIONAL_READINESS,
            LaunchReadinessDimensionType.OWNERSHIP_CLARITY,
        ],
    ),
    (
        ("scope",),
        [LaunchReadinessDimensionType.SCOPE_CLARITY],
    ),
    (
        ("validation", "test", "acceptance"),
        [LaunchReadinessDimensionType.VALIDATION_READINESS],
    ),
]


def _goal_dimensions(goal: object) -> list[LaunchReadinessDimensionType]:
    explicit = getattr(goal, "related_dimensions", None)
    if explicit:
        resolved = []
        for value in explicit:
            try:
                resolved.append(
                    LaunchReadinessDimensionType(getattr(value, "value", value))
                )
            except ValueError:
                continue
        if resolved:
            return resolved
    name = str(getattr(goal, "name", "") or "").lower()
    for keywords, dimensions in _GOAL_KEYWORD_DIMENSIONS:
        if any(k in name for k in keywords):
            return dimensions
    return list(LaunchReadinessDimensionType)


def build_goal_estimates(
    *,
    launch: LaunchDefinition,
    scorecard: LaunchReadinessScorecard,
    forecast: LaunchForecast,
    project_goals: list[object],
    as_of: datetime,
) -> list[GoalEstimate]:
    """Goal-specific estimates mapped through relevant dimensions and
    blockers — never the same probability blindly copied to every goal."""
    dims_by_type = {d.dimension: d for d in scorecard.dimensions}
    estimates: list[GoalEstimate] = []

    for goal in project_goals:
        goal_id = str(getattr(goal, "id", None) or getattr(goal, "goal_id", "goal"))
        goal_name = str(getattr(goal, "name", None) or goal_id)
        related = _goal_dimensions(goal)
        related_dims = [dims_by_type[d] for d in related if d in dims_by_type]

        mean_score = (
            sum(d.score for d in related_dims) / len(related_dims)
            if related_dims
            else 50.0
        )
        related_blockers = [
            b
            for b in scorecard.blockers
            if not b.resolved and b.affected_dimension in related
        ]
        probability = _clamp01(
            0.55 * forecast.on_time_probability
            + 0.45 * (mean_score / 100.0)
            - 0.08 * len(related_blockers)
        )
        confidence = _clamp01(
            (
                sum(d.confidence for d in related_dims) / len(related_dims)
                if related_dims
                else 0.0
            )
            * (forecast.confidence if forecast.confidence else 1.0)
        )

        slip = timedelta(days=forecast.expected_slip_days)
        estimated = launch.target_date + (slip if probability < 0.8 else timedelta())

        evidence_ids: list[str] = []
        for dim in related_dims:
            for eid in dim.positive_evidence_ids + dim.negative_evidence_ids:
                if eid not in evidence_ids:
                    evidence_ids.append(eid)

        estimates.append(
            GoalEstimate(
                id=f"goal-est-{launch.id}-{goal_id}",
                project_id=scorecard.project_id,
                launch_id=launch.id,
                goal_id=goal_id,
                goal_name=goal_name,
                completion_probability=probability,
                estimated_completion_date=estimated,
                lower_completion_date=launch.target_date,
                upper_completion_date=launch.target_date
                + timedelta(days=forecast.upper_slip_days),
                confidence=confidence,
                blocker_ids=[b.id for b in related_blockers],
                evidence_ids=evidence_ids,
                explanation=(
                    f"Goal '{goal_name}' maps to "
                    f"{', '.join(d.value for d in related)}; mean dimension "
                    f"score {mean_score:.0f}, {len(related_blockers)} related "
                    f"unresolved blocker(s)."
                ),
            )
        )
    return estimates


# ── Scorecard orchestrator ───────────────────────────────────────────

def build_launch_readiness_snapshot(
    *,
    understanding: ProjectUnderstandingSnapshot,
    launch: LaunchDefinition,
    ontology_records: list[object],
    structural_artifacts: list[object],
    project_goals: list[object],
    previous_snapshots: list[LaunchReadinessSnapshot],
    predictor: LaunchReadinessPredictor,
    config: ReadinessScoringConfig,
    as_of: datetime,
) -> LaunchReadinessSnapshot:
    """Extract evidence → identify blockers → score dimensions → overall
    → scorecard → feature vector → forecast → goal estimates."""
    contributions = extract_readiness_evidence(
        understanding=understanding,
        launch=launch,
        ontology_records=ontology_records,
        structural_artifacts=structural_artifacts,
        as_of=as_of,
    )
    blockers = identify_launch_blockers(
        understanding=understanding,
        launch=launch,
        contributions=contributions,
        structural_artifacts=structural_artifacts,
        as_of=as_of,
    )

    unresolved_contradictions = [
        c for c in understanding.contradictions if not c.resolved
    ]
    dimensions: list[LaunchReadinessDimension] = []
    for dimension_type in LaunchReadinessDimensionType:
        # Contradictions penalize the scope dimension where they live.
        applicable = (
            unresolved_contradictions
            if dimension_type == LaunchReadinessDimensionType.SCOPE_CLARITY
            else []
        )
        dimensions.append(
            score_readiness_dimension(
                dimension=dimension_type,
                contributions=contributions,
                contradictions=applicable,
                blockers=blockers,
                config=config,
            )
        )

    overall_score, overall_confidence = compute_overall_readiness(
        dimensions=dimensions, blockers=blockers, config=config
    )

    critical_unknown_ids = [
        u.id
        for u in understanding.unknowns
        if u.priority == ResearchPriority.CRITICAL
    ]

    evidence_ids: list[str] = []
    for contribution in contributions:
        for eid in contribution.evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    sorted_dims = sorted(dimensions, key=lambda d: d.score)
    top_reasons = [
        f"{d.dimension.value} scored {d.score:.0f} "
        f"({d.top_risks[0] if d.top_risks else 'no dominant risk'})"
        for d in sorted_dims[:3]
    ]
    leverage = [
        d.recommended_next_action
        for d in sorted_dims
        if d.recommended_next_action
    ][:3]

    scorecard = LaunchReadinessScorecard(
        id=f"scorecard-{launch.id}-{as_of.isoformat()}",
        project_id=understanding.project_id,
        launch_id=launch.id,
        as_of=as_of,
        overall_score=overall_score,
        overall_confidence=overall_confidence,
        dimensions=dimensions,
        blockers=blockers,
        critical_unknown_ids=critical_unknown_ids,
        contradiction_ids=[c.id for c in unresolved_contradictions],
        top_reasons_for_score=top_reasons,
        highest_leverage_actions=leverage,
        evidence_ids=evidence_ids,
        generated_at=as_of,
    )

    feature_vector = build_launch_feature_vector(
        scorecard=scorecard,
        understanding=understanding,
        structural_artifacts=structural_artifacts,
        previous_snapshots=previous_snapshots,
        launch=launch,
    )
    forecast = predictor.predict(
        scorecard=scorecard, feature_vector=feature_vector
    )
    goal_estimates = build_goal_estimates(
        launch=launch,
        scorecard=scorecard,
        forecast=forecast,
        project_goals=project_goals,
        as_of=as_of,
    )

    return LaunchReadinessSnapshot(
        id=f"lrs-{launch.id}-{as_of.isoformat()}",
        scorecard=scorecard,
        forecast=forecast,
        goal_estimates=goal_estimates,
    )


# ── Trend comparison ─────────────────────────────────────────────────

_TREND_DELTA_THRESHOLD = 2.0


def compare_launch_readiness_snapshots(
    *,
    previous: LaunchReadinessSnapshot,
    current: LaunchReadinessSnapshot,
) -> LaunchReadinessTrend:
    """The summary explains WHY readiness moved, not just by how much."""
    prev_scores = {
        d.dimension.value: d.score for d in previous.scorecard.dimensions
    }
    deltas: dict[str, float] = {}
    improving: list[str] = []
    deteriorating: list[str] = []
    for dim in current.scorecard.dimensions:
        delta = dim.score - prev_scores.get(dim.dimension.value, dim.score)
        deltas[dim.dimension.value] = delta
        if delta >= _TREND_DELTA_THRESHOLD:
            improving.append(dim.dimension.value)
        elif delta <= -_TREND_DELTA_THRESHOLD:
            deteriorating.append(dim.dimension.value)

    prev_blocker_titles = {
        b.title.strip().lower() for b in previous.scorecard.blockers
    }
    current_titles = {
        b.title.strip().lower() for b in current.scorecard.blockers
    }
    newly_opened = [
        b.id
        for b in current.scorecard.blockers
        if b.title.strip().lower() not in prev_blocker_titles
    ]
    newly_resolved = [
        b.id
        for b in previous.scorecard.blockers
        if b.title.strip().lower() not in current_titles
    ]

    score_delta = (
        current.scorecard.overall_score - previous.scorecard.overall_score
    )

    reasons: list[str] = []
    for name in deteriorating:
        dim = next(
            d for d in current.scorecard.dimensions if d.dimension.value == name
        )
        risk = dim.top_risks[0] if dim.top_risks else "score deteriorated"
        reasons.append(f"{name} fell {abs(deltas[name]):.0f} points ({risk})")
    if newly_opened:
        opened_titles = [
            b.title
            for b in current.scorecard.blockers
            if b.id in newly_opened
        ]
        reasons.append(f"new blocker(s): {'; '.join(opened_titles[:3])}")
    for name in improving:
        reasons.append(f"{name} improved {deltas[name]:.0f} points")
    if not reasons:
        reasons.append("no material dimension movement")

    direction = "fell" if score_delta < 0 else "rose"
    summary = (
        f"Readiness {direction} by {abs(score_delta):.0f} points because "
        + "; ".join(reasons)
        + "."
    )

    return LaunchReadinessTrend(
        project_id=current.scorecard.project_id,
        launch_id=current.scorecard.launch_id,
        previous_snapshot_id=previous.id,
        current_snapshot_id=current.id,
        score_delta=score_delta,
        confidence_delta=(
            current.scorecard.overall_confidence
            - previous.scorecard.overall_confidence
        ),
        dimension_deltas=deltas,
        improving_dimensions=improving,
        deteriorating_dimensions=deteriorating,
        newly_opened_blocker_ids=newly_opened,
        newly_resolved_blocker_ids=newly_resolved,
        summary=summary,
    )
