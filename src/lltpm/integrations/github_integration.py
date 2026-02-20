"""GitHub integration — PR tracking, build health, release notes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lltpm.integrations.base import Integration, IntegrationError


@dataclass
class PRSummary:
    number: int
    title: str
    author: str
    state: str  # open, closed, merged
    labels: list[str]
    review_state: str  # approved, changes_requested, pending
    url: str


@dataclass
class BuildStatus:
    ref: str
    state: str  # success, failure, pending
    checks: list[dict[str, str]]  # name, status, conclusion


class GitHubIntegration(Integration):
    """Read-only GitHub integration for program visibility."""

    def name(self) -> str:
        return "GitHub"

    def test_connection(self) -> bool:
        if not self.enabled:
            return False
        try:
            from github import Github
            g = Github(self._config.api_key)
            g.get_user().login
            return True
        except Exception:
            return False

    def list_open_prs(self, repo: str) -> list[PRSummary]:
        self._require_enabled()
        try:
            from github import Github
            g = Github(self._config.api_key)
            r = g.get_repo(repo)
            prs = []
            for pr in r.get_pulls(state="open"):
                reviews = list(pr.get_reviews())
                review_state = "pending"
                if any(rev.state == "APPROVED" for rev in reviews):
                    review_state = "approved"
                elif any(rev.state == "CHANGES_REQUESTED" for rev in reviews):
                    review_state = "changes_requested"
                prs.append(PRSummary(
                    number=pr.number,
                    title=pr.title,
                    author=pr.user.login,
                    state="open",
                    labels=[l.name for l in pr.labels],
                    review_state=review_state,
                    url=pr.html_url,
                ))
            return prs
        except Exception as e:
            raise IntegrationError(f"Failed to list PRs: {e}") from e

    def get_build_status(self, repo: str, ref: str = "main") -> BuildStatus:
        self._require_enabled()
        try:
            from github import Github
            g = Github(self._config.api_key)
            r = g.get_repo(repo)
            commit = r.get_commit(ref)
            checks = []
            for run in commit.get_check_runs():
                checks.append({
                    "name": run.name,
                    "status": run.status,
                    "conclusion": run.conclusion or "pending",
                })
            combined = commit.get_combined_status()
            return BuildStatus(
                ref=ref,
                state=combined.state,
                checks=checks,
            )
        except Exception as e:
            raise IntegrationError(f"Failed to get build status: {e}") from e

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("GitHub integration is not configured.")
