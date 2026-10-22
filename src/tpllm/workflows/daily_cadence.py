"""Daily cadence engine — the TPM's automated daily routine.

Replicates the TPM's day structure but runs in seconds instead of hours:
  1. Intake & situational awareness
  2. Update sweep
  3. Critical path work (dependency chasing)
  4. Documentation & propagation
  5. Quality & bug economy
  6. Status & exec comms
  7. Close loops
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.dependency_tracker import DependencyTracker, DriftAlert
from tpllm.workflows.risk_engine import RiskEngine, RiskSignal
from tpllm.workflows.update_aggregator import ProgramPulse, UpdateAggregator


@dataclass
class DailyBrief:
    """The output of the daily cadence — everything the team needs to know."""

    program_id: str
    date: date = field(default_factory=date.today)
    pulse: ProgramPulse | None = None
    drift_alerts: list[DriftAlert] = field(default_factory=list)
    risk_signals: list[RiskSignal] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"# Daily Brief — {self.program_id} — {self.date.isoformat()}"]

        if self.pulse:
            lines.append(f"\n**Health: {self.pulse.health_signal}**")
            lines.append(
                f"Milestones: {self.pulse.completed_milestones}/{self.pulse.total_milestones} done | "
                f"{self.pulse.at_risk_milestones} at risk | {self.pulse.overdue_milestones} overdue"
            )
            lines.append(
                f"RAID: {self.pulse.open_risks} risks ({self.pulse.critical_risks} critical), "
                f"{self.pulse.open_issues} issues, {self.pulse.open_dependencies} deps"
            )
            lines.append(
                f"Actions: {self.pulse.open_actions} open, {self.pulse.overdue_actions} overdue"
            )

        if self.drift_alerts:
            lines.append("\n## Dependency Drift")
            for alert in self.drift_alerts:
                lines.append(f"- [{alert.severity}] **{alert.title}**: {alert.detail}")
                lines.append(f"  Action: {alert.suggested_action}")

        if self.risk_signals:
            lines.append("\n## Risk Signals")
            for signal in self.risk_signals:
                lines.append(f"- [{signal.severity}] **{signal.title}**: {signal.detail}")
                lines.append(f"  Action: {signal.recommended_action}")

        if self.recommended_actions:
            lines.append("\n## Today's Recommended Actions")
            for i, action in enumerate(self.recommended_actions, 1):
                lines.append(f"{i}. {action}")

        return "\n".join(lines)


class DailyCadence:
    """Run the full daily TPM cadence for a program."""

    def __init__(self, store: ArtifactStore, stale_threshold_hours: int = 48) -> None:
        self._store = store
        self._aggregator = UpdateAggregator(store)
        self._dependency_tracker = DependencyTracker(store, stale_threshold_hours)
        self._risk_engine = RiskEngine(store)

    def run(self, program_id: str) -> DailyBrief:
        brief = DailyBrief(program_id=program_id)

        # 1. Aggregate program pulse
        brief.pulse = self._aggregator.aggregate(program_id)

        # 2. Check for dependency drift
        brief.drift_alerts = self._dependency_tracker.scan(program_id)

        # 3. Scan for risk signals
        brief.risk_signals = self._risk_engine.scan(program_id)

        # 4. Synthesise recommended actions
        brief.recommended_actions = self._synthesise_actions(brief)

        # 5. Save the brief
        self._store.save_raw(
            program_id,
            f"daily_brief_{brief.date.isoformat()}",
            brief.render(),
        )

        return brief

    def _synthesise_actions(self, brief: DailyBrief) -> list[str]:
        actions: list[str] = []

        # High-severity drift alerts become top actions
        for alert in brief.drift_alerts:
            if alert.severity == "HIGH":
                actions.append(alert.suggested_action)

        # Critical risk signals
        for signal in brief.risk_signals:
            if signal.severity in ("CRITICAL", "HIGH"):
                actions.append(signal.recommended_action)

        # Overdue actions reminder
        if brief.pulse and brief.pulse.overdue_actions > 0:
            actions.append(
                f"Follow up on {brief.pulse.overdue_actions} overdue action items."
            )

        # Overdue milestones
        if brief.pulse and brief.pulse.overdue_milestones > 0:
            actions.append(
                f"Review {brief.pulse.overdue_milestones} overdue milestones — "
                f"reschedule or escalate."
            )

        return actions
