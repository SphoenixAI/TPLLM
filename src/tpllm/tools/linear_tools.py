"""Linear tools — issue tracking and team management."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from tpllm.config import TPLLMConfig
from tpllm.integrations.linear_integration import LinearIntegration


def get_linear_tools(config: TPLLMConfig) -> list[dict[str, Any]]:
    integration = LinearIntegration(config.linear)

    def linear_search_issues(
        team_key: str,
        state_filter: str = "started",
    ) -> str:
        issues = integration.search_issues(team_key, state_filter)
        return json.dumps({
            "count": len(issues),
            "issues": [asdict(i) for i in issues],
        })

    def linear_get_issue(issue_id: str) -> str:
        issue = integration.get_issue(issue_id)
        if not issue:
            return json.dumps({"error": f"Issue {issue_id} not found"})
        return json.dumps(asdict(issue))

    def linear_create_issue(
        team_id: str,
        title: str,
        description: str = "",
        priority: int = 0,
        assignee_id: str = "",
    ) -> str:
        issue = integration.create_issue(team_id, title, description, priority, assignee_id)
        return json.dumps(asdict(issue))

    def linear_update_issue(
        issue_id: str,
        title: str = "",
        state_id: str = "",
        priority: int | None = None,
        assignee_id: str = "",
    ) -> str:
        success = integration.update_issue(issue_id, title, state_id, priority, assignee_id)
        return json.dumps({"success": success, "issue_id": issue_id})

    def linear_list_teams() -> str:
        teams = integration.list_teams()
        return json.dumps({"teams": teams})

    return [
        {
            "name": "linear_search_issues",
            "description": "Search Linear issues by team key and state filter.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "team_key": {"type": "string", "description": "Team key (e.g. 'ENG')"},
                    "state_filter": {
                        "type": "string",
                        "description": "State type filter (started, completed, cancelled, etc.)",
                    },
                },
                "required": ["team_key"],
            },
            "executor": linear_search_issues,
        },
        {
            "name": "linear_get_issue",
            "description": "Get a single Linear issue by identifier (e.g. 'ENG-123').",
            "input_schema": {
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue identifier (e.g. 'ENG-123')"},
                },
                "required": ["issue_id"],
            },
            "executor": linear_get_issue,
        },
        {
            "name": "linear_create_issue",
            "description": "Create a new issue in Linear.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "team_id": {"type": "string", "description": "Linear team ID"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {
                        "type": "integer",
                        "description": "0=none, 1=urgent, 2=high, 3=medium, 4=low",
                    },
                    "assignee_id": {"type": "string", "description": "Linear user ID to assign to"},
                },
                "required": ["team_id", "title"],
            },
            "executor": linear_create_issue,
        },
        {
            "name": "linear_update_issue",
            "description": "Update an existing Linear issue (title, state, priority, assignee).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string"},
                    "title": {"type": "string"},
                    "state_id": {"type": "string", "description": "Linear workflow state ID"},
                    "priority": {"type": "integer"},
                    "assignee_id": {"type": "string"},
                },
                "required": ["issue_id"],
            },
            "executor": linear_update_issue,
        },
        {
            "name": "linear_list_teams",
            "description": "List all Linear teams in the workspace.",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "executor": linear_list_teams,
        },
    ]
