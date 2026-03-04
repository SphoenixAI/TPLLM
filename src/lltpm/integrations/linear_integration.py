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

    def get_issue(self, issue_id: str) -> LinearIssue | None:
        """Get a single issue by ID or identifier (e.g. 'ENG-123')."""
        self._require_enabled()
        query = """
        query($id: String!) {
            issue(id: $id) {
                id identifier title
                state { name }
                assignee { name }
                priority
                labels { nodes { name } }
                url
            }
        }
        """
        try:
            data = self._graphql(query, {"id": issue_id})
            node = data.get("data", {}).get("issue")
            if not node:
                return None
            return self._parse_issue(node)
        except Exception as e:
            raise IntegrationError(f"Linear get issue failed: {e}") from e

    def create_issue(
        self,
        team_id: str,
        title: str,
        description: str = "",
        priority: int = 0,
        assignee_id: str = "",
    ) -> LinearIssue:
        """Create a new issue in Linear."""
        self._require_enabled()
        mutation = """
        mutation($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue {
                    id identifier title
                    state { name }
                    assignee { name }
                    priority
                    labels { nodes { name } }
                    url
                }
            }
        }
        """
        input_data: dict = {
            "teamId": team_id,
            "title": title,
            "description": description,
            "priority": priority,
        }
        if assignee_id:
            input_data["assigneeId"] = assignee_id

        try:
            data = self._graphql(mutation, {"input": input_data})
            result = data.get("data", {}).get("issueCreate", {})
            if not result.get("success"):
                raise IntegrationError("Linear issue creation failed")
            return self._parse_issue(result.get("issue", {}))
        except IntegrationError:
            raise
        except Exception as e:
            raise IntegrationError(f"Linear create issue failed: {e}") from e

    def update_issue(
        self,
        issue_id: str,
        title: str = "",
        state_id: str = "",
        priority: int | None = None,
        assignee_id: str = "",
    ) -> bool:
        """Update an existing issue."""
        self._require_enabled()
        mutation = """
        mutation($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) { success }
        }
        """
        input_data: dict = {}
        if title:
            input_data["title"] = title
        if state_id:
            input_data["stateId"] = state_id
        if priority is not None:
            input_data["priority"] = priority
        if assignee_id:
            input_data["assigneeId"] = assignee_id
        if not input_data:
            return True
        try:
            data = self._graphql(mutation, {"id": issue_id, "input": input_data})
            return data.get("data", {}).get("issueUpdate", {}).get("success", False)
        except Exception as e:
            raise IntegrationError(f"Linear update issue failed: {e}") from e

    def list_teams(self) -> list[dict[str, str]]:
        """List all teams in the workspace."""
        self._require_enabled()
        query = "query { teams { nodes { id name key } } }"
        try:
            data = self._graphql(query)
            nodes = data.get("data", {}).get("teams", {}).get("nodes", [])
            return [{"id": n["id"], "name": n["name"], "key": n["key"]} for n in nodes]
        except Exception as e:
            raise IntegrationError(f"Linear list teams failed: {e}") from e

    def _parse_issue(self, node: dict) -> LinearIssue:
        return LinearIssue(
            id=node.get("identifier", node.get("id", "")),
            title=node.get("title", ""),
            state=node.get("state", {}).get("name", ""),
            assignee=node.get("assignee", {}).get("name", "") if node.get("assignee") else "",
            priority=node.get("priority", 0),
            labels=[l["name"] for l in node.get("labels", {}).get("nodes", [])],
            url=node.get("url", ""),
        )

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Linear integration is not configured.")
