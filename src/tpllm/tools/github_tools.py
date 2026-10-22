"""GitHub tools — PR tracking, build health (requires GitHub integration)."""

from __future__ import annotations

import json
from typing import Any

from tpllm.config import TPLLMConfig
from tpllm.integrations.github_integration import GitHubIntegration


def get_github_tools(config: TPLLMConfig) -> list[dict[str, Any]]:
    gh = GitHubIntegration(config.github)

    def list_open_prs(repo: str) -> str:
        prs = gh.list_open_prs(repo)
        if not prs:
            return "No open PRs."
        lines = []
        for pr in prs:
            labels = ", ".join(pr.labels) if pr.labels else "none"
            lines.append(
                f"#{pr.number} {pr.title} — by {pr.author} — "
                f"review: {pr.review_state} — labels: {labels}"
            )
        return "\n".join(lines)

    def get_build_status(repo: str, ref: str = "main") -> str:
        status = gh.get_build_status(repo, ref)
        lines = [f"Build on {status.ref}: **{status.state}**"]
        for check in status.checks:
            lines.append(f"  - {check['name']}: {check['conclusion']}")
        return "\n".join(lines)

    return [
        {
            "name": "list_open_prs",
            "description": "List open pull requests for a GitHub repo with review status.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "GitHub repo in owner/name format"},
                },
                "required": ["repo"],
            },
            "executor": list_open_prs,
        },
        {
            "name": "get_build_status",
            "description": "Get the build/CI status for a repo and branch.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "GitHub repo in owner/name format"},
                    "ref": {"type": "string", "description": "Branch or commit ref (default: main)"},
                },
                "required": ["repo"],
            },
            "executor": get_build_status,
        },
    ]
