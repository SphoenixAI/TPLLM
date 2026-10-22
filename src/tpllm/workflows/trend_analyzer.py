"""Historical Trend Analyzer — track patterns over time.

Takes periodic snapshots and analyzes:
- Program health trajectory (improving, stable, declining)
- Velocity trends (milestones completed per period)
- Risk accumulation patterns
- Per-person engagement trends
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.trends import (
    HealthSnapshot,
    RiskTrend,
    TrendDirection,
    TrendReport,
    VelocityPoint,
)


class TrendAnalyzer:
    """Analyze historical trends for programs and people."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    # ── Snapshot Recording ────────────────────────────────────

    def record_health_snapshot(self, program_id: str) -> HealthSnapshot:
        """Record a point-in-time health snapshot for a program."""
        from tpllm.models.milestone import MilestonePlan
        from tpllm.models.risk import RAIDLog
        from tpllm.models.delegation import DelegationPlan

        plan = self._store.load(program_id, "milestone_plan", MilestonePlan)
        raid = self._store.load(program_id, "raid_log", RAIDLog)
        delegation = self._store.load(program_id, "delegation_plan", DelegationPlan)

        total_ms = len(plan.milestones) if plan else 0
        completed = (
            len([m for m in plan.milestones if m.status.value == "COMPLETED"])
            if plan else 0
        )
        at_risk = (
            len([m for m in plan.milestones if m.status.value == "AT_RISK"])
            if plan else 0
        )
        open_risks = (
            len([r for r in raid.risks if r.status.value == "OPEN"])
            if raid else 0
        )

        health = "GREEN"
        if at_risk > 0 or open_risks > 2:
            health = "YELLOW"
        if at_risk > 2 or open_risks > 5:
            health = "RED"

        snapshot = HealthSnapshot(
            program_id=program_id,
            health=health,
            milestone_completion=completed / total_ms if total_ms > 0 else 0.0,
            open_risks=open_risks,
            active_delegations=len(delegation.open_items) if delegation else 0,
            velocity=completed,
        )

        # Append to history
        history = self._load_snapshots(program_id)
        history.append(snapshot.model_dump())
        self._save_snapshots(program_id, history)

        return snapshot

    def record_velocity(
        self,
        program_id: str,
        period_start: date,
        period_end: date,
        milestones_completed: int = 0,
        milestones_planned: int = 0,
        items_closed: int = 0,
        items_opened: int = 0,
    ) -> VelocityPoint:
        """Record a velocity measurement for a period."""
        point = VelocityPoint(
            period_start=period_start,
            period_end=period_end,
            milestones_completed=milestones_completed,
            milestones_planned=milestones_planned,
            items_closed=items_closed,
            items_opened=items_opened,
        )

        history = self._load_velocity(program_id)
        history.append(point.model_dump())
        self._save_velocity(program_id, history)

        return point

    # ── Trend Analysis ────────────────────────────────────────

    def analyze_trends(self, program_id: str) -> TrendReport:
        """Generate a comprehensive trend report for a program."""
        snapshots = self._load_snapshots(program_id)
        velocity_data = self._load_velocity(program_id)

        health_snaps = [HealthSnapshot.model_validate(s) for s in snapshots]
        velocity_points = [VelocityPoint.model_validate(v) for v in velocity_data]

        health_trend = self._compute_health_trend(health_snaps)
        velocity_trend = self._compute_velocity_trend(velocity_points)
        risk_trend_data = self._compute_risk_trend(program_id, health_snaps)
        latency_trend, median_hours = self._compute_latency_trend(
            program_id
        )

        # Generate warnings and recommendations
        warnings = []
        recommendations = []

        if health_trend == TrendDirection.DECLINING:
            warnings.append("Program health is declining — review root causes")
        if velocity_trend == TrendDirection.DECLINING:
            warnings.append("Velocity is declining — may need scope or resource adjustment")
        if risk_trend_data and risk_trend_data.current_open > risk_trend_data.peak_open * 0.8:
            warnings.append("Risk count approaching peak levels")
        if median_hours > 48:
            warnings.append(
                f"Requests are waiting a median of {median_hours:.0f}h for "
                f"a reply — look for a shared approval point or a person "
                f"on several critical paths"
            )

        if warnings:
            recommendations.append("Schedule a retrospective to address declining trends")
        if velocity_trend == TrendDirection.DECLINING:
            recommendations.append("Review scope — consider cutting non-critical items")
        if latency_trend == TrendDirection.DECLINING:
            recommendations.append(
                "Time to reply is lengthening — check where requests queue "
                "before concluding anything about who is answering them"
            )

        report = TrendReport(
            program_id=program_id,
            health_trend=health_trend,
            velocity_trend=velocity_trend,
            risk_trend=risk_trend_data.direction if risk_trend_data else TrendDirection.STABLE,
            response_latency_trend=latency_trend,
            median_response_hours=median_hours,
            health_snapshots=health_snaps[-20:],  # keep last 20
            velocity_points=velocity_points[-12:],  # keep last 12
            risk_trends=risk_trend_data,
            warnings=warnings,
            recommendations=recommendations,
        )

        # Persist
        self._store.save_raw(
            program_id, "trend_report",
            report.model_dump_json(indent=2),
        )

        return report

    def get_trend_summary(self, program_id: str) -> str:
        """Get a human-readable trend summary."""
        report = self.analyze_trends(program_id)
        return report.summary()

    # ── Trend Computation ─────────────────────────────────────

    def _compute_health_trend(
        self, snapshots: list[HealthSnapshot]
    ) -> TrendDirection:
        """Compute health direction from snapshots."""
        if len(snapshots) < 3:
            return TrendDirection.STABLE

        health_scores = {"GREEN": 3, "YELLOW": 2, "RED": 1}
        recent = snapshots[-3:]
        older = snapshots[-6:-3] if len(snapshots) >= 6 else snapshots[:3]

        recent_avg = sum(health_scores.get(s.health, 2) for s in recent) / len(recent)
        older_avg = sum(health_scores.get(s.health, 2) for s in older) / len(older)

        diff = recent_avg - older_avg
        if diff > 0.3:
            return TrendDirection.IMPROVING
        if diff < -0.3:
            return TrendDirection.DECLINING
        return TrendDirection.STABLE

    def _compute_velocity_trend(
        self, points: list[VelocityPoint]
    ) -> TrendDirection:
        """Compute velocity direction from data points."""
        if len(points) < 3:
            return TrendDirection.STABLE

        recent = points[-3:]
        older = points[-6:-3] if len(points) >= 6 else points[:3]

        recent_rate = sum(p.completion_rate for p in recent) / len(recent)
        older_rate = sum(p.completion_rate for p in older) / len(older)

        diff = recent_rate - older_rate
        if diff > 0.1:
            return TrendDirection.IMPROVING
        if diff < -0.1:
            return TrendDirection.DECLINING
        return TrendDirection.STABLE

    def _compute_risk_trend(
        self,
        program_id: str,
        snapshots: list[HealthSnapshot],
    ) -> RiskTrend | None:
        """Compute risk accumulation trend."""
        if not snapshots:
            return None

        data_points = [
            {"date": s.timestamp.isoformat(), "open_risks": s.open_risks}
            for s in snapshots
        ]

        current = snapshots[-1].open_risks if snapshots else 0
        peak = max(s.open_risks for s in snapshots) if snapshots else 0

        direction = TrendDirection.STABLE
        if len(snapshots) >= 3:
            recent_avg = sum(s.open_risks for s in snapshots[-3:]) / 3
            older_avg = (
                sum(s.open_risks for s in snapshots[-6:-3]) / 3
                if len(snapshots) >= 6
                else sum(s.open_risks for s in snapshots[:3]) / max(len(snapshots[:3]), 1)
            )
            if recent_avg > older_avg * 1.3:
                direction = TrendDirection.DECLINING  # risks growing
            elif recent_avg < older_avg * 0.7:
                direction = TrendDirection.IMPROVING  # risks shrinking

        return RiskTrend(
            program_id=program_id,
            data_points=data_points,
            direction=direction,
            current_open=current,
            peak_open=peak,
        )

    def _compute_latency_trend(
        self, program_id: str,
    ) -> tuple[TrendDirection, float]:
        """Program-level time-to-reply, with nobody named.

        Returns a direction and the median hours across every routing
        profile that has observations. Deliberately an aggregate: the
        per-person version of this produced "Engagement Alerts" naming
        individuals, which is the finding this system is built not to
        make.
        """
        import json as json_mod

        raw = self._store.load_raw(program_id, "engagement_profiles")
        if not raw:
            return TrendDirection.STABLE, 0.0

        try:
            profiles = json_mod.loads(raw)
        except (json_mod.JSONDecodeError, TypeError):
            return TrendDirection.STABLE, 0.0

        samples = []
        for data in profiles.values():
            if not isinstance(data, dict):
                continue
            if not data.get("observed_responses"):
                continue
            hours = data.get("median_response_hours")
            if isinstance(hours, (int, float)):
                samples.append(float(hours))

        if not samples:
            return TrendDirection.STABLE, 0.0

        samples.sort()
        middle = len(samples) // 2
        median = (
            samples[middle] if len(samples) % 2
            else (samples[middle - 1] + samples[middle]) / 2
        )
        # One snapshot cannot establish a direction; saying STABLE is
        # honest where inventing DECLINING from a single reading is not.
        direction = (
            TrendDirection.DECLINING if median > 48 else TrendDirection.STABLE
        )
        return direction, round(median, 2)

    # ── Persistence ───────────────────────────────────────────

    def _load_snapshots(self, program_id: str) -> list[dict]:
        raw = self._store.load_raw(program_id, "health_snapshots")
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def _save_snapshots(self, program_id: str, data: list[dict]) -> None:
        self._store.save_raw(program_id, "health_snapshots", json.dumps(data, default=str))

    def _load_velocity(self, program_id: str) -> list[dict]:
        raw = self._store.load_raw(program_id, "velocity_history")
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def _save_velocity(self, program_id: str, data: list[dict]) -> None:
        self._store.save_raw(program_id, "velocity_history", json.dumps(data, default=str))
