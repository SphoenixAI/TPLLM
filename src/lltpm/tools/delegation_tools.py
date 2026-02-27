"""Delegation tools — intelligent work routing and assignment tracking."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.workflows.delegation_engine import DelegationEngine
from lltpm.workflows.meeting_intelligence import MeetingIntelligenceEngine
from lltpm.workflows.task_board_engine import TaskBoardEngine


def get_delegation_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    engine = DelegationEngine(store)
    intel_engine = MeetingIntelligenceEngine(store)
    task_engine = TaskBoardEngine(store)

    def register_team_member(
        program_id: str,
        name: str,
        role: str = "",
        team: str = "",
        expertise: list[str] | None = None,
        max_load: int = 5,
    ) -> str:
        profile = engine.register_team_member(program_id, name, role, team, expertise, max_load)
        return json.dumps(profile.model_dump())

    def get_team(program_id: str) -> str:
        team = engine.get_team(program_id)
        if not team:
            return "No team members registered. Use register_team_member to add people."
        lines = ["# Team Roster"]
        for p in team:
            cap = "available" if p.has_capacity else "at capacity"
            lines.append(
                f"- **{p.name}** ({p.role}, {p.team}) — "
                f"Load: {p.current_load}/{p.max_load} ({cap}) — "
                f"Expertise: {', '.join(p.expertise) or 'not set'}"
            )
        return "\n".join(lines)

    # Map delegation priority codes to task board priorities
    _delegation_priority_map = {
        "P0": "CRITICAL", "P1": "HIGH", "P2": "MEDIUM", "P3": "LOW", "P4": "LOW",
    }

    def delegate_task(
        program_id: str,
        title: str,
        description: str = "",
        category: str = "EXECUTION",
        priority: str = "P2",
        assigned_to: str = "",
        due_date: str | None = None,
        source: str = "",
        context: str = "",
        success_criteria: str = "",
    ) -> str:
        item = engine.delegate(
            program_id, title, description, category, priority,
            assigned_to, due_date, source, "", context, success_criteria,
        )
        # Auto-create a corresponding task on the board
        board_priority = _delegation_priority_map.get(priority, "MEDIUM")
        source_type = "RISK_MITIGATION" if category == "RISK_MITIGATION" else "MANUAL"
        task_engine.create_task(
            program_id,
            title=title,
            description=description or context or success_criteria,
            priority=board_priority,
            assignee=assigned_to,
            due_date=due_date or "",
            source=source_type,
            source_id=item.id,
            source_context=f"Delegation: {category}",
        )
        return json.dumps(item.model_dump(), default=str)

    def delegate_from_meeting(program_id: str, meeting_id: str) -> str:
        intel = intel_engine.get_intelligence(program_id, meeting_id)
        if intel is None:
            return json.dumps({"error": f"No meeting intelligence found for {meeting_id}"})
        items = engine.delegate_from_meeting(program_id, intel)
        if not items:
            return "No delegatable items extracted from this meeting."
        lines = [f"# Delegations from Meeting ({len(items)} items)"]
        for item in items:
            lines.append(
                f"- [{item.priority.value}] **{item.title}** → {item.assigned_to or 'UNASSIGNED'} "
                f"({item.category.value}) — Due: {item.due_date}"
            )
        return "\n".join(lines)

    def delegate_from_risks(program_id: str) -> str:
        items = engine.delegate_from_risks(program_id)
        if not items:
            return "No unmitigated risks to delegate."
        # Auto-create board tasks for risk mitigation delegations
        for item in items:
            board_priority = _delegation_priority_map.get(item.priority.value, "MEDIUM")
            task_engine.create_task(
                program_id,
                title=item.title,
                description=item.description,
                priority=board_priority,
                assignee=item.assigned_to,
                due_date=item.due_date.isoformat() if item.due_date else "",
                source="RISK_MITIGATION",
                source_id=item.id,
                source_context=f"Auto-created from risk delegation",
            )
        lines = [f"# Risk Mitigation Delegations ({len(items)} items)"]
        for item in items:
            lines.append(
                f"- [{item.priority.value}] **{item.title}** → {item.assigned_to or 'UNASSIGNED'} "
                f"— Due: {item.due_date}"
            )
        lines.append(f"\n{len(items)} task(s) also added to the task board.")
        return "\n".join(lines)

    def delegate_from_overdue(program_id: str) -> str:
        items = engine.delegate_from_overdue(program_id)
        if not items:
            return "No overdue action items to escalate."
        lines = [f"# Escalation Delegations ({len(items)} items)"]
        for item in items:
            lines.append(
                f"- [{item.priority.value}] **{item.title}** → {item.assigned_to} "
                f"— Due: {item.due_date}"
            )
        return "\n".join(lines)

    def update_delegation_status(
        program_id: str,
        delegation_id: str,
        status: str,
        blockers: list[str] | None = None,
    ) -> str:
        success = engine.update_status(program_id, delegation_id, status, blockers)
        return json.dumps({"success": success})

    def reassign_delegation(
        program_id: str,
        delegation_id: str,
        new_assignee: str,
        rationale: str = "",
    ) -> str:
        success = engine.reassign(program_id, delegation_id, new_assignee, rationale)
        return json.dumps({"success": success})

    def get_delegation_summary(program_id: str) -> str:
        return engine.get_summary(program_id)

    def get_person_delegations(program_id: str, assignee: str) -> str:
        items = engine.get_by_assignee(program_id, assignee)
        if not items:
            return f"No open delegations for {assignee}."
        lines = [f"# Delegations for {assignee} ({len(items)} open)"]
        for item in sorted(items, key=lambda x: x.priority.value):
            due = item.due_date.isoformat() if item.due_date else "TBD"
            lines.append(
                f"- [{item.priority.value}] {item.title} ({item.category.value}) — "
                f"Due: {due} — Status: {item.status.value}"
            )
        return "\n".join(lines)

    return [
        {
            "name": "register_team_member",
            "description": "Register a team member for intelligent work routing. Include their expertise for better assignment matching.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "name": {"type": "string"},
                    "role": {"type": "string", "description": "e.g. 'Backend Engineer', 'PM', 'SRE'"},
                    "team": {"type": "string"},
                    "expertise": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Areas of expertise for routing (e.g. 'auth', 'database', 'frontend')",
                    },
                    "max_load": {"type": "integer", "description": "Maximum concurrent delegations (default 5)"},
                },
                "required": ["program_id", "name"],
            },
            "executor": register_team_member,
        },
        {
            "name": "get_team",
            "description": "Get the team roster with current load and capacity.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_team,
        },
        {
            "name": "delegate_task",
            "description": (
                "Create a delegation item — assign work to a team member with full context. "
                "Every delegation includes: what, who, why them, what done looks like, and when."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "DECISION", "INVESTIGATION", "EXECUTION", "REVIEW",
                            "COMMUNICATION", "BLOCKER_RESOLUTION", "RISK_MITIGATION",
                            "DEPENDENCY_CHASE", "DOCUMENTATION", "ESCALATION",
                        ],
                    },
                    "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3", "P4"]},
                    "assigned_to": {"type": "string"},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "source": {"type": "string", "description": "Where this work came from"},
                    "context": {"type": "string", "description": "Full context for the assignee"},
                    "success_criteria": {"type": "string", "description": "What 'done' looks like"},
                },
                "required": ["program_id", "title"],
            },
            "executor": delegate_task,
        },
        {
            "name": "delegate_from_meeting",
            "description": (
                "Auto-generate delegation items from meeting intelligence. "
                "Converts extracted commitments, risks, decisions, and dependencies "
                "into actionable work items with owners and deadlines."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "meeting_id": {"type": "string", "description": "The meeting intelligence ID (MI-xxxx)"},
                },
                "required": ["program_id", "meeting_id"],
            },
            "executor": delegate_from_meeting,
        },
        {
            "name": "delegate_from_risks",
            "description": "Generate delegation items for all unmitigated risks in the RAID log.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": delegate_from_risks,
        },
        {
            "name": "delegate_from_overdue",
            "description": "Generate escalation delegations for overdue action items.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": delegate_from_overdue,
        },
        {
            "name": "update_delegation_status",
            "description": "Update the status of a delegation item.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "delegation_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["PROPOSED", "ACCEPTED", "IN_PROGRESS", "BLOCKED", "COMPLETED", "REASSIGNED"],
                    },
                    "blockers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["program_id", "delegation_id", "status"],
            },
            "executor": update_delegation_status,
        },
        {
            "name": "reassign_delegation",
            "description": "Reassign a delegation item to a different person.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "delegation_id": {"type": "string"},
                    "new_assignee": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["program_id", "delegation_id", "new_assignee"],
            },
            "executor": reassign_delegation,
        },
        {
            "name": "get_delegation_summary",
            "description": "Get the full delegation plan summary with open, overdue, and blocked items.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_delegation_summary,
        },
        {
            "name": "get_person_delegations",
            "description": "Get all open delegations for a specific person.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "assignee": {"type": "string"},
                },
                "required": ["program_id", "assignee"],
            },
            "executor": get_person_delegations,
        },
    ]
