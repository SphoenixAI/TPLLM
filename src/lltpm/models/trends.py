"""Trend models — where a program is heading, not who is slipping.

**What was removed in Commit 41, and why.** This module held
``PersonTrend``: per-person response rate and response hours over 7 and
30 days with an ``is_degrading`` flag, which the trend report rendered
as "Engagement Alerts" naming individuals, and which the program's
``engagement_trend`` was computed by counting.

A program whose replies are slowing down is a real and useful signal.
Attributing it to the people who replied slowest is a different claim,
and usually the wrong one — the same evidence more often means requests
are queuing at a shared authority, or that one person sits on four
critical paths. ``response_latency_trend`` keeps the signal at the level
where it can be acted on, as time-to-reply across the program with no
per-person breakdown to sort.
"""

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
    # Program-level time-to-reply, not a per-person breakdown. See
    # the module note: the old engagement_trend aggregated
    # individual responsiveness grades.
    response_latency_trend: TrendDirection = TrendDirection.STABLE
    median_response_hours: float = 0.0
    health_snapshots: list[HealthSnapshot] = Field(default_factory=list)
    velocity_points: list[VelocityPoint] = Field(default_factory=list)
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
            f"- Time to reply: {self.response_latency_trend.value}"
            + (
                f" (median {self.median_response_hours:.0f}h)"
                if self.median_response_hours else ""
            ),
        ]

        if self.warnings:
            lines.append(f"\n## Warnings ({len(self.warnings)})")
            for w in self.warnings:
                lines.append(f"- {w}")

        if self.recommendations:
            lines.append(f"\n## Recommendations ({len(self.recommendations)})")
            for r in self.recommendations:
                lines.append(f"- {r}")

        return "\n".join(lines)
