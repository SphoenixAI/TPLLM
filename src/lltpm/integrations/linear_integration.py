"""Linear integration — issue tracking for teams using Linear."""

from __future__ import annotations

from dataclasses import dataclass

from lltpm.integrations.base import Integration, IntegrationError


@dataclass
class LinearIssue:
    id: str
    title: str
    state: str
    assignee: str
    priority: int  # 0=none, 1=urgent, 2=high, 3=medium, 4=low
    labels: list[str]
    url: str


class LinearIntegration(Integration):
    """Linear integration for issue tracking."""

    def name(self) -> str:
        return "Linear"

    def test_connection(self) -> bool:
        if not self.enabled:
            return False
        try:
            import urllib.request
            import json
            req = urllib.request.Request(
                "https://api.linear.app/graphql",
                data=json.dumps({"query": "{ viewer { id } }"}).encode(),
                headers={
                    "Authorization": self._config.api_key,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req) as resp:
                return resp.status == 200
        except Exception:
            return False

    def search_issues(self, team_key: str, state_filter: str = "started") -> list[LinearIssue]:
        """Search issues by team and state."""
        self._require_enabled()
        query = """
        query($teamKey: String!, $filter: IssueFilter) {
            team(key: $teamKey) {
                issues(filter: $filter, first: 50) {
                    nodes {
                        id identifier title
                        state { name }
                        assignee { name }
                        priority
                        labels { nodes { name } }
                        url
                    }
                }
            }
        }
        """
        variables = {"teamKey": team_key}
        if state_filter:
            variables["filter"] = {"state": {"type": {"eq": state_filter}}}
        try:
            data = self._graphql(query, variables)
            issues = []
            for node in data.get("data", {}).get("team", {}).get("issues", {}).get("nodes", []):
                issues.append(LinearIssue(
                    id=node.get("identifier", node.get("id", "")),
                    title=node.get("title", ""),
                    state=node.get("state", {}).get("name", ""),
                    assignee=node.get("assignee", {}).get("name", "") if node.get("assignee") else "",
                    priority=node.get("priority", 0),
                    labels=[l["name"] for l in node.get("labels", {}).get("nodes", [])],
                    url=node.get("url", ""),
                ))
            return issues
        except Exception as e:
            raise IntegrationError(f"Linear search failed: {e}") from e

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        import json
        import urllib.request
        body = {"query": query}
        if variables:
            body["variables"] = variables
        req = urllib.request.Request(
            "https://api.linear.app/graphql",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": self._config.api_key,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Linear integration is not configured.")
