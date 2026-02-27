"""Task board tools — AI-native project tracking for the TPM agent.

Replaces external tools (Asana, etc.) with intelligent, built-in
task management that reasons about priorities and auto-extracts
tasks from Zoom AI meeting notes.
"""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.workflows.task_board_engine import TaskBoardEngine


def get_task_board_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    engine = TaskBoardEngine(store)

    def create_board_task(
        program_id: str,
        title: str,
        description: str = "",
        priority: str = "MEDIUM",
        assignee: str = "",
        due_date: str = "",
        effort_hours: float = 0.0,
        source: str = "MANUAL",
        source_id: str = "",
        tags: list[str] | None = None,
        sprint_id: str = "",
    ) -> str:
        task = engine.create_task(
            program_id, title, description, priority, assignee,
            due_date, effort_hours, source, source_id, tags=tags,
            sprint_id=sprint_id,
        )
        return json.dumps({
            "task_id": task.id,
            "title": task.title,
            "status": task.status.value,
            "priority": task.priority.value,
            "assignee": task.assignee,
        })

    def update_board_task(
        program_id: str,
        task_id: str,
        status: str = "",
        priority: str = "",
        assignee: str = "",
        due_date: str = "",
        sprint_id: str = "",
        effort_hours: float | None = None,
        blocked_by: list[str] | None = None,
    ) -> str:
        task = engine.update_task(
            program_id, task_id, status, priority, assignee,
            due_date, sprint_id, effort_hours, blocked_by,
        )
        if not task:
            return json.dumps({"error": f"Task {task_id} not found."})
        return json.dumps({
            "task_id": task.id,
            "status": task.status.value,
            "priority": task.priority.value,
            "assignee": task.assignee,
            "message": "Task updated.",
        })

    def extract_tasks_from_meeting(
        program_id: str,
        meeting_id: str = "",
        raw_notes: str = "",
        action_items: list[dict[str, str]] | None = None,
        key_decisions: list[str] | None = None,
        follow_ups: list[dict[str, str]] | None = None,
        meeting_topic: str = "",
    ) -> str:
        tasks = engine.extract_tasks_from_zoom_notes(
            program_id, meeting_id, raw_notes, action_items,
            key_decisions, follow_ups, meeting_topic,
        )
        return json.dumps({
            "tasks_created": len(tasks),
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "assignee": t.assignee,
                    "priority": t.priority.value,
                    "source": t.source.value,
                    "confidence": t.confidence,
                }
                for t in tasks
            ],
            "message": f"Extracted {len(tasks)} tasks from meeting notes.",
        })

    def reprioritize_tasks(program_id: str) -> str:
        changes = engine.reprioritize(program_id)
        if not changes:
            return json.dumps({"message": "No priority changes needed."})
        return json.dumps({
            "changes": changes,
            "message": f"Reprioritized {len(changes)} task(s).",
        })

    def get_task_board(program_id: str, sprint_id: str = "") -> str:
        return engine.get_board(program_id, sprint_id)

    def get_workload_analysis(program_id: str) -> str:
        workload = engine.get_workload(program_id)
        if not workload:
            return json.dumps({"message": "No tasks assigned yet."})

        lines = ["# Workload Analysis"]
        for person, data in workload.items():
            health_icon = {"healthy": "OK", "heavy": "HEAVY", "at_risk": "AT RISK", "overloaded": "OVERLOADED"}
            lines.append(
                f"\n## {person} [{health_icon.get(data['health'], '?')}]"
                f"\n  Tasks: {data['total_tasks']} | "
                f"Hours: {data['total_hours']:.0f} | "
                f"Overdue: {data['overdue_count']} | "
                f"Blocked: {data['blocked_count']}"
            )
            if data["by_priority"]:
                parts = [f"{p}: {c}" for p, c in sorted(data["by_priority"].items())]
                lines.append(f"  Priority breakdown: {', '.join(parts)}")
        return "\n".join(lines)

    def create_sprint(
        program_id: str,
        name: str,
        start_date: str,
        end_date: str,
        goal: str = "",
        team_capacity_hours: float = 0.0,
    ) -> str:
        sprint = engine.create_sprint(
            program_id, name, start_date, end_date, goal, team_capacity_hours,
        )
        return json.dumps({
            "sprint_id": sprint.id,
            "name": sprint.name,
            "start_date": sprint.start_date.isoformat(),
            "end_date": sprint.end_date.isoformat(),
        })

    def start_sprint(program_id: str, sprint_id: str) -> str:
        sprint = engine.start_sprint(program_id, sprint_id)
        if not sprint:
            return json.dumps({"error": "Sprint not found."})
        return json.dumps({
            "sprint_id": sprint.id,
            "name": sprint.name,
            "is_active": sprint.is_active,
            "message": f"Sprint '{sprint.name}' is now active.",
        })

    def complete_sprint(program_id: str, sprint_id: str) -> str:
        result = engine.complete_sprint(program_id, sprint_id)
        return json.dumps(result)

    def get_sprint_metrics(program_id: str, sprint_id: str = "") -> str:
        metrics = engine.get_sprint_metrics(program_id, sprint_id)
        return json.dumps(metrics)

    return [
        {
            "name": "create_board_task",
            "description": (
                "Create a task on the program's task board. "
                "Use for manually adding work items — for auto-extraction from "
                "meeting notes, use extract_tasks_from_meeting instead."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string", "description": "Task title"},
                    "description": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                    },
                    "assignee": {"type": "string"},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "effort_hours": {"type": "number", "description": "Estimated effort in hours"},
                    "source": {
                        "type": "string",
                        "enum": ["MANUAL", "RISK_MITIGATION", "DEPENDENCY", "SCOPE_CHANGE", "BUG", "ESCALATION"],
                    },
                    "source_id": {"type": "string", "description": "Reference ID (risk, bug, etc.)"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "sprint_id": {"type": "string", "description": "Sprint to assign to"},
                },
                "required": ["program_id", "title"],
            },
            "executor": create_board_task,
        },
        {
            "name": "update_board_task",
            "description": "Update a task's status, priority, assignee, or sprint assignment.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["BACKLOG", "TODO", "IN_PROGRESS", "IN_REVIEW", "BLOCKED", "DONE", "CANCELLED"],
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                    },
                    "assignee": {"type": "string"},
                    "due_date": {"type": "string"},
                    "sprint_id": {"type": "string"},
                    "effort_hours": {"type": "number"},
                    "blocked_by": {"type": "array", "items": {"type": "string"}, "description": "Task IDs blocking this"},
                },
                "required": ["program_id", "task_id"],
            },
            "executor": update_board_task,
        },
        {
            "name": "extract_tasks_from_meeting",
            "description": (
                "Auto-extract tasks from Zoom AI meeting notes. "
                "Parses action items, follow-ups, and detects implicit commitments. "
                "Creates tasks with full traceability back to the meeting. "
                "Use this after ingesting Zoom notes to populate the task board."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "meeting_id": {"type": "string", "description": "Zoom meeting ID"},
                    "raw_notes": {"type": "string", "description": "Raw meeting notes text"},
                    "action_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "owner": {"type": "string"},
                                "description": {"type": "string"},
                            },
                        },
                        "description": "Structured action items from AI notes",
                    },
                    "key_decisions": {"type": "array", "items": {"type": "string"}},
                    "follow_ups": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "person": {"type": "string"},
                                "action": {"type": "string"},
                            },
                        },
                    },
                    "meeting_topic": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": extract_tasks_from_meeting,
        },
        {
            "name": "reprioritize_tasks",
            "description": (
                "Re-evaluate all task priorities using AI reasoning. "
                "Considers due dates, blocker chains, sprint deadlines, and related risks. "
                "Run this periodically or before sprint planning."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": reprioritize_tasks,
        },
        {
            "name": "get_task_board",
            "description": "View the task board — shows all tasks grouped by status (kanban-style).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "sprint_id": {"type": "string", "description": "Filter to a specific sprint"},
                },
                "required": ["program_id"],
            },
            "executor": get_task_board,
        },
        {
            "name": "get_workload_analysis",
            "description": (
                "Analyze workload distribution across team members. "
                "Shows task counts, hours, overdue/blocked items, and health assessment."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": get_workload_analysis,
        },
        {
            "name": "create_sprint",
            "description": "Create a new sprint for time-boxed iteration planning.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "name": {"type": "string", "description": "Sprint name (e.g. 'Sprint 7')"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "goal": {"type": "string", "description": "Sprint goal"},
                    "team_capacity_hours": {"type": "number", "description": "Total available team hours"},
                },
                "required": ["program_id", "name", "start_date", "end_date"],
            },
            "executor": create_sprint,
        },
        {
            "name": "start_sprint",
            "description": "Activate a sprint — deactivates any previous active sprint.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "sprint_id": {"type": "string"},
                },
                "required": ["program_id", "sprint_id"],
            },
            "executor": start_sprint,
        },
        {
            "name": "complete_sprint",
            "description": (
                "Complete a sprint — returns summary with velocity and carry-over items. "
                "Incomplete tasks are moved back to backlog."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "sprint_id": {"type": "string"},
                },
                "required": ["program_id", "sprint_id"],
            },
            "executor": complete_sprint,
        },
        {
            "name": "get_sprint_metrics",
            "description": "Get sprint health metrics — completion rate, velocity, on-track status.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "sprint_id": {"type": "string", "description": "Sprint ID (uses active sprint if empty)"},
                },
                "required": ["program_id"],
            },
            "executor": get_sprint_metrics,
        },
    ]
