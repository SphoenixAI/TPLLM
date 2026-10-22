"""Jira integration — ticket tracking, sprint status, epic progress."""

from __future__ import annotations

from dataclasses import dataclass

from tpllm.integrations.base import Integration, IntegrationError


@dataclass
class JiraTicket:
    key: str
    summary: str
    status: str
    assignee: str
    priority: str
    labels: list[str]
    epic: str
    url: str


@dataclass
class SprintSummary:
    name: str
    state: str
    total_issues: int
    completed: int
    in_progress: int
    remaining: int


class JiraIntegration(Integration):
    """Jira integration for project tracking visibility."""

    def name(self) -> str:
        return "Jira"

    def test_connection(self) -> bool:
        if not self.enabled:
            return False
        try:
            client = self._get_client()
            client.myself()
            return True
        except Exception:
            return False

    def search_issues(self, jql: str, max_results: int = 50) -> list[JiraTicket]:
        self._require_enabled()
        try:
            client = self._get_client()
            issues = client.search_issues(jql, maxResults=max_results)
            return [self._to_ticket(issue) for issue in issues]
        except Exception as e:
            raise IntegrationError(f"Jira search failed: {e}") from e

    def get_sprint_summary(self, board_id: int) -> SprintSummary | None:
        self._require_enabled()
        try:
            client = self._get_client()
            sprints = client.sprints(board_id, state="active")
            if not sprints:
                return None
            sprint = sprints[0]
            issues = client.search_issues(f"sprint = {sprint.id}")
            completed = sum(1 for i in issues if str(i.fields.status) == "Done")
            in_progress = sum(1 for i in issues if str(i.fields.status) == "In Progress")
            return SprintSummary(
                name=sprint.name,
                state=sprint.state,
                total_issues=len(issues),
                completed=completed,
                in_progress=in_progress,
                remaining=len(issues) - completed - in_progress,
            )
        except Exception as e:
            raise IntegrationError(f"Jira sprint query failed: {e}") from e

    def _get_client(self):
        from jira import JIRA
        return JIRA(
            server=self._config.base_url,
            basic_auth=(self._config.extra.get("email", ""), self._config.api_key),
        )

    def _to_ticket(self, issue) -> JiraTicket:
        return JiraTicket(
            key=issue.key,
            summary=issue.fields.summary,
            status=str(issue.fields.status),
            assignee=str(issue.fields.assignee) if issue.fields.assignee else "Unassigned",
            priority=str(issue.fields.priority) if issue.fields.priority else "None",
            labels=issue.fields.labels,
            epic=getattr(issue.fields, "epic", {}).get("name", "") if hasattr(issue.fields, "epic") else "",
            url=issue.permalink(),
        )

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Jira integration is not configured.")
