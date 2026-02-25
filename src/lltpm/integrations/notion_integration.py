"""Notion integration — database queries and page management.

Connects to the Notion API for:
- Database querying (project boards, task trackers, wikis)
- Page creation and updates
- Status tracking across databases
- Content search
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from lltpm.integrations.base import Integration, IntegrationError


@dataclass
class NotionPage:
    id: str
    title: str
    url: str
    status: str
    assignee: str
    due_date: str
    tags: list[str]
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotionDatabase:
    id: str
    title: str
    url: str
    property_names: list[str]


class NotionIntegration(Integration):
    """Notion integration for database and page management."""

    API_BASE = "https://api.notion.com/v1"
    API_VERSION = "2022-06-28"

    def name(self) -> str:
        return "Notion"

    def test_connection(self) -> bool:
        if not self.enabled:
            return False
        try:
            data = self._api_get("/users/me")
            return "id" in data
        except Exception:
            return False

    def list_databases(self) -> list[NotionDatabase]:
        """List databases the integration has access to."""
        self._require_enabled()
        try:
            data = self._api_post("/search", body={
                "filter": {"value": "database", "property": "object"},
                "page_size": 100,
            })
            databases = []
            for item in data.get("results", []):
                title_parts = item.get("title", [])
                title = title_parts[0].get("plain_text", "") if title_parts else ""
                prop_names = list(item.get("properties", {}).keys())
                databases.append(NotionDatabase(
                    id=item.get("id", ""),
                    title=title,
                    url=item.get("url", ""),
                    property_names=prop_names,
                ))
            return databases
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Notion list databases failed: {e}") from e

    def query_database(
        self,
        database_id: str,
        status_filter: str = "",
        assignee_filter: str = "",
        sort_by: str = "",
        sort_direction: str = "descending",
    ) -> list[NotionPage]:
        """Query a Notion database with optional filters."""
        self._require_enabled()
        try:
            body: dict[str, Any] = {"page_size": 100}

            # Build filter
            filters: list[dict] = []
            if status_filter:
                filters.append({
                    "property": "Status",
                    "status": {"equals": status_filter},
                })
            if assignee_filter:
                filters.append({
                    "property": "Assignee",
                    "people": {"contains": assignee_filter},
                })
            if len(filters) == 1:
                body["filter"] = filters[0]
            elif len(filters) > 1:
                body["filter"] = {"and": filters}

            # Sort
            if sort_by:
                body["sorts"] = [{"property": sort_by, "direction": sort_direction}]

            data = self._api_post(f"/databases/{database_id}/query", body=body)
            return [self._parse_page(item) for item in data.get("results", [])]
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Notion query failed: {e}") from e

    def create_page(
        self,
        database_id: str,
        title: str,
        status: str = "",
        assignee: str = "",
        due_date: str = "",
        content: str = "",
        extra_properties: dict[str, Any] | None = None,
    ) -> NotionPage:
        """Create a page in a Notion database."""
        self._require_enabled()
        try:
            properties: dict[str, Any] = {
                "Name": {"title": [{"text": {"content": title}}]},
            }
            if status:
                properties["Status"] = {"status": {"name": status}}
            if due_date:
                properties["Due Date"] = {"date": {"start": due_date}}
            if extra_properties:
                properties.update(extra_properties)

            body: dict[str, Any] = {
                "parent": {"database_id": database_id},
                "properties": properties,
            }

            # Add content blocks if provided
            if content:
                body["children"] = [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": content}}],
                        },
                    }
                ]

            data = self._api_post("/pages", body=body)
            return self._parse_page(data)
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Notion create page failed: {e}") from e

    def update_page(
        self,
        page_id: str,
        status: str = "",
        assignee: str = "",
        due_date: str = "",
        extra_properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update properties on a Notion page."""
        self._require_enabled()
        try:
            properties: dict[str, Any] = {}
            if status:
                properties["Status"] = {"status": {"name": status}}
            if due_date:
                properties["Due Date"] = {"date": {"start": due_date}}
            if extra_properties:
                properties.update(extra_properties)

            if not properties:
                return {"message": "No fields to update."}

            data = self._api_patch(f"/pages/{page_id}", body={"properties": properties})
            return {"id": data.get("id", ""), "url": data.get("url", "")}
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Notion update page failed: {e}") from e

    def search(self, query: str) -> list[NotionPage]:
        """Full-text search across Notion."""
        self._require_enabled()
        try:
            data = self._api_post("/search", body={
                "query": query,
                "filter": {"value": "page", "property": "object"},
                "page_size": 20,
            })
            return [self._parse_page(item) for item in data.get("results", [])]
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Notion search failed: {e}") from e

    def get_database_summary(self, database_id: str) -> dict[str, Any]:
        """Get a summary of statuses in a database — useful for board overview."""
        self._require_enabled()
        try:
            pages = self.query_database(database_id)
            status_counts: dict[str, int] = {}
            assignee_counts: dict[str, int] = {}
            for page in pages:
                s = page.status or "No Status"
                status_counts[s] = status_counts.get(s, 0) + 1
                if page.assignee:
                    assignee_counts[page.assignee] = assignee_counts.get(page.assignee, 0) + 1
            return {
                "total_pages": len(pages),
                "by_status": status_counts,
                "by_assignee": assignee_counts,
            }
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Notion summary failed: {e}") from e

    # ── Page parsing ─────────────────────────────────────────

    def _parse_page(self, item: dict) -> NotionPage:
        props = item.get("properties", {})

        # Extract title
        title = ""
        for key in ("Name", "Title", "name", "title"):
            if key in props and props[key].get("type") == "title":
                parts = props[key].get("title", [])
                title = parts[0].get("plain_text", "") if parts else ""
                break

        # Extract status
        status = ""
        if "Status" in props:
            status_prop = props["Status"]
            if status_prop.get("type") == "status" and status_prop.get("status"):
                status = status_prop["status"].get("name", "")
            elif status_prop.get("type") == "select" and status_prop.get("select"):
                status = status_prop["select"].get("name", "")

        # Extract assignee
        assignee = ""
        for key in ("Assignee", "Assign", "Owner", "Person"):
            if key in props and props[key].get("type") == "people":
                people = props[key].get("people", [])
                if people:
                    assignee = people[0].get("name", "")
                break

        # Extract due date
        due_date = ""
        for key in ("Due Date", "Due", "Deadline", "Date"):
            if key in props and props[key].get("type") == "date":
                date_obj = props[key].get("date")
                if date_obj:
                    due_date = date_obj.get("start", "")
                break

        # Extract tags
        tags = []
        for key in ("Tags", "Labels", "Category"):
            if key in props and props[key].get("type") == "multi_select":
                tags = [o.get("name", "") for o in props[key].get("multi_select", [])]
                break

        return NotionPage(
            id=item.get("id", ""),
            title=title,
            url=item.get("url", ""),
            status=status,
            assignee=assignee,
            due_date=due_date,
            tags=tags,
            properties={k: v.get("type", "") for k, v in props.items()},
        )

    # ── HTTP helpers ────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.api_key}",
            "Notion-Version": self.API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _api_get(self, path: str) -> dict:
        import urllib.request

        url = f"{self.API_BASE}{path}"
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _api_post(self, path: str, body: dict | None = None) -> dict:
        import urllib.request

        url = f"{self.API_BASE}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body or {}).encode(),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _api_patch(self, path: str, body: dict | None = None) -> dict:
        import urllib.request

        url = f"{self.API_BASE}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body or {}).encode(),
            headers=self._headers(),
            method="PATCH",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Notion integration is not configured.")
