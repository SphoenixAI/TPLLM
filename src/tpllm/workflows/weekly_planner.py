"""Weekly planner — enforces the weekly cadence rhythm.

Monday:    Plan integrity — reconfirm priorities, milestones, critical path
Tuesday:   Execution acceleration — unblock the hardest dependency
Wednesday: Quality + integration — build health, test coverage, perf
Thursday:  Launch readiness / cross-functional gates
Friday:    Reporting + retro + next week setup
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Weekday(str, Enum):
    MONDAY = "MONDAY"
    TUESDAY = "TUESDAY"
    WEDNESDAY = "WEDNESDAY"
    THURSDAY = "THURSDAY"
    FRIDAY = "FRIDAY"


DAILY_THEMES = {
    Weekday.MONDAY: {
        "theme": "Plan Integrity",
        "focus": [
            "Reconfirm priorities, milestones, and critical path.",
            "Ensure dependencies have owners and dates.",
            "Set weekly top risks and mitigation actions.",
        ],
    },
    Weekday.TUESDAY: {
        "theme": "Execution Acceleration",
        "focus": [
            "Deep unblock day: resolve the hardest dependency/risk.",
            "Reduce work-in-progress; push tasks to done.",
            "Follow up on Monday's risk mitigations.",
        ],
    },
    Weekday.WEDNESDAY: {
        "theme": "Quality + Integration",
        "focus": [
            "Integration readiness checks (build health, test coverage, perf).",
            "Bug triage + dogfooding pulse.",
            "Verify CI/CD pipelines are green.",
        ],
    },
    Weekday.THURSDAY: {
        "theme": "Launch Readiness / Cross-Functional Gates",
        "focus": [
            "Security/privacy/legal reviews if needed.",
            "Rollout + monitoring + support readiness.",
            "Confirm gate sign-offs are on track.",
        ],
    },
    Weekday.FRIDAY: {
        "theme": "Reporting + Retro + Next Week Setup",
        "focus": [
            "Publish weekly status update.",
            "Run short retro: what slowed us down, what to change.",
            "Update templates/process to remove repeated friction.",
        ],
    },
}


@dataclass
class WeeklyPlan:
    program_id: str
    week_of: date = field(default_factory=date.today)
    daily_plans: dict[str, dict] = field(default_factory=dict)
    weekly_goals: list[str] = field(default_factory=list)
    carryover_items: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"# Weekly Plan — {self.program_id} — Week of {self.week_of.isoformat()}",
        ]
        if self.weekly_goals:
            lines.append("\n## Weekly Goals")
            for g in self.weekly_goals:
                lines.append(f"- {g}")
        if self.carryover_items:
            lines.append("\n## Carryover from Last Week")
            for c in self.carryover_items:
                lines.append(f"- {c}")
        for day in Weekday:
            theme = DAILY_THEMES[day]
            lines.append(f"\n## {day.value} — {theme['theme']}")
            for f in theme["focus"]:
                lines.append(f"- {f}")
            extras = self.daily_plans.get(day.value, {}).get("extras", [])
            for e in extras:
                lines.append(f"- [ADDED] {e}")
        return "\n".join(lines)


class WeeklyPlanner:
    """Generate and manage the weekly cadence plan."""

    def generate(
        self,
        program_id: str,
        weekly_goals: list[str] | None = None,
        carryover: list[str] | None = None,
    ) -> WeeklyPlan:
        today = date.today()
        # Find Monday of this week
        monday = today - __import__("datetime").timedelta(days=today.weekday())
        return WeeklyPlan(
            program_id=program_id,
            week_of=monday,
            weekly_goals=weekly_goals or [],
            carryover_items=carryover or [],
        )

    def get_today_focus(self) -> dict:
        today = date.today()
        weekday_idx = today.weekday()
        if weekday_idx > 4:  # weekend
            return {
                "theme": "Weekend",
                "focus": ["Rest. The program will be there Monday."],
            }
        day = list(Weekday)[weekday_idx]
        return DAILY_THEMES[day]
