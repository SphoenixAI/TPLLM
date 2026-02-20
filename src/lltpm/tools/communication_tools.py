"""Communication tools — Slack messaging (requires Slack integration)."""

from __future__ import annotations

from typing import Any

from lltpm.config import LLTPMConfig
from lltpm.integrations.slack_integration import SlackIntegration


def get_communication_tools(config: LLTPMConfig) -> list[dict[str, Any]]:
    slack = SlackIntegration(config.slack)

    def send_slack_message(channel: str, text: str) -> str:
        ts = slack.send_message(channel, text)
        return f"Message sent (ts={ts})"

    def request_async_update(channel: str, owner: str, topic: str, deadline: str) -> str:
        ts = slack.request_update(channel, owner, topic, deadline)
        return f"Update request sent to {owner} (ts={ts})"

    return [
        {
            "name": "send_slack_message",
            "description": "Send a message to a Slack channel.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Slack channel name or ID"},
                    "text": {"type": "string", "description": "Message text (supports Slack markdown)"},
                },
                "required": ["channel", "text"],
            },
            "executor": send_slack_message,
        },
        {
            "name": "request_async_update",
            "description": (
                "Send a structured async update request to a team member via Slack. "
                "Uses the standard format: Yesterday / Today / Blockers / ETA confidence."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "owner": {"type": "string", "description": "Person to request update from"},
                    "topic": {"type": "string", "description": "What to update on"},
                    "deadline": {"type": "string", "description": "When the update is needed by"},
                },
                "required": ["channel", "owner", "topic", "deadline"],
            },
            "executor": request_async_update,
        },
    ]
