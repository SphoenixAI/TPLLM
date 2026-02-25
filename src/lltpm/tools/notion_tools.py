"""Notion tools — database queries and page management via Notion API."""

from __future__ import annotations

import json
from typing import Any

from lltpm.config import LLTPMConfig
from lltpm.integrations.notion_integration import NotionIntegration


def get_notion_tools(config: LLTPMConfig) -> list[dict[str, Any]]:
    notion = NotionIntegration(config.notion)

    def notion_list_databases() -> str:
        try:
            databases = notion.list_databases()
            return json.dumps({
                "databases": [
                    {
                        "id": db.id,
                        "title": db.title,
                        "url": db.url,
                        "properties": db.property_names,
                    }
                    for db in databases
                ]
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    def notion_query_database(
        database_id: str,
        status_filter: str = "",
        assignee_filter: str = "",
        sort_by: str = "",
        sort_direction: str = "descending",
    ) -> str:
        try:
            pages = notion.query_database(
                database_id, status_filter, assignee_filter, sort_by, sort_direction
            )
            return json.dumps({
                "pages": [
                    {
                        "id": p.id,
                        "title": p.title,
                        "status": p.status,
                        "assignee": p.assignee,
                        "due_date": p.due_date,
                        "tags": p.tags,
                        "url": p.url,
                    }
                    for p in pages
                ]
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    def notion_create_page(
        database_id: str,
        title: str,
        status: str = "",
        due_date: str = "",
        content: str = "",
    ) -> str:
        try:
            page = notion.create_page(
                database_id, title, status=status, due_date=due_date, content=content
            )
            return json.dumps({
                "id": page.id,
                "title": page.title,
                "url": page.url,
                "message": f"Page '{title}' created.",
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    def notion_update_page(
        page_id: str,
        status: str = "",
        due_date: str = "",
    ) -> str:
        try:
            result = notion.update_page(page_id, status=status, due_date=due_date)
            return json.dumps({"result": result, "message": "Page updated."})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def notion_search(query: str) -> str:
        try:
            pages = notion.search(query)
            return json.dumps({
                "results": [
                    {
                        "id": p.id,
                        "title": p.title,
                        "status": p.status,
                        "url": p.url,
                    }
                    for p in pages
                ]
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    def notion_database_summary(database_id: str) -> str:
        try:
            summary = notion.get_database_summary(database_id)
            lines = [f"Notion Database Summary ({summary['total_pages']} pages):"]
            lines.append("\nBy Status:")
            for status, count in summary["by_status"].items():
                lines.append(f"  {status}: {count}")
            if summary["by_assignee"]:
                lines.append("\nBy Assignee:")
                for assignee, count in summary["by_assignee"].items():
                    lines.append(f"  {assignee}: {count}")
            return "\n".join(lines)
        except Exception as e:
            return json.dumps({"error": str(e)})

    return [
        {
            "name": "notion_list_databases",
            "description": "List Notion databases the integration can access.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
            "executor": notion_list_databases,
        },
        {
            "name": "notion_query_database",
            "description": (
                "Query a Notion database with optional filters for status, assignee, "
                "and sorting. Returns pages with their properties."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "database_id": {"type": "string", "description": "Notion database ID"},
                    "status_filter": {"type": "string", "description": "Filter by Status property"},
                    "assignee_filter": {"type": "string", "description": "Filter by Assignee"},
                    "sort_by": {"type": "string", "description": "Property name to sort by"},
                    "sort_direction": {
                        "type": "string",
                        "enum": ["ascending", "descending"],
                    },
                },
                "required": ["database_id"],
            },
            "executor": notion_query_database,
        },
        {
            "name": "notion_create_page",
            "description": "Create a new page in a Notion database.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "database_id": {"type": "string", "description": "Database to add page to"},
                    "title": {"type": "string", "description": "Page title"},
                    "status": {"type": "string", "description": "Status value"},
                    "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)"},
                    "content": {"type": "string", "description": "Page body content"},
                },
                "required": ["database_id", "title"],
            },
            "executor": notion_create_page,
        },
        {
            "name": "notion_update_page",
            "description": "Update properties on a Notion page.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string", "description": "Notion page ID"},
                    "status": {"type": "string", "description": "New status value"},
                    "due_date": {"type": "string", "description": "New due date (YYYY-MM-DD)"},
                },
                "required": ["page_id"],
            },
            "executor": notion_update_page,
        },
        {
            "name": "notion_search",
            "description": "Full-text search across Notion pages.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
            "executor": notion_search,
        },
        {
            "name": "notion_database_summary",
            "description": "Get a summary of a Notion database: task counts by status and assignee.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "database_id": {"type": "string", "description": "Notion database ID"},
                },
                "required": ["database_id"],
            },
            "executor": notion_database_summary,
        },
    ]
