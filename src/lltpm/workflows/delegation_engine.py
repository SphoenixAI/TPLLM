"""Delegation Engine — intelligent work routing and assignment.

The delegation engine takes signals from across the TPM system and
produces actionable delegation plans:

1. Meeting Intelligence → commitments become delegation items
2. Risk Signals → mitigations become delegation items
3. Dependency Drift → chase actions become delegation items
4. Action Items → get routed to the right people
5. Overdue items → get escalation delegations

The engine considers:
- Team member expertise and current load
- Priority based on urgency and program impact
- Due dates based on upstream deadlines
- Context richness — every delegation includes full context so
  the assignee doesn't need to ask "what do you need?"
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from lltpm.artifacts.store import ArtifactStore
from lltpm.artifacts.meeting_notes import MeetingNotesManager
from lltpm.artifacts.raid_log import RAIDLogManager
from lltpm.artifacts.milestone_plan import MilestonePlanManager
from lltpm.models.delegation import (
    DelegationCategory,
    DelegationItem,
    DelegationPlan,
    DelegationPriority,
    DelegationStatus,
    TeamMemberProfile,
)
from lltpm.models.meeting_intel import (
    MeetingIntelligence,
    UrgencyLevel,
)
from lltpm.models.risk import Severity

DELEGATION_ARTIFACT = "delegation_plan"
TEAM_PROFILES_ARTIFACT = "team_profiles"

# Maps urgency to delegation priority
_URGENCY_TO_PRIORITY = {
    UrgencyLevel.CRITICAL: DelegationPriority.P0,
    UrgencyLevel.HIGH: DelegationPriority.P1,
    UrgencyLevel.MEDIUM: DelegationPriority.P2,
    UrgencyLevel.LOW: DelegationPriority.P3,
}

_SEVERITY_TO_PRIORITY = {
    Severity.CRITICAL: DelegationPriority.P0,
    Severity.HIGH: DelegationPriority.P1,
    Severity.MEDIUM: DelegationPriority.P2,
    Severity.LOW: DelegationPriority.P3,
}

# Default due date offsets by priority
_PRIORITY_DUE_DAYS = {
    DelegationPriority.P0: 1,
    DelegationPriority.P1: 3,
    DelegationPriority.P2: 7,
    DelegationPriority.P3: 14,
    DelegationPriority.P4: 30,
}


class DelegationEngine:
    """Intelligent delegation engine for TPM work routing.

    Analyzes program state across all artifacts and produces
    delegation plans with proper context, priority, and routing.
    """

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store
        self._raid = RAIDLogManager(store)
        self._milestones = MilestonePlanManager(store)
        self._meetings = MeetingNotesManager(store)

    # ── Team profile management ───────────────────────────────

    def register_team_member(
        self,
        program_id: str,
        name: str,
        role: str = "",
        team: str = "",
        expertise: list[str] | None = None,
        max_load: int = 5,
    ) -> TeamMemberProfile:
        """Register a team member for intelligent routing."""
        profiles = self._load_profiles(program_id)
        profile = TeamMemberProfile(
            name=name,
            role=role,
            team=team,
            expertise=expertise or [],
            max_load=max_load,
        )
        # Update existing or add new
        profiles = [p for p in profiles if p.name != name]
        profiles.append(profile)
        self._save_profiles(program_id, profiles)
        return profile

    def get_team(self, program_id: str) -> list[TeamMemberProfile]:
        """Get all registered team members."""
        return self._load_profiles(program_id)

    # ── Delegation item management ────────────────────────────

    def delegate(
        self,
        program_id: str,
        title: str,
        description: str = "",
        category: str = "EXECUTION",
        priority: str = "P2",
        assigned_to: str = "",
        due_date: str | None = None,
        source: str = "",
        source_id: str = "",
        context: str = "",
        success_criteria: str = "",
        assignment_rationale: str = "",
    ) -> DelegationItem:
        """Create a delegation item manually."""
        plan = self._load_plan(program_id)

        try:
            cat = DelegationCategory(category)
        except ValueError:
            cat = DelegationCategory.EXECUTION
        try:
            pri = DelegationPriority(priority)
        except ValueError:
            pri = DelegationPriority.P2

        due = None
        if due_date:
            due = date.fromisoformat(due_date)
        elif pri:
            due = date.today() + timedelta(days=_PRIORITY_DUE_DAYS.get(pri, 7))

        item = DelegationItem(
            id=f"DLG-{uuid4().hex[:6]}",
            title=title,
            description=description,
            category=cat,
            priority=pri,
            assigned_to=assigned_to,
            assignment_rationale=assignment_rationale,
            program_id=program_id,
            source=source,
            source_id=source_id,
            context=context,
            success_criteria=success_criteria,
            due_date=due,
        )

        # If assigned, update their load
        if assigned_to:
            item.status = DelegationStatus.ACCEPTED
            self._increment_load(program_id, assigned_to)

        plan.items.append(item)
        self._save_plan(program_id, plan)
        return item

    def update_status(
        self,
        program_id: str,
        delegation_id: str,
        status: str,
        blockers: list[str] | None = None,
    ) -> bool:
        """Update the status of a delegation item."""
        plan = self._load_plan(program_id)
        for item in plan.items:
            if item.id == delegation_id:
                old_status = item.status
                try:
                    item.status = DelegationStatus(status)
                except ValueError:
                    return False
                if blockers is not None:
                    item.blockers = blockers
                if item.status == DelegationStatus.COMPLETED and old_status != DelegationStatus.COMPLETED:
                    from datetime import datetime
                    item.completed_at = datetime.utcnow()
                    self._decrement_load(program_id, item.assigned_to)
                self._save_plan(program_id, plan)
                return True
        return False

    def reassign(
        self,
        program_id: str,
        delegation_id: str,
        new_assignee: str,
        rationale: str = "",
    ) -> bool:
        """Reassign a delegation item to a different person."""
        plan = self._load_plan(program_id)
        for item in plan.items:
            if item.id == delegation_id:
                old_assignee = item.assigned_to
                item.assigned_to = new_assignee
                item.assignment_rationale = rationale
                item.status = DelegationStatus.REASSIGNED
                if old_assignee:
                    self._decrement_load(program_id, old_assignee)
                self._increment_load(program_id, new_assignee)
                self._save_plan(program_id, plan)
                return True
        return False

    # ── Intelligent delegation from meeting intelligence ──────

    def delegate_from_meeting(
        self,
        program_id: str,
        intel: MeetingIntelligence,
    ) -> list[DelegationItem]:
        """Generate delegation items from meeting intelligence.

        Converts extracted commitments, risks, decisions, and
        dependencies into actionable delegation items with proper
        context and routing.
        """
        items: list[DelegationItem] = []
        plan = self._load_plan(program_id)
        profiles = self._load_profiles(program_id)
        profile_map = {p.name: p for p in profiles}

        # 1. Commitments → delegation items
        for commitment in intel.commitments:
            item = DelegationItem(
                id=f"DLG-{uuid4().hex[:6]}",
                title=commitment.description[:100],
                description=commitment.description,
                category=DelegationCategory.EXECUTION,
                priority=_URGENCY_TO_PRIORITY.get(intel.overall_urgency, DelegationPriority.P2),
                assigned_to=commitment.owner,
                assignment_rationale=f"Self-committed in meeting: {intel.meeting_title}",
                program_id=program_id,
                source=f"Meeting: {intel.meeting_title}",
                source_id=intel.meeting_id,
                context=f"Committed during {intel.meeting_title} on {intel.meeting_date.isoformat()}. "
                        f"Quote: \"{commitment.source_quote}\"" if commitment.source_quote else "",
                success_criteria=commitment.description,
                due_date=commitment.due_date or (date.today() + timedelta(days=7)),
            )
            if commitment.owner:
                item.status = DelegationStatus.ACCEPTED
            items.append(item)
            plan.items.append(item)

        # 2. Risks → risk mitigation delegations
        for risk in intel.risks:
            owner = risk.mentioned_by or self._find_best_assignee(profiles, ["risk", "mitigation"])
            item = DelegationItem(
                id=f"DLG-{uuid4().hex[:6]}",
                title=f"Mitigate risk: {risk.title}",
                description=risk.description,
                category=DelegationCategory.RISK_MITIGATION,
                priority=_URGENCY_TO_PRIORITY.get(risk.severity, DelegationPriority.P2),
                assigned_to=owner,
                assignment_rationale=f"Raised risk in meeting: {intel.meeting_title}",
                program_id=program_id,
                source=f"Meeting: {intel.meeting_title}",
                source_id=intel.meeting_id,
                context=f"Risk identified: {risk.description}. "
                        f"Severity: {risk.severity.value}. "
                        f"Source: \"{risk.source_quote}\"" if risk.source_quote else "",
                success_criteria=f"Risk '{risk.title}' has documented mitigation plan with owner and timeline",
                due_date=date.today() + timedelta(days=_PRIORITY_DUE_DAYS.get(
                    _URGENCY_TO_PRIORITY.get(risk.severity, DelegationPriority.P2), 7
                )),
            )
            items.append(item)
            plan.items.append(item)

        # 3. Pending decisions → decision delegations
        for decision in intel.decisions:
            if not decision.is_final:
                owner = decision.decided_by or self._find_best_assignee(profiles, ["decision", "lead"])
                item = DelegationItem(
                    id=f"DLG-{uuid4().hex[:6]}",
                    title=f"Decide: {decision.title}",
                    description=f"Options: {', '.join(decision.options_discussed)}" if decision.options_discussed else decision.title,
                    category=DelegationCategory.DECISION,
                    priority=DelegationPriority.P1,
                    assigned_to=owner,
                    assignment_rationale="Decision owner from meeting discussion",
                    program_id=program_id,
                    source=f"Meeting: {intel.meeting_title}",
                    source_id=intel.meeting_id,
                    context=f"Discussed in {intel.meeting_title}. "
                            f"Options: {decision.options_discussed}. "
                            f"Quote: \"{decision.source_quote}\"" if decision.source_quote else "",
                    success_criteria=f"Decision '{decision.title}' is documented with rationale and follow-ups",
                    due_date=date.today() + timedelta(days=3),
                )
                items.append(item)
                plan.items.append(item)

        # 4. Dependencies → dependency chase delegations
        for dep in intel.dependencies:
            owner = self._find_best_assignee(profiles, ["dependency", dep.upstream]) if not dep.downstream else dep.downstream
            item = DelegationItem(
                id=f"DLG-{uuid4().hex[:6]}",
                title=f"Chase dependency: {dep.title}",
                description=f"{dep.upstream} → {dep.downstream}: {dep.title}",
                category=DelegationCategory.DEPENDENCY_CHASE,
                priority=DelegationPriority.P2,
                assigned_to=owner,
                assignment_rationale=f"Downstream consumer of dependency from {dep.upstream}",
                program_id=program_id,
                source=f"Meeting: {intel.meeting_title}",
                source_id=intel.meeting_id,
                context=f"Dependency on {dep.upstream}. "
                        f"Target: {dep.target_date.isoformat() if dep.target_date else 'TBD'}. "
                        f"Source: \"{dep.source_quote}\"" if dep.source_quote else "",
                success_criteria=f"Dependency '{dep.title}' has confirmed commitment date and owner from {dep.upstream}",
                due_date=dep.target_date or (date.today() + timedelta(days=7)),
            )
            items.append(item)
            plan.items.append(item)

        self._save_plan(program_id, plan)
        return items

    # ── Delegation from program state ─────────────────────────

    def delegate_from_risks(self, program_id: str) -> list[DelegationItem]:
        """Generate delegation items from unmitigated risks in the RAID log."""
        items: list[DelegationItem] = []
        plan = self._load_plan(program_id)
        log = self._raid.get_log(program_id)

        for risk in log.open_risks:
            if not risk.mitigation:
                item = DelegationItem(
                    id=f"DLG-{uuid4().hex[:6]}",
                    title=f"Mitigate risk: {risk.title}",
                    description=risk.description,
                    category=DelegationCategory.RISK_MITIGATION,
                    priority=_SEVERITY_TO_PRIORITY.get(risk.severity, DelegationPriority.P2),
                    assigned_to=risk.owner,
                    assignment_rationale="Risk owner from RAID log",
                    program_id=program_id,
                    source="RAID Log",
                    source_id=risk.id,
                    context=f"Risk: {risk.title}\nImpact: {risk.impact}\nSeverity: {risk.severity.value}",
                    success_criteria=f"Documented mitigation plan for '{risk.title}'",
                    due_date=risk.due_date or (date.today() + timedelta(days=_PRIORITY_DUE_DAYS.get(
                        _SEVERITY_TO_PRIORITY.get(risk.severity, DelegationPriority.P2), 7
                    ))),
                )
                items.append(item)
                plan.items.append(item)

        if items:
            self._save_plan(program_id, plan)
        return items

    def delegate_from_overdue(self, program_id: str) -> list[DelegationItem]:
        """Generate escalation delegations for overdue action items."""
        items: list[DelegationItem] = []
        plan = self._load_plan(program_id)
        overdue = self._meetings.get_overdue_items(program_id)

        for action in overdue:
            # Check if we already have a delegation for this
            existing = [i for i in plan.items if i.source_id == action.id]
            if existing:
                continue

            item = DelegationItem(
                id=f"DLG-{uuid4().hex[:6]}",
                title=f"Follow up: {action.description[:80]}",
                description=f"Overdue action item: {action.description}",
                category=DelegationCategory.ESCALATION,
                priority=DelegationPriority.P1,
                assigned_to=action.owner,
                assignment_rationale="Original owner of overdue action item",
                program_id=program_id,
                source=action.source,
                source_id=action.id,
                context=f"Action item '{action.description}' was due {action.due_date} and is overdue. "
                        f"Source: {action.source}",
                success_criteria=action.description,
                due_date=date.today() + timedelta(days=1),
            )
            item.follow_up_count = 1
            items.append(item)
            plan.items.append(item)

        if items:
            self._save_plan(program_id, plan)
        return items

    # ── Queries ───────────────────────────────────────────────

    def get_plan(self, program_id: str) -> DelegationPlan:
        """Get the full delegation plan."""
        return self._load_plan(program_id)

    def get_summary(self, program_id: str) -> str:
        """Get a rendered summary of the delegation plan."""
        return self._load_plan(program_id).summary()

    def get_by_assignee(self, program_id: str, assignee: str) -> list[DelegationItem]:
        """Get all open delegation items for a specific person."""
        plan = self._load_plan(program_id)
        return [i for i in plan.open_items if i.assigned_to == assignee]

    def get_overdue(self, program_id: str) -> list[DelegationItem]:
        """Get all overdue delegation items."""
        return self._load_plan(program_id).overdue_items

    def get_blocked(self, program_id: str) -> list[DelegationItem]:
        """Get all blocked delegation items."""
        return self._load_plan(program_id).blocked_items

    # ── Internal helpers ──────────────────────────────────────

    def _find_best_assignee(self, profiles: list[TeamMemberProfile], keywords: list[str]) -> str:
        """Find the best assignee based on expertise match and capacity."""
        if not profiles:
            return ""

        scored: list[tuple[float, TeamMemberProfile]] = []
        for p in profiles:
            if not p.has_capacity:
                continue
            # Score based on expertise match
            expertise_score = sum(
                1 for kw in keywords
                for exp in p.expertise
                if kw.lower() in exp.lower()
            )
            # Penalize high load
            load_penalty = p.load_factor * 2
            score = expertise_score - load_penalty
            scored.append((score, p))

        if not scored:
            # Everyone is at capacity — pick least loaded
            return min(profiles, key=lambda p: p.load_factor).name

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1].name

    def _increment_load(self, program_id: str, name: str) -> None:
        profiles = self._load_profiles(program_id)
        for p in profiles:
            if p.name == name:
                p.current_load += 1
                break
        self._save_profiles(program_id, profiles)

    def _decrement_load(self, program_id: str, name: str) -> None:
        profiles = self._load_profiles(program_id)
        for p in profiles:
            if p.name == name:
                p.current_load = max(0, p.current_load - 1)
                break
        self._save_profiles(program_id, profiles)

    def _load_plan(self, program_id: str) -> DelegationPlan:
        plan = self._store.load(program_id, DELEGATION_ARTIFACT, DelegationPlan)
        if plan is None:
            plan = DelegationPlan(program_id=program_id)
        return plan

    def _save_plan(self, program_id: str, plan: DelegationPlan) -> None:
        self._store.save(program_id, DELEGATION_ARTIFACT, plan)

    def _load_profiles(self, program_id: str) -> list[TeamMemberProfile]:
        raw = self._store.load_raw(program_id, TEAM_PROFILES_ARTIFACT)
        if raw is None:
            return []
        try:
            data = json.loads(raw)
            return [TeamMemberProfile.model_validate(p) for p in data]
        except (json.JSONDecodeError, TypeError):
            return []

    def _save_profiles(self, program_id: str, profiles: list[TeamMemberProfile]) -> None:
        data = [p.model_dump() for p in profiles]
        self._store.save_raw(program_id, TEAM_PROFILES_ARTIFACT, json.dumps(data, indent=2))
