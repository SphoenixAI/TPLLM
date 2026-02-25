"""Risk engine — automatic risk detection and escalation recommendations.

Scans the program state and surfaces risks that humans might miss because
they don't have the full dependency graph in their heads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.milestone import MilestonePlan, MilestoneStatus
from lltpm.models.risk import RAIDLog, Severity


@dataclass
class RiskSignal:
    """An automatically detected risk signal."""

    category: str
    title: str
    detail: str
    recommended_action: str
    severity: str


class RiskEngine:
    """Scans program artifacts for risk signals."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def scan(self, program_id: str) -> list[RiskSignal]:
        signals: list[RiskSignal] = []
        signals.extend(self._check_milestone_compression(program_id))
        signals.extend(self._check_risk_without_mitigation(program_id))
        signals.extend(self._check_dependency_clustering(program_id))
        signals.extend(self._check_approaching_deadlines(program_id))
        return signals

    def _check_milestone_compression(self, program_id: str) -> list[RiskSignal]:
        """Detect when remaining milestones are compressed into too little time."""
        plan = self._store.load(program_id, "milestone_plan", MilestonePlan)
        if plan is None:
            return []

        signals = []
        remaining = [
            m for m in plan.milestones
            if m.status not in (MilestoneStatus.COMPLETED,) and m.target_date
        ]
        if len(remaining) >= 2:
            dates = sorted(m.target_date for m in remaining)
            span_days = (dates[-1] - dates[0]).days
            if span_days > 0 and len(remaining) / span_days > 0.5:  # >1 milestone per 2 days
                signals.append(RiskSignal(
                    category="schedule_compression",
                    title="Milestone compression detected",
                    detail=f"{len(remaining)} milestones packed into {span_days} days.",
                    recommended_action="Review scope: can any milestones be parallelized, "
                                       "deferred, or descoped?",
                    severity="HIGH",
                ))
        return signals

    def _check_risk_without_mitigation(self, program_id: str) -> list[RiskSignal]:
        """Flag high/critical risks that have no mitigation plan."""
        raid = self._store.load(program_id, "raid_log", RAIDLog)
        if raid is None:
            return []

        signals = []
        for risk in raid.open_risks:
            if risk.severity in (Severity.HIGH, Severity.CRITICAL) and not risk.mitigation.strip():
                signals.append(RiskSignal(
                    category="unmitigated_risk",
                    title=f"High-severity risk with no mitigation: {risk.title}",
                    detail=f"Severity: {risk.severity.value}, Owner: {risk.owner or 'UNOWNED'}",
                    recommended_action=f"Assign a mitigation plan and owner for risk '{risk.title}'.",
                    severity=risk.severity.value,
                ))
        return signals

    def _check_dependency_clustering(self, program_id: str) -> list[RiskSignal]:
        """Flag when too many open dependencies point at the same team."""
        raid = self._store.load(program_id, "raid_log", RAIDLog)
        if raid is None:
            return []

        team_counts: dict[str, int] = {}
        for dep in raid.open_dependencies:
            team = dep.upstream_team.strip()
            if team:
                team_counts[team] = team_counts.get(team, 0) + 1

        signals = []
        for team, count in team_counts.items():
            if count >= 3:
                signals.append(RiskSignal(
                    category="dependency_clustering",
                    title=f"Dependency bottleneck on team: {team}",
                    detail=f"{count} open dependencies blocked on {team}.",
                    recommended_action=f"Coordinate with {team} lead to batch-resolve "
                                       f"dependencies or find alternatives.",
                    severity="HIGH",
                ))
        return signals

    def _check_approaching_deadlines(self, program_id: str) -> list[RiskSignal]:
        """Flag milestones due within 7 days that are not yet in progress."""
        plan = self._store.load(program_id, "milestone_plan", MilestonePlan)
        if plan is None:
            return []

        signals = []
        today = date.today()
        threshold = today + timedelta(days=7)
        for m in plan.milestones:
            if (
                m.target_date
                and m.target_date <= threshold
                and m.status == MilestoneStatus.NOT_STARTED
            ):
                days_left = (m.target_date - today).days
                signals.append(RiskSignal(
                    category="approaching_deadline",
                    title=f"Milestone not started but due in {days_left} days: {m.name}",
                    detail=f"Due: {m.target_date}, Owner: {m.owner}",
                    recommended_action=f"Confirm with {m.owner} that work is planned "
                                       f"or reschedule the milestone.",
                    severity="HIGH" if days_left <= 3 else "MEDIUM",
                ))
        return signals
