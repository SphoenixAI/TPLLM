"""Personalized Briefing engine — role-aware summaries for each person.

Engineers get their items. PMs get program health. Execs get risks + asks.
Nobody gets noise they don't need.
"""

from __future__ import annotations

from datetime import date

from lltpm.artifacts.store import ArtifactStore
from lltpm.artifacts.meeting_notes import MeetingNotesManager
from lltpm.artifacts.milestone_plan import MilestonePlanManager
from lltpm.artifacts.raid_log import RAIDLogManager
from lltpm.workflows.delegation_engine import DelegationEngine
from lltpm.workflows.update_aggregator import UpdateAggregator
from lltpm.models.briefing import (
    BriefingFormat,
    BriefingSection,
    PersonalizedBriefing,
    RoleType,
)


# Map roles to briefing formats
_ROLE_FORMAT = {
    RoleType.ENGINEER: BriefingFormat.ENGINEER,
    RoleType.TECH_LEAD: BriefingFormat.TEAM_LEAD,
    RoleType.ENGINEERING_MANAGER: BriefingFormat.TEAM_LEAD,
    RoleType.PRODUCT_MANAGER: BriefingFormat.FULL,
    RoleType.DESIGNER: BriefingFormat.ENGINEER,
    RoleType.EXECUTIVE: BriefingFormat.EXECUTIVE,
    RoleType.STAKEHOLDER: BriefingFormat.EXECUTIVE,
}


class PersonalizedBriefingEngine:
    """Generate role-filtered briefings for each team member."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store
        self._aggregator = UpdateAggregator(store)
        self._milestones = MilestonePlanManager(store)
        self._raid = RAIDLogManager(store)
        self._meetings = MeetingNotesManager(store)
        self._delegation = DelegationEngine(store)

    def generate(
        self,
        program_id: str,
        person: str,
        role: str = "ENGINEER",
    ) -> PersonalizedBriefing:
        """Generate a personalized briefing for one person."""
        try:
            role_type = RoleType(role)
        except ValueError:
            role_type = RoleType.ENGINEER

        fmt = _ROLE_FORMAT.get(role_type, BriefingFormat.ENGINEER)

        briefing = PersonalizedBriefing(
            person=person,
            role=role_type,
            program_id=program_id,
            format=fmt,
        )

        # Get program pulse for context
        pulse = self._aggregator.aggregate(program_id)

        if fmt == BriefingFormat.ENGINEER:
            briefing = self._build_engineer_briefing(briefing, program_id, person, pulse)
        elif fmt == BriefingFormat.TEAM_LEAD:
            briefing = self._build_lead_briefing(briefing, program_id, person, pulse)
        elif fmt == BriefingFormat.EXECUTIVE:
            briefing = self._build_exec_briefing(briefing, program_id, person, pulse)
        else:
            briefing = self._build_full_briefing(briefing, program_id, person, pulse)

        return briefing

    def _build_engineer_briefing(self, briefing, program_id, person, pulse):
        """Engineer briefing: my items, my blockers, my deadlines."""
        # Their delegations
        my_items = self._delegation.get_by_assignee(program_id, person)
        if my_items:
            section = BriefingSection(
                header="Your Items",
                action_required=True,
                items=[],
            )
            for item in sorted(my_items, key=lambda x: x.priority.value):
                due = item.due_date.isoformat() if item.due_date else "TBD"
                overdue = " (OVERDUE)" if item.is_overdue else ""
                section.items.append(
                    f"[{item.priority.value}] {item.title} — Due: {due}{overdue}"
                )
                if item.is_overdue:
                    section.urgency = "HIGH"
            briefing.sections.append(section)

        # Their action items
        open_actions = self._meetings.get_open_items(program_id)
        my_actions = [a for a in open_actions if a.owner == person]
        if my_actions:
            section = BriefingSection(header="Your Action Items", items=[])
            for a in my_actions:
                due = a.due_date.isoformat() if a.due_date else "TBD"
                overdue = " (OVERDUE)" if a.is_overdue else ""
                section.items.append(f"{a.description} — Due: {due}{overdue}")
                if a.is_overdue:
                    section.urgency = "HIGH"
                    section.action_required = True
            briefing.sections.append(section)

        # Milestones they own
        plan = self._milestones.get_plan(program_id)
        my_milestones = [m for m in plan.milestones if m.owner == person]
        if my_milestones:
            section = BriefingSection(header="Your Milestones", items=[])
            for m in my_milestones:
                due = m.target_date.isoformat() if m.target_date else "TBD"
                section.items.append(f"{m.name} [{m.status.value}] — Due: {due}")
            briefing.sections.append(section)

        # Quick actions
        if my_items:
            briefing.quick_actions = [
                f"Update status on: {my_items[0].title}" if my_items else "",
            ]

        # Program context (minimal)
        briefing.sections.append(BriefingSection(
            header="Program Pulse",
            items=[f"Health: {pulse.health_signal} | "
                   f"Your items: {len(my_items)} open | "
                   f"Program: {pulse.completed_milestones}/{pulse.total_milestones} milestones done"],
        ))

        briefing.greeting = f"Good morning, {person}. Here's what needs your attention today."
        return briefing

    def _build_lead_briefing(self, briefing, program_id, person, pulse):
        """Team lead briefing: team status, risks, decisions needed."""
        # Program health
        briefing.sections.append(BriefingSection(
            header="Program Health",
            urgency="HIGH" if pulse.health_signal == "RED" else "NORMAL",
            items=[
                f"Overall: {pulse.health_signal}",
                f"Milestones: {pulse.completed_milestones}/{pulse.total_milestones} "
                f"({pulse.at_risk_milestones} at risk, {pulse.overdue_milestones} overdue)",
                f"Open risks: {pulse.open_risks} ({pulse.critical_risks} critical)",
                f"Actions: {pulse.open_actions} open, {pulse.overdue_actions} overdue",
            ],
        ))

        # Team delegation overview
        delegation_plan = self._delegation.get_plan(program_id)
        if delegation_plan.overdue_items:
            section = BriefingSection(
                header="Overdue Items (Team)",
                urgency="HIGH",
                action_required=True,
                items=[],
            )
            for item in delegation_plan.overdue_items:
                section.items.append(
                    f"[{item.priority.value}] {item.title} → {item.assigned_to} "
                    f"(due {item.due_date})"
                )
            briefing.sections.append(section)

        # Pending decisions
        log = self._raid.get_log(program_id)
        critical_risks = [r for r in log.open_risks if r.severity.value in ("CRITICAL", "HIGH")]
        if critical_risks:
            section = BriefingSection(
                header="Critical Risks",
                urgency="HIGH",
                items=[f"[{r.severity.value}] {r.title}: {r.description[:80]}" for r in critical_risks],
            )
            briefing.sections.append(section)

        briefing.greeting = f"{person} — here's your team overview for today."
        return briefing

    def _build_exec_briefing(self, briefing, program_id, person, pulse):
        """Executive briefing: risks, asks, blockers only."""
        # Health — one line
        briefing.sections.append(BriefingSection(
            header="Program Health",
            urgency="HIGH" if pulse.health_signal == "RED" else "NORMAL",
            items=[
                f"{pulse.health_signal} — "
                f"{pulse.completed_milestones}/{pulse.total_milestones} milestones | "
                f"{pulse.critical_risks} critical risks | "
                f"{pulse.overdue_milestones} overdue",
            ],
        ))

        # Top risks
        log = self._raid.get_log(program_id)
        critical = [r for r in log.open_risks if r.severity.value in ("CRITICAL", "HIGH")]
        if critical:
            briefing.sections.append(BriefingSection(
                header="Top Risks",
                urgency="HIGH" if any(r.severity.value == "CRITICAL" for r in critical) else "NORMAL",
                items=[f"[{r.severity.value}] {r.title} — Owner: {r.owner}" for r in critical[:5]],
            ))

        # Asks (things that need exec attention)
        delegation_plan = self._delegation.get_plan(program_id)
        escalations = [i for i in delegation_plan.open_items if i.category.value == "ESCALATION"]
        if escalations:
            briefing.sections.append(BriefingSection(
                header="Needs Your Attention",
                action_required=True,
                items=[f"{i.title} — {i.context[:80]}" for i in escalations[:5]],
            ))

        briefing.greeting = f"{person} — executive summary for {date.today().isoformat()}."
        return briefing

    def _build_full_briefing(self, briefing, program_id, person, pulse):
        """Full briefing for PMs — everything."""
        self._build_lead_briefing(briefing, program_id, person, pulse)

        # Also add scope changes
        # (scope lock data would go here if integrated)

        briefing.greeting = f"{person} — full program briefing for today."
        return briefing
