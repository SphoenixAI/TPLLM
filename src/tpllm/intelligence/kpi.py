"""KPI intelligence core models.

A KPI is not just a number on a dashboard. The model understands what a
KPI means, why it matters, its target, unit, observation window,
measurement lag, leading/lagging behavior, related milestones and
workstreams, response state, freshness, and conflicts.

Targets are versioned — historical targets are never overwritten. A
lagging indicator is distinct from a late measurement. Relationship
labels never claim causal certainty from correlation alone.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from tpllm.core.actors import ActorReference


# ── Enums ──────────────────────────────────────────────────────────────

class KPIType(str, Enum):
    PRODUCT = "product"
    BUSINESS = "business"
    TECHNICAL = "technical"
    OPERATIONAL = "operational"
    RELIABILITY = "reliability"
    QUALITY = "quality"
    DELIVERY = "delivery"
    CUSTOMER = "customer"


class KPIIndicatorRole(str, Enum):
    LEADING = "leading"
    LAGGING = "lagging"
    COINCIDENT = "coincident"
    UNKNOWN = "unknown"


class KPITargetOperator(str, Enum):
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    EQUAL = "equal"
    RANGE = "range"


class KPIHealthStatus(str, Enum):
    HEALTHY = "healthy"
    WATCH = "watch"
    BREACHED = "breached"
    CRITICAL = "critical"
    UNKNOWN = "unknown"
    STALE = "stale"


class KPIClaimType(str, Enum):
    DEFINITION = "definition"
    CURRENT_VALUE = "current_value"
    TARGET = "target"
    IMPORTANCE = "importance"
    MILESTONE_LINK = "milestone_link"
    CAUSE = "cause"
    OWNER = "owner"
    MEASUREMENT_LAG = "measurement_lag"
    OBSERVATION_WINDOW = "observation_window"
    EXPECTED_REFRESH = "expected_refresh"


class KPIRelationLabel(str, Enum):
    PRECEDES = "precedes"
    CONTRIBUTES_TO = "contributes_to"
    CORRELATES_WITH = "correlates_with"
    EXPECTED_TO_INFLUENCE = "expected_to_influence"
    CAUSALLY_VALIDATED = "causally_validated"


class KPIResponseState(str, Enum):
    UNACKNOWLEDGED = "unacknowledged"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    REMEDIATING = "remediating"
    WAITING_ON_DEPENDENCY = "waiting_on_dependency"
    VALIDATING_RECOVERY = "validating_recovery"
    RESOLVED = "resolved"


# ── Definition and targets ───────────────────────────────────────────

class KPIDefinition(BaseModel):
    id: str
    project_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    description: str
    kpi_type: KPIType
    indicator_role: KPIIndicatorRole
    unit: str
    why_it_matters: str
    decision_use: str | None = None
    source_systems: list[str] = Field(default_factory=list)
    expected_refresh_interval_seconds: int | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float

    @model_validator(mode="after")
    def _check(self) -> "KPIDefinition":
        if not self.why_it_matters.strip():
            raise ValueError(
                "A KPI definition requires why_it_matters — a number "
                "without meaning is not a KPI."
            )
        return self


class KPITarget(BaseModel):
    """A versioned target. Historical targets are closed with
    effective_until — never overwritten or deleted."""
    id: str
    kpi_id: str
    operator: KPITargetOperator
    target_value: float | None = None
    range_min: float | None = None
    range_max: float | None = None
    effective_from: datetime
    effective_until: datetime | None = None
    milestone_id: str | None = None
    rationale: str | None = None
    evidence_ids: list[str]
    confidence: float


def set_kpi_target(
    *,
    targets: list[KPITarget],
    new_target: KPITarget,
) -> list[KPITarget]:
    """Version a new target: the currently open target for the KPI is
    closed at the new target's effective_from; history is preserved."""
    updated: list[KPITarget] = []
    for target in targets:
        if (
            target.kpi_id == new_target.kpi_id
            and target.effective_until is None
            and target.effective_from < new_target.effective_from
        ):
            updated.append(
                target.model_copy(
                    update={"effective_until": new_target.effective_from}
                )
            )
        else:
            updated.append(target)
    updated.append(new_target)
    return updated


def active_target(
    *,
    targets: list[KPITarget],
    kpi_id: str,
    as_of: datetime,
) -> KPITarget | None:
    candidates = [
        t
        for t in targets
        if t.kpi_id == kpi_id
        and t.effective_from <= as_of
        and (t.effective_until is None or t.effective_until > as_of)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda t: (t.effective_from, t.id))[-1]


# ── Observations and measurement ─────────────────────────────────────

class KPIObservation(BaseModel):
    id: str
    kpi_id: str
    value: float
    unit: str
    observed_at: datetime
    reported_at: datetime
    effective_window_start: datetime | None = None
    effective_window_end: datetime | None = None
    source_system: str
    source_id: str
    evidence_ids: list[str]
    confidence: float


class KPIMeasurementProfile(BaseModel):
    """Distinguishes a lagging indicator (the metric moves after the
    cause) from a late measurement (the number arrives slowly)."""
    kpi_id: str
    indicator_role: KPIIndicatorRole
    expected_refresh_interval_seconds: int | None
    expected_measurement_lag_seconds: int | None
    typical_stabilization_window_seconds: int | None
    explanation: str
    evidence_ids: list[str]


class KPIRelationship(BaseModel):
    """A mechanism-bearing relationship. Correlation alone never earns
    the CAUSALLY_VALIDATED label."""
    id: str
    source_kpi_id: str
    target_kpi_id: str
    relation: str
    expected_direction: str | None = None
    expected_lag_seconds: int | None = None
    mechanism: str
    evidence_ids: list[str]
    confidence: float

    @model_validator(mode="after")
    def _check(self) -> "KPIRelationship":
        if not self.mechanism.strip():
            raise ValueError(
                "A KPI relationship requires an explicit mechanism."
            )
        if self.relation == KPIRelationLabel.CAUSALLY_VALIDATED.value:
            if len(self.evidence_ids) < 2 or self.confidence < 0.8:
                raise ValueError(
                    "CAUSALLY_VALIDATED requires strong multi-source "
                    "evidence — correlation alone is not causation."
                )
        return self


class KPIMilestoneLink(BaseModel):
    id: str
    kpi_id: str
    milestone_id: str
    importance: str
    launch_blocking: bool
    required_target_id: str | None
    explanation: str
    evidence_ids: list[str]


# ── Claims extracted from communication ──────────────────────────────

class KPIClaim(BaseModel):
    id: str
    project_id: str
    candidate_kpi_name: str
    claim_type: KPIClaimType
    statement: str
    parsed_numeric_value: float | None = None
    parsed_unit: str | None = None
    target_candidate: float | None = None
    actor: ActorReference | None = None
    observed_at: datetime
    evidence_ids: list[str]
    confidence: float


# ── Trend ────────────────────────────────────────────────────────────

class KPITrend(BaseModel):
    kpi_id: str
    as_of: datetime
    latest_value: float | None
    direction: str
    slope: float | None
    target_distance: float | None
    observation_count: int
    freshness_seconds: int | None
    health: KPIHealthStatus
    evidence_ids: list[str]


# ── Health assessment ────────────────────────────────────────────────

_DEFAULT_STALENESS_MULTIPLIER = 2.0
_DEFAULT_STALENESS_SECONDS = 14 * 24 * 3600
_WATCH_MARGIN = 0.05
_CRITICAL_RELATIVE_BREACH = 0.20


def _target_satisfied(target: KPITarget, value: float) -> bool:
    op = target.operator
    tv = target.target_value
    if op == KPITargetOperator.GREATER_THAN:
        return tv is not None and value > tv
    if op == KPITargetOperator.GREATER_THAN_OR_EQUAL:
        return tv is not None and value >= tv
    if op == KPITargetOperator.LESS_THAN:
        return tv is not None and value < tv
    if op == KPITargetOperator.LESS_THAN_OR_EQUAL:
        return tv is not None and value <= tv
    if op == KPITargetOperator.EQUAL:
        return tv is not None and math.isclose(value, tv, rel_tol=1e-6)
    if op == KPITargetOperator.RANGE:
        return (
            target.range_min is not None
            and target.range_max is not None
            and target.range_min <= value <= target.range_max
        )
    return False


def _relative_breach(target: KPITarget, value: float) -> float:
    tv = target.target_value
    if tv is None or tv == 0:
        return 0.0
    return abs(value - tv) / abs(tv)


def _improving_direction(target: KPITarget | None) -> float:
    """+1 when higher is better, -1 when lower is better, 0 unknown."""
    if target is None:
        return 0.0
    if target.operator in (
        KPITargetOperator.GREATER_THAN,
        KPITargetOperator.GREATER_THAN_OR_EQUAL,
    ):
        return 1.0
    if target.operator in (
        KPITargetOperator.LESS_THAN,
        KPITargetOperator.LESS_THAN_OR_EQUAL,
    ):
        return -1.0
    return 0.0


def assess_kpi_health(
    *,
    definition: KPIDefinition,
    target: KPITarget | None,
    observations: list[KPIObservation],
    profile: KPIMeasurementProfile | None,
    as_of: datetime,
) -> KPITrend:
    """Deterministic trend + health: missing observation → UNKNOWN;
    stale observation → STALE (never quietly HEALTHY); fresh breach →
    BREACHED or CRITICAL by severity; else HEALTHY/WATCH."""
    relevant = sorted(
        (o for o in observations
         if o.kpi_id == definition.id and o.observed_at <= as_of),
        key=lambda o: (o.observed_at, o.id),
    )

    if not relevant:
        return KPITrend(
            kpi_id=definition.id,
            as_of=as_of,
            latest_value=None,
            direction="unknown",
            slope=None,
            target_distance=None,
            observation_count=0,
            freshness_seconds=None,
            health=KPIHealthStatus.UNKNOWN,
            evidence_ids=[],
        )

    latest = relevant[-1]
    freshness_seconds = int((as_of - latest.observed_at).total_seconds())

    refresh = (
        (profile.expected_refresh_interval_seconds if profile else None)
        or definition.expected_refresh_interval_seconds
    )
    staleness_limit = (
        refresh * _DEFAULT_STALENESS_MULTIPLIER
        if refresh
        else _DEFAULT_STALENESS_SECONDS
    )

    slope = None
    direction = "flat"
    if len(relevant) >= 2:
        previous = relevant[-2]
        elapsed_days = max(
            (latest.observed_at - previous.observed_at).total_seconds() / 86400,
            1e-9,
        )
        slope = (latest.value - previous.value) / elapsed_days
        better = _improving_direction(target)
        if abs(latest.value - previous.value) < 1e-12:
            direction = "flat"
        elif better == 0.0:
            direction = "rising" if slope > 0 else "falling"
        else:
            direction = (
                "improving" if slope * better > 0 else "deteriorating"
            )

    target_distance = None
    if target is not None and target.target_value is not None:
        target_distance = latest.value - target.target_value

    evidence_ids: list[str] = []
    for observation in relevant[-3:]:
        for eid in observation.evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    if freshness_seconds > staleness_limit:
        health = KPIHealthStatus.STALE
    elif target is None:
        health = KPIHealthStatus.UNKNOWN
    elif _target_satisfied(target, latest.value):
        if (
            target.target_value is not None
            and _relative_breach(target, latest.value) <= _WATCH_MARGIN
        ):
            health = KPIHealthStatus.WATCH  # within margin of the line
        else:
            health = KPIHealthStatus.HEALTHY
    else:
        health = (
            KPIHealthStatus.CRITICAL
            if _relative_breach(target, latest.value)
            >= _CRITICAL_RELATIVE_BREACH
            else KPIHealthStatus.BREACHED
        )

    return KPITrend(
        kpi_id=definition.id,
        as_of=as_of,
        latest_value=latest.value,
        direction=direction,
        slope=slope,
        target_distance=target_distance,
        observation_count=len(relevant),
        freshness_seconds=freshness_seconds,
        health=health,
        evidence_ids=evidence_ids,
    )


def resolve_kpi_by_name(
    *,
    name: str,
    known_kpis: list[KPIDefinition],
) -> KPIDefinition | None:
    """Alias-aware canonical resolution, case-insensitive."""
    lowered = name.strip().lower()
    for definition in known_kpis:
        if definition.canonical_name.lower() == lowered:
            return definition
        if any(alias.lower() == lowered for alias in definition.aliases):
            return definition
    return None
