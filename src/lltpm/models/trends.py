"""Historical trend analysis models — track patterns over time."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TrendDirection(str, Enum):
    """Direction of a trend over time."""

    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    VOLATILE = "volatile"


class HealthSnapshot(BaseModel):
    """Point-in-time snapshot of program health for trend tracking."""

    program_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    health: str = "GREEN"  # GREEN, YELLOW, RED
    milestone_completion: float = 0.0  # 0.0 to 1.0
    open_risks: int = 0
    open_blockers: int = 0
    active_delegations: int = 0
    avg_response_hours: float = 0.0
    scope_changes: int = 0
    velocity: float = 0.0  # milestones completed per week


class VelocityPoint(BaseModel):
    """A single velocity measurement."""

    period_start: date
    period_end: date
    milestones_completed: int = 0
    milestones_planned: int = 0
    items_closed: int = 0
    items_opened: int = 0

    @property
    def completion_rate(self) -> float:
        if self.milestones_planned == 0:
            return 1.0
        return self.milestones_completed / self.milestones_planned

    @property
    def burn_rate(self) -> float:
        """Net items burned (closed - opened). Positive = making progress."""
        return self.items_closed - self.items_opened


class PersonTrend(BaseModel):
    """Response time and engagement trend for a person."""

    person: str
    program_id: str = ""
    data_points: list[dict] = Field(default_factory=list)  # [{date, avg_response_h, response_rate}]
    direction: TrendDirection = TrendDirection.STABLE
    avg_response_hours_30d: float = 0.0
    avg_response_hours_7d: float = 0.0
    response_rate_30d: float = 1.0
    response_rate_7d: float = 1.0

    @property
    def is_degrading(self) -> bool:
        return (
            self.avg_response_hours_7d > self.avg_response_hours_30d * 1.5
            or self.response_rate_7d < self.response_rate_30d * 0.8
        )


class RiskTrend(BaseModel):
    """Track whether risks are accumulating or being resolved."""

    program_id: str
    data_points: list[dict] = Field(default_factory=list)  # [{date, open_risks, resolved}]
    direction: TrendDirection = TrendDirection.STABLE
    current_open: int = 0
    peak_open: int = 0
    total_resolved: int = 0
    avg_time_to_resolve_days: float = 0.0


class TrendReport(BaseModel):
    """Comprehensive trend analysis for a program."""

    program_id: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    health_trend: TrendDirection = TrendDirection.STABLE
    velocity_trend: TrendDirection = TrendDirection.STABLE
    risk_trend: TrendDirection = TrendDirection.STABLE
    engagement_trend: TrendDirection = TrendDirection.STABLE
    health_snapshots: list[HealthSnapshot] = Field(default_factory=list)
    velocity_points: list[VelocityPoint] = Field(default_factory=list)
    person_trends: list[PersonTrend] = Field(default_factory=list)
    risk_trends: Optional[RiskTrend] = None
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"# Trend Report — {self.program_id}",
            f"Generated: {self.generated_at.strftime('%Y-%m-%d')}",
            "",
            "## Trajectory",
            f"- Health: {self.health_trend.value}",
            f"- Velocity: {self.velocity_trend.value}",
            f"- Risk exposure: {self.risk_trend.value}",
            f"- Engagement: {self.engagement_trend.value}",
        ]

        if self.warnings:
            lines.append(f"\n## Warnings ({len(self.warnings)})")
            for w in self.warnings:
                lines.append(f"- {w}")

        if self.recommendations:
            lines.append(f"\n## Recommendations ({len(self.recommendations)})")
            for r in self.recommendations:
                lines.append(f"- {r}")

        degrading = [p for p in self.person_trends if p.is_degrading]
        if degrading:
            lines.append(f"\n## Engagement Alerts ({len(degrading)})")
            for p in degrading:
                lines.append(
                    f"- **{p.person}**: response time {p.avg_response_hours_7d:.0f}h (7d) "
                    f"vs {p.avg_response_hours_30d:.0f}h (30d) — {p.direction.value}"
                )

        return "\n".join(lines)
