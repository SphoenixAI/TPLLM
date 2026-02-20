"""Update aggregator — collects signals from multiple sources into a program pulse.

Instead of asking engineers "what's your status?", the aggregator pulls
data from existing systems and synthesises a decision-ready brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.milestone import MilestonePlan, MilestoneStatus
from lltpm.models.risk import RAIDLog
from lltpm.models.update import ActionItemStatus
from lltpm.artifacts.meeting_notes import ActionTracker


@dataclass
class ProgramPulse:
    """Aggregated snapshot of program health from all data sources."""

    program_id: str
    date: date = field(default_factory=date.today)
    # milestones
    total_milestones: int = 0
    completed_milestones: int = 0
    at_risk_milestones: int = 0
    overdue_milestones: int = 0
    # risks
    open_risks: int = 0
    critical_risks: int = 0
    open_issues: int = 0
    open_dependencies: int = 0
    # actions
    open_actions: int = 0
    overdue_actions: int = 0

    @property
    def health_signal(self) -> str:
        if self.critical_risks > 0 or self.overdue_milestones > 0:
            return "RED"
        if self.at_risk_milestones > 0 or self.overdue_actions > 2:
            return "YELLOW"
        return "GREEN"

    def summary(self) -> str:
        lines = [
            f"# Program Pulse — {self.program_id} — {self.date.isoformat()}",
            f"**Health: {self.health_signal}**",
            "",
            "## Milestones",
            f"- Total: {self.total_milestones} | Completed: {self.completed_milestones} "
            f"| At risk: {self.at_risk_milestones} | Overdue: {self.overdue_milestones}",
            "",
            "## RAID",
            f"- Risks: {self.open_risks} (critical: {self.critical_risks})",
            f"- Issues: {self.open_issues}",
            f"- Dependencies: {self.open_dependencies}",
            "",
            "## Actions",
            f"- Open: {self.open_actions} | Overdue: {self.overdue_actions}",
        ]
        return "\n".join(lines)


class UpdateAggregator:
    """Pull data from all artifact stores and produce a ProgramPulse."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def aggregate(self, program_id: str) -> ProgramPulse:
        pulse = ProgramPulse(program_id=program_id)

        # Milestones
        plan = self._store.load(program_id, "milestone_plan", MilestonePlan)
        if plan:
            pulse.total_milestones = len(plan.milestones)
            pulse.completed_milestones = sum(
                1 for m in plan.milestones if m.status == MilestoneStatus.COMPLETED
            )
            pulse.at_risk_milestones = len(plan.at_risk)
            pulse.overdue_milestones = len(plan.overdue)

        # RAID
        raid = self._store.load(program_id, "raid_log", RAIDLog)
        if raid:
            from lltpm.models.risk import Severity
            pulse.open_risks = len(raid.open_risks)
            pulse.critical_risks = sum(
                1 for r in raid.open_risks if r.severity == Severity.CRITICAL
            )
            pulse.open_issues = len(raid.open_issues)
            pulse.open_dependencies = len(raid.open_dependencies)

        # Actions
        tracker = self._store.load(program_id, "action_items", ActionTracker)
        if tracker:
            pulse.open_actions = len(tracker.open_items)
            pulse.overdue_actions = len(tracker.overdue_items)

        return pulse
