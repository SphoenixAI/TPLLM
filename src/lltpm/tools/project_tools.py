"""Project management tools — create/read programs, milestones, RAID items, decisions."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.artifacts.program_brief import ProgramBriefGenerator
from lltpm.artifacts.raid_log import RAIDLogManager
from lltpm.artifacts.decision_log import DecisionLogManager
from lltpm.artifacts.milestone_plan import MilestonePlanManager
from lltpm.artifacts.launch_checklist import LaunchChecklistManager
from lltpm.artifacts.meeting_notes import MeetingNotesManager
from lltpm.artifacts.status_update import StatusUpdateGenerator


def get_project_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    briefs = ProgramBriefGenerator(store)
    raid = RAIDLogManager(store)
    decisions = DecisionLogManager(store)
    milestones = MilestonePlanManager(store)
    launch = LaunchChecklistManager(store)
    meetings = MeetingNotesManager(store)
    status = StatusUpdateGenerator(store)

    return [
        # ── Program Brief ──────────────────────────────────────
        {
            "name": "create_program",
            "description": "Create a new program with its brief (one-pager). Use this when starting a new program.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string", "description": "Unique short ID for the program (e.g. 'atlas-v2')"},
                    "name": {"type": "string", "description": "Human-readable program name"},
                    "goal": {"type": "string", "description": "One-sentence goal of the program"},
                    "non_goals": {"type": "array", "items": {"type": "string"}, "description": "Explicit non-goals"},
                    "target_date": {"type": "string", "description": "Target completion date (YYYY-MM-DD)"},
                    "stakeholders": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "role": {"type": "string"},
                                "team": {"type": "string"},
                            },
                            "required": ["name", "role"],
                        },
                    },
                    "success_metrics": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "target": {"type": "string"},
                            },
                            "required": ["name", "target"],
                        },
                    },
                },
                "required": ["program_id", "name", "goal"],
            },
            "executor": lambda **kw: json.dumps(briefs.create_program(**kw).model_dump(), default=str),
        },
        {
            "name": "get_program_brief",
            "description": "Get the program brief (one-pager) for a program.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": lambda program_id: briefs.get_brief(program_id),
        },
        {
            "name": "list_programs",
            "description": "List all programs being tracked.",
            "input_schema": {"type": "object", "properties": {}},
            "executor": lambda: json.dumps(store.list_programs()),
        },
        # ── RAID Log ───────────────────────────────────────────
        {
            "name": "add_risk",
            "description": "Add a risk to the RAID log.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
                    "impact": {"type": "string"},
                    "mitigation": {"type": "string"},
                    "owner": {"type": "string"},
                },
                "required": ["program_id", "title"],
            },
            "executor": lambda **kw: json.dumps(raid.add_risk(**kw).model_dump(), default=str),
        },
        {
            "name": "add_issue",
            "description": "Add an issue to the RAID log.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
                    "impact": {"type": "string"},
                    "owner": {"type": "string"},
                },
                "required": ["program_id", "title"],
            },
            "executor": lambda **kw: json.dumps(raid.add_issue(**kw).model_dump(), default=str),
        },
        {
            "name": "add_dependency",
            "description": "Add a dependency to the RAID log.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string"},
                    "upstream_team": {"type": "string"},
                    "downstream_team": {"type": "string"},
                    "owner": {"type": "string"},
                    "commitment_date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["program_id", "title"],
            },
            "executor": lambda **kw: json.dumps(raid.add_dependency(**kw).model_dump(), default=str),
        },
        {
            "name": "get_raid_summary",
            "description": "Get the RAID log summary for a program.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": lambda program_id: raid.get_summary(program_id),
        },
        # ── Decision Log ───────────────────────────────────────
        {
            "name": "propose_decision",
            "description": "Propose a decision that needs to be made. Document options and tradeoffs.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string"},
                    "context": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "pros": {"type": "array", "items": {"type": "string"}},
                                "cons": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["label"],
                        },
                    },
                },
                "required": ["program_id", "title"],
            },
            "executor": lambda **kw: json.dumps(decisions.propose_decision(**kw).model_dump(), default=str),
        },
        {
            "name": "record_decision",
            "description": "Record that a decision has been made.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "decision_id": {"type": "string"},
                    "decision_text": {"type": "string"},
                    "decided_by": {"type": "string"},
                    "rationale": {"type": "string"},
                    "follow_ups": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["program_id", "decision_id", "decision_text", "decided_by"],
            },
            "executor": lambda **kw: json.dumps({"success": decisions.record_decision(**kw)}),
        },
        {
            "name": "get_decision_summary",
            "description": "Get the decision log summary for a program.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": lambda program_id: decisions.get_summary(program_id),
        },
        # ── Milestones ─────────────────────────────────────────
        {
            "name": "add_milestone",
            "description": "Add a milestone to the program plan.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "phase": {"type": "string"},
                    "target_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "owner": {"type": "string"},
                    "on_critical_path": {"type": "boolean"},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["program_id", "name"],
            },
            "executor": lambda **kw: json.dumps(milestones.add_milestone(**kw).model_dump(), default=str),
        },
        {
            "name": "update_milestone_status",
            "description": "Update the status of a milestone.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "milestone_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["NOT_STARTED", "IN_PROGRESS", "AT_RISK", "COMPLETED", "MISSED"]},
                },
                "required": ["program_id", "milestone_id", "status"],
            },
            "executor": lambda **kw: json.dumps({"success": milestones.update_milestone_status(**kw)}),
        },
        {
            "name": "get_milestone_summary",
            "description": "Get the milestone plan summary for a program.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": lambda program_id: milestones.get_summary(program_id),
        },
        # ── Launch Checklist ───────────────────────────────────
        {
            "name": "init_launch_checklist",
            "description": "Initialize the launch readiness checklist with default gates.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": lambda program_id: json.dumps(launch.initialize(program_id).model_dump(), default=str),
        },
        {
            "name": "get_launch_readiness",
            "description": "Get the launch readiness summary.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": lambda program_id: launch.get_summary(program_id),
        },
        # ── Action Items ──────────────────────────────────────
        {
            "name": "add_action_item",
            "description": "Add an action item with an owner and deadline.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "description": {"type": "string"},
                    "owner": {"type": "string"},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "source": {"type": "string", "description": "Where this action came from"},
                },
                "required": ["program_id", "description", "owner"],
            },
            "executor": lambda **kw: json.dumps(meetings.add_action_item(**kw).model_dump(), default=str),
        },
        {
            "name": "complete_action_item",
            "description": "Mark an action item as done.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "item_id": {"type": "string"},
                },
                "required": ["program_id", "item_id"],
            },
            "executor": lambda **kw: json.dumps({"success": meetings.complete_action_item(**kw)}),
        },
        {
            "name": "get_action_summary",
            "description": "Get open and overdue action items.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": lambda program_id: meetings.get_action_summary(program_id),
        },
        # ── Status Updates ─────────────────────────────────────
        {
            "name": "create_status_update",
            "description": "Create a structured status update for a program.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "program_name": {"type": "string"},
                    "overall_health": {"type": "string", "enum": ["GREEN", "YELLOW", "RED"]},
                    "progress": {"type": "array", "items": {"type": "string"}},
                    "top_risks": {"type": "array", "items": {"type": "string"}},
                    "asks": {"type": "array", "items": {"type": "string"}},
                    "upcoming": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["program_id", "program_name", "overall_health"],
            },
            "executor": lambda **kw: status.create(**kw).render(),
        },
        {
            "name": "get_latest_status",
            "description": "Get the most recent status update for a program.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": lambda program_id: status.render_latest(program_id),
        },
    ]
