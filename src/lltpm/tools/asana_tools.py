"""Asana tools — project and task management via Asana API."""

from __future__ import annotations

import json
from typing import Any

from lltpm.config import LLTPMConfig
from lltpm.integrations.asana_integration import AsanaIntegration


def get_asana_tools(config: LLTPMConfig) -> list[dict[str, Any]]:
    asana = AsanaIntegration(config.asana)

    def asana_list_projects(workspace_gid: str = "") -> str:
        try:
            projects = asana.list_projects(workspace_gid)
            return json.dumps({
                "projects": [
                    {"gid": p.gid, "name": p.name, "team": p.team, "url": p.url}
                    for p in projects
                ]
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    def asana_search_tasks(
        project_gid: str = "",
        assignee: str = "",
        completed: bool | None = None,
        section_gid: str = "",
    ) -> str:
        try:
            tasks = asana.search_tasks(project_gid, assignee, completed, section_gid)
            return json.dumps({
                "tasks": [
                    {
                        "gid": t.gid,
                        "name": t.name,
                        "assignee": t.assignee,
                        "section": t.section,
                        "due_date": t.due_date,
                        "completed": t.completed,
                        "tags": t.tags,
                        "url": t.url,
                    }
                    for t in tasks
                ]
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    def asana_create_task(
        name: str,
        project_gid: str,
        assignee: str = "",
        due_date: str = "",
        notes: str = "",
        section_gid: str = "",
    ) -> str:
        try:
            task = asana.create_task(
                name, project_gid, assignee, due_date, notes, section_gid
            )
            return json.dumps({
                "gid": task.gid,
                "name": task.name,
                "url": task.url,
                "message": f"Task '{name}' created successfully.",
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    def asana_update_task(
        task_gid: str,
        name: str = "",
        assignee: str = "",
        due_date: str = "",
        completed: bool | None = None,
        notes: str = "",
    ) -> str:
        try:
            result = asana.update_task(task_gid, name, assignee, due_date, completed, notes)
            return json.dumps({"result": result, "message": "Task updated."})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def asana_board_summary(project_gid: str) -> str:
        try:
            counts = asana.get_task_count_by_section(project_gid)
            total = sum(counts.values())
            lines = [f"Asana Board Summary ({total} open tasks):"]
            for section, count in counts.items():
                lines.append(f"  {section}: {count}")
            return "\n".join(lines)
        except Exception as e:
            return json.dumps({"error": str(e)})

    return [
        {
            "name": "asana_list_projects",
            "description": "List Asana projects in a workspace.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "workspace_gid": {
                        "type": "string",
                        "description": "Asana workspace GID (uses default if empty)",
                    },
                },
                "required": [],
            },
            "executor": asana_list_projects,
        },
        {
            "name": "asana_search_tasks",
            "description": (
                "Search Asana tasks by project, assignee, completion status, or section."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_gid": {"type": "string", "description": "Asana project GID"},
                    "assignee": {"type": "string", "description": "Filter by assignee name"},
                    "completed": {"type": "boolean", "description": "Filter by completion status"},
                    "section_gid": {"type": "string", "description": "Filter by section GID"},
                },
                "required": [],
            },
            "executor": asana_search_tasks,
        },
        {
            "name": "asana_create_task",
            "description": "Create a new task in an Asana project.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Task name"},
                    "project_gid": {"type": "string", "description": "Project to add task to"},
                    "assignee": {"type": "string", "description": "Assignee email or GID"},
                    "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)"},
                    "notes": {"type": "string", "description": "Task description"},
                    "section_gid": {"type": "string", "description": "Section to place task in"},
                },
                "required": ["name", "project_gid"],
            },
            "executor": asana_create_task,
        },
        {
            "name": "asana_update_task",
            "description": "Update an existing Asana task (name, assignee, due date, completion, notes).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_gid": {"type": "string", "description": "Asana task GID"},
                    "name": {"type": "string"},
                    "assignee": {"type": "string"},
                    "due_date": {"type": "string"},
                    "completed": {"type": "boolean"},
                    "notes": {"type": "string"},
                },
                "required": ["task_gid"],
            },
            "executor": asana_update_task,
        },
        {
            "name": "asana_board_summary",
            "description": "Get task counts by section for an Asana project (sprint board overview).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_gid": {"type": "string", "description": "Asana project GID"},
                },
                "required": ["project_gid"],
            },
            "executor": asana_board_summary,
        },
    ]
