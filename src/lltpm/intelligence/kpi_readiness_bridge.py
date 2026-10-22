"""KPI → launch readiness bridge.

KPIs influence launch readiness only as ReadinessEvidenceContributions
— preserving launch readiness's evidence-based scoring. This module never
mutates a scorecard or a score.
"""

from __future__ import annotations

from datetime import datetime

from lltpm.intelligence.kpi import (
    KPIDefinition,
    KPIHealthStatus,
    KPIIndicatorRole,
    KPIMilestoneLink,
    KPITrend,
)
from lltpm.intelligence.launch_readiness import (
    LaunchReadinessDimensionType,
    ReadinessEvidenceContribution,
    ReadinessSignalDirection,
)

_KPI_TYPE_DIMENSION = {
    "product": LaunchReadinessDimensionType.BUSINESS_ALIGNMENT,
    "business": LaunchReadinessDimensionType.BUSINESS_ALIGNMENT,
    "customer": LaunchReadinessDimensionType.BUSINESS_ALIGNMENT,
    "technical": LaunchReadinessDimensionType.TECHNICAL_CONFIDENCE,
    "reliability": LaunchReadinessDimensionType.OPERATIONAL_READINESS,
    "operational": LaunchReadinessDimensionType.OPERATIONAL_READINESS,
    "quality": LaunchReadinessDimensionType.VALIDATION_READINESS,
    "delivery": LaunchReadinessDimensionType.INTEGRATION_READINESS,
}


def _freshness_factor(trend: KPITrend) -> float:
    if trend.freshness_seconds is None:
        return 0.1
    days = trend.freshness_seconds / 86400
    return max(0.05, min(1.0, 1.0 - days / 30.0))


def kpi_to_readiness_contributions(
    *,
    kpi_definitions: list[KPIDefinition],
    trends: list[KPITrend],
    milestone_links: list[KPIMilestoneLink],
    as_of: datetime,
) -> list[ReadinessEvidenceContribution]:
    """Translate KPI state into readiness evidence contributions:

    - launch-blocking KPI below target → strong negative validation
      contribution (plus its type-mapped dimension)
    - stale KPI → low-freshness negative signal (confidence problem)
    - deteriorating leading KPI → early technical/dependency risk
    - healthy launch-linked KPI → positive validation evidence
    """
    contributions: list[ReadinessEvidenceContribution] = []
    counter = 0
    definitions_by_id = {d.id: d for d in kpi_definitions}
    blocking_kpi_ids = {
        link.kpi_id for link in milestone_links if link.launch_blocking
    }

    def add(
        definition: KPIDefinition,
        trend: KPITrend,
        dimension: LaunchReadinessDimensionType,
        direction: ReadinessSignalDirection,
        strength: float,
        rationale: str,
        fresh: float,
    ) -> None:
        nonlocal counter
        counter += 1
        contributions.append(
            ReadinessEvidenceContribution(
                id=f"kpi-rec-{definition.id}-{counter}",
                project_id=definition.project_id,
                launch_id="",
                dimension=dimension,
                direction=direction,
                strength=max(0.0, min(1.0, strength)),
                confidence=max(0.0, min(1.0, definition.confidence or 0.5)),
                source_reliability=0.9,  # direct metric sources
                freshness_factor=fresh,
                evidence_ids=trend.evidence_ids or definition.evidence_ids,
                observed_at=as_of,
                rationale=rationale,
                blocker_candidate=(
                    definition.id in blocking_kpi_ids
                    and trend.health
                    in (KPIHealthStatus.BREACHED, KPIHealthStatus.CRITICAL)
                ),
            )
        )

    for trend in sorted(trends, key=lambda t: t.kpi_id):
        definition = definitions_by_id.get(trend.kpi_id)
        if definition is None:
            continue
        dimension = _KPI_TYPE_DIMENSION.get(
            definition.kpi_type.value,
            LaunchReadinessDimensionType.VALIDATION_READINESS,
        )
        fresh = _freshness_factor(trend)
        blocking = definition.id in blocking_kpi_ids

        if trend.health in (KPIHealthStatus.BREACHED, KPIHealthStatus.CRITICAL):
            strength = 0.9 if trend.health == KPIHealthStatus.CRITICAL else 0.7
            if blocking:
                add(
                    definition, trend,
                    LaunchReadinessDimensionType.VALIDATION_READINESS,
                    ReadinessSignalDirection.NEGATIVE,
                    min(1.0, strength + 0.1),
                    f"Launch-blocking KPI '{definition.canonical_name}' is "
                    f"{trend.health.value} at {trend.latest_value}"
                    f"{definition.unit} (distance to target: "
                    f"{trend.target_distance}).",
                    fresh,
                )
            add(
                definition, trend, dimension,
                ReadinessSignalDirection.NEGATIVE, strength,
                f"KPI '{definition.canonical_name}' is {trend.health.value}: "
                f"{definition.why_it_matters}",
                fresh,
            )
        elif trend.health == KPIHealthStatus.STALE:
            add(
                definition, trend, dimension,
                ReadinessSignalDirection.NEGATIVE, 0.4,
                f"KPI '{definition.canonical_name}' evidence is stale — "
                f"its current state cannot be trusted for launch "
                f"assessment.",
                min(fresh, 0.2),
            )
        elif (
            definition.indicator_role == KPIIndicatorRole.LEADING
            and trend.direction == "deteriorating"
        ):
            add(
                definition, trend,
                LaunchReadinessDimensionType.TECHNICAL_CONFIDENCE,
                ReadinessSignalDirection.NEGATIVE, 0.5,
                f"Leading KPI '{definition.canonical_name}' is "
                f"deteriorating — early risk signal ahead of lagging "
                f"impact.",
                fresh,
            )
        elif trend.health == KPIHealthStatus.HEALTHY and blocking:
            add(
                definition, trend,
                LaunchReadinessDimensionType.VALIDATION_READINESS,
                ReadinessSignalDirection.POSITIVE, 0.6,
                f"Launch-linked KPI '{definition.canonical_name}' is "
                f"healthy at {trend.latest_value}{definition.unit}.",
                fresh,
            )

    return contributions
