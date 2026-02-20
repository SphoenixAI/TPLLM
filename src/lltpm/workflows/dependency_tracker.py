"""Dependency drift detection and tracking.

Scans for:
- Tickets on the critical path with no movement in N hours
- Dependencies past their commitment date
- Unowned dependencies
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.milestone import MilestonePlan, MilestoneStatus
from lltpm.models.risk import Dependency, RAIDItemStatus, RAIDLog


@dataclass
class DriftAlert:
    """A detected dependency or milestone drift."""

    kind: str  # "stale_milestone", "overdue_dependency", "unowned_dependency"
    title: str
    detail: str
    suggested_action: str
    severity: str  # "HIGH", "MEDIUM", "LOW"


class DependencyTracker:
    def __init__(self, store: ArtifactStore, stale_threshold_hours: int = 48) -> None:
        self._store = store
        self._stale_hours = stale_threshold_hours

    def scan(self, program_id: str) -> list[DriftAlert]:
        alerts: list[DriftAlert] = []
        alerts.extend(self._check_stale_milestones(program_id))
        alerts.extend(self._check_overdue_dependencies(program_id))
        alerts.extend(self._check_unowned_dependencies(program_id))
        return alerts

    def _check_stale_milestones(self, program_id: str) -> list[DriftAlert]:
        plan = self._store.load(program_id, "milestone_plan", MilestonePlan)
        if plan is None:
            return []
        alerts = []
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for m in plan.milestones:
            if m.status in (MilestoneStatus.IN_PROGRESS, MilestoneStatus.AT_RISK):
                hours_since_update = (now - m.updated_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                if hours_since_update > self._stale_hours:
                    alerts.append(DriftAlert(
                        kind="stale_milestone",
                        title=f"Stale milestone: {m.name}",
                        detail=f"No update in {int(hours_since_update)}h (threshold: {self._stale_hours}h). Owner: {m.owner}",
                        suggested_action=f"Request async update from {m.owner} on milestone '{m.name}'.",
                        severity="HIGH" if m.on_critical_path else "MEDIUM",
                    ))
        return alerts

    def _check_overdue_dependencies(self, program_id: str) -> list[DriftAlert]:
        raid = self._store.load(program_id, "raid_log", RAIDLog)
        if raid is None:
            return []
        alerts = []
        today = date.today()
        for dep in raid.dependencies:
            if dep.status == RAIDItemStatus.OPEN and dep.commitment_date and dep.commitment_date < today:
                days_overdue = (today - dep.commitment_date).days
                alerts.append(DriftAlert(
                    kind="overdue_dependency",
                    title=f"Overdue dependency: {dep.title}",
                    detail=f"{days_overdue} days past commitment ({dep.commitment_date}). "
                           f"Upstream: {dep.upstream_team}, Owner: {dep.owner}",
                    suggested_action=f"Escalate to {dep.owner}: get revised commitment date or mitigation plan.",
                    severity="HIGH",
                ))
        return alerts

    def _check_unowned_dependencies(self, program_id: str) -> list[DriftAlert]:
        raid = self._store.load(program_id, "raid_log", RAIDLog)
        if raid is None:
            return []
        alerts = []
        for dep in raid.dependencies:
            if dep.status == RAIDItemStatus.OPEN and not dep.owner.strip():
                alerts.append(DriftAlert(
                    kind="unowned_dependency",
                    title=f"Unowned dependency: {dep.title}",
                    detail=f"No owner assigned. Upstream: {dep.upstream_team}",
                    suggested_action="Assign an owner to track and follow up on this dependency.",
                    severity="MEDIUM",
                ))
        return alerts
