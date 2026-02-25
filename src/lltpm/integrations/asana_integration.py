"""Asana integration — project and task tracking for teams using Asana.

Connects to the Asana REST API for:
- Project listing and task search
- Task creation and updates
- Section/column management
- Assignee workload tracking
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from lltpm.integrations.base import Integration, IntegrationError


@dataclass
class AsanaTask:
    gid: str
    name: str
    assignee: str
    section: str
    due_date: str
    completed: bool
    tags: list[str]
    url: str
    notes: str = ""


@dataclass
class AsanaProject:
    gid: str
    name: str
    workspace_gid: str
    team: str
    url: str


class AsanaIntegration(Integration):
    """Asana integration for project and task management."""

    def name(self) -> str:
        return "Asana"

    def test_connection(self) -> bool:
        if not self.enabled:
            return False
        try:
            data = self._api_get("/users/me")
            return "gid" in data.get("data", {})
        except Exception:
            return False

    def list_projects(self, workspace_gid: str = "") -> list[AsanaProject]:
        """List projects in a workspace."""
        self._require_enabled()
        ws = workspace_gid or self._config.extra.get("workspace_gid", "")
        if not ws:
            raise IntegrationError("Asana workspace_gid is required.")
        try:
            data = self._api_get(
                "/projects",
                params={
                    "workspace": ws,
                    "opt_fields": "name,team.name,permalink_url",
                    "limit": "100",
                },
            )
            projects = []
            for item in data.get("data", []):
                projects.append(AsanaProject(
                    gid=item.get("gid", ""),
                    name=item.get("name", ""),
                    workspace_gid=ws,
                    team=item.get("team", {}).get("name", "") if item.get("team") else "",
                    url=item.get("permalink_url", ""),
                ))
            return projects
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Asana list projects failed: {e}") from e

    def search_tasks(
        self,
        project_gid: str = "",
        assignee: str = "",
        completed: bool | None = None,
        section_gid: str = "",
    ) -> list[AsanaTask]:
        """Search tasks with filters."""
        self._require_enabled()
        try:
            if section_gid:
                data = self._api_get(
                    f"/sections/{section_gid}/tasks",
                    params={
                        "opt_fields": "name,assignee.name,memberships.section.name,"
                        "due_on,completed,tags.name,permalink_url,notes",
                        "limit": "100",
                    },
                )
            elif project_gid:
                params: dict[str, str] = {
                    "opt_fields": "name,assignee.name,memberships.section.name,"
                    "due_on,completed,tags.name,permalink_url,notes",
                    "limit": "100",
                }
                if completed is not None:
                    params["completed_since"] = "now" if not completed else "2000-01-01"
                data = self._api_get(f"/projects/{project_gid}/tasks", params=params)
            else:
                # Search in workspace using assignee
                ws = self._config.extra.get("workspace_gid", "")
                if not ws:
                    raise IntegrationError("project_gid or workspace_gid required for task search.")
                params = {
                    "workspace": ws,
                    "opt_fields": "name,assignee.name,memberships.section.name,"
                    "due_on,completed,tags.name,permalink_url,notes",
                    "limit": "100",
                }
                if assignee:
                    params["assignee"] = assignee
                data = self._api_get("/tasks", params=params)

            tasks = []
            for item in data.get("data", []):
                section_name = ""
                for m in item.get("memberships", []):
                    if m.get("section"):
                        section_name = m["section"].get("name", "")
                        break

                tasks.append(AsanaTask(
                    gid=item.get("gid", ""),
                    name=item.get("name", ""),
                    assignee=(
                        item.get("assignee", {}).get("name", "")
                        if item.get("assignee") else ""
                    ),
                    section=section_name,
                    due_date=item.get("due_on", ""),
                    completed=item.get("completed", False),
                    tags=[t["name"] for t in item.get("tags", [])],
                    url=item.get("permalink_url", ""),
                    notes=item.get("notes", ""),
                ))

            # Apply client-side filters
            if assignee and project_gid:
                tasks = [t for t in tasks if assignee.lower() in t.assignee.lower()]
            if completed is not None and not section_gid:
                tasks = [t for t in tasks if t.completed == completed]

            return tasks
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Asana task search failed: {e}") from e

    def create_task(
        self,
        name: str,
        project_gid: str,
        assignee: str = "",
        due_date: str = "",
        notes: str = "",
        section_gid: str = "",
        tags: list[str] | None = None,
    ) -> AsanaTask:
        """Create a new task in a project."""
        self._require_enabled()
        try:
            body: dict[str, Any] = {
                "name": name,
                "projects": [project_gid],
            }
            if assignee:
                body["assignee"] = assignee
            if due_date:
                body["due_on"] = due_date
            if notes:
                body["notes"] = notes
            if section_gid:
                body["memberships"] = [{"project": project_gid, "section": section_gid}]

            data = self._api_post("/tasks", body={"data": body})
            item = data.get("data", {})
            return AsanaTask(
                gid=item.get("gid", ""),
                name=item.get("name", name),
                assignee=assignee,
                section="",
                due_date=due_date,
                completed=False,
                tags=tags or [],
                url=item.get("permalink_url", ""),
                notes=notes,
            )
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Asana create task failed: {e}") from e

    def update_task(
        self,
        task_gid: str,
        name: str = "",
        assignee: str = "",
        due_date: str = "",
        completed: bool | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Update an existing task."""
        self._require_enabled()
        try:
            body: dict[str, Any] = {}
            if name:
                body["name"] = name
            if assignee:
                body["assignee"] = assignee
            if due_date:
                body["due_on"] = due_date
            if completed is not None:
                body["completed"] = completed
            if notes:
                body["notes"] = notes

            if not body:
                return {"message": "No fields to update."}

            data = self._api_put(f"/tasks/{task_gid}", body={"data": body})
            return data.get("data", {})
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Asana update task failed: {e}") from e

    def get_task_count_by_section(self, project_gid: str) -> dict[str, int]:
        """Get task counts grouped by section — useful for sprint board overview."""
        self._require_enabled()
        try:
            sections_data = self._api_get(f"/projects/{project_gid}/sections")
            counts: dict[str, int] = {}
            for section in sections_data.get("data", []):
                name = section.get("name", "No Section")
                tasks = self._api_get(
                    f"/sections/{section['gid']}/tasks",
                    params={"limit": "100", "opt_fields": "completed"},
                )
                incomplete = [t for t in tasks.get("data", []) if not t.get("completed")]
                counts[name] = len(incomplete)
            return counts
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Asana section counts failed: {e}") from e

    # ── HTTP helpers ────────────────────────────────────────────

    def _api_get(self, path: str, params: dict[str, str] | None = None) -> dict:
        import urllib.request
        import urllib.parse

        url = f"https://app.asana.com/api/1.0{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _api_post(self, path: str, body: dict | None = None) -> dict:
        import urllib.request

        url = f"https://app.asana.com/api/1.0{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body or {}).encode(),
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _api_put(self, path: str, body: dict | None = None) -> dict:
        import urllib.request

        url = f"https://app.asana.com/api/1.0{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body or {}).encode(),
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="PUT",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Asana integration is not configured.")
