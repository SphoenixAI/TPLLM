"""Tool definitions for Slack bot interface."""

from __future__ import annotations

import json
from typing import Any

from tpllm.config import TPLLMConfig


def get_slack_bot_tools(config: TPLLMConfig) -> list[dict[str, Any]]:
    """Return tool definitions for Slack bot interaction."""
    from tpllm.integrations.slack_bot import SlackBot

    bot = SlackBot(config.slack)

    def slack_send_message(
        channel: str, text: str, thread_ts: str = "", blocks_json: str = "",
    ) -> str:
        blocks = json.loads(blocks_json) if blocks_json else None
        result = bot.send_message(channel, text, thread_ts, blocks)
        return json.dumps(result)

    def slack_send_status(
        channel: str,
        program_id: str,
        health: str,
        milestones: str,
        risks: str,
        blockers: str,
        thread_ts: str = "",
    ) -> str:
        blocks = bot.build_status_blocks(program_id, health, milestones, risks, blockers)
        result = bot.send_message(
            channel,
            f"Status: {program_id} — {health}",
            thread_ts,
            blocks,
        )
        return json.dumps(result)

    def slack_send_approval(
        channel: str,
        request_id: str,
        title: str,
        requester: str,
        description: str,
        step_info: str,
    ) -> str:
        blocks = bot.build_approval_blocks(request_id, title, requester, description, step_info)
        result = bot.send_message(
            channel,
            f"Approval needed: {title}",
            blocks=blocks,
        )
        return json.dumps(result)

    def slack_send_nudge(
        channel: str,
        nudge_id: str,
        owner: str,
        topic: str,
        urgency: str = "medium",
        days_pending: int = 1,
    ) -> str:
        blocks = bot.build_nudge_blocks(nudge_id, owner, topic, urgency, days_pending)
        result = bot.send_message(
            channel,
            f"Follow-up: {topic} ({owner})",
            blocks=blocks,
        )
        return json.dumps(result)

    def slack_send_risk_summary(
        channel: str,
        program_id: str,
        risks_json: str,
        thread_ts: str = "",
    ) -> str:
        risks = json.loads(risks_json) if risks_json else []
        blocks = bot.build_risk_blocks(program_id, risks)
        result = bot.send_message(
            channel,
            f"Risks: {program_id}",
            thread_ts,
            blocks,
        )
        return json.dumps(result)

    def slack_get_bot_summary() -> str:
        return bot.get_summary()

    return [
        {
            "name": "slack_send_message",
            "description": "Send a message to a Slack channel or thread, optionally with Block Kit blocks",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Slack channel ID or name"},
                    "text": {"type": "string", "description": "Message text (fallback for blocks)"},
                    "thread_ts": {"type": "string", "description": "Thread timestamp to reply in"},
                    "blocks_json": {"type": "string", "description": "JSON-encoded Block Kit blocks"},
                },
                "required": ["channel", "text"],
            },
            "executor": slack_send_message,
        },
        {
            "name": "slack_send_status",
            "description": "Send a rich program status update to Slack with health indicators",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "program_id": {"type": "string"},
                    "health": {"type": "string", "enum": ["green", "yellow", "red"]},
                    "milestones": {"type": "string"},
                    "risks": {"type": "string"},
                    "blockers": {"type": "string"},
                    "thread_ts": {"type": "string"},
                },
                "required": ["channel", "program_id", "health", "milestones", "risks", "blockers"],
            },
            "executor": slack_send_status,
        },
        {
            "name": "slack_send_approval",
            "description": "Send an approval request to Slack with approve/reject/delegate buttons",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "request_id": {"type": "string"},
                    "title": {"type": "string"},
                    "requester": {"type": "string"},
                    "description": {"type": "string"},
                    "step_info": {"type": "string"},
                },
                "required": ["channel", "request_id", "title", "requester", "description", "step_info"],
            },
            "executor": slack_send_approval,
        },
        {
            "name": "slack_send_nudge",
            "description": "Send a follow-up nudge to Slack with done/snooze/delegate actions",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "nudge_id": {"type": "string"},
                    "owner": {"type": "string"},
                    "topic": {"type": "string"},
                    "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
                    "days_pending": {"type": "integer"},
                },
                "required": ["channel", "nudge_id", "owner", "topic"],
            },
            "executor": slack_send_nudge,
        },
        {
            "name": "slack_send_risk_summary",
            "description": "Send a risk summary to Slack with severity indicators",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "program_id": {"type": "string"},
                    "risks_json": {"type": "string", "description": "JSON array of risk objects"},
                    "thread_ts": {"type": "string"},
                },
                "required": ["channel", "program_id", "risks_json"],
            },
            "executor": slack_send_risk_summary,
        },
        {
            "name": "slack_get_bot_summary",
            "description": "Get a summary of the Slack bot state (conversations, commands)",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "executor": slack_get_bot_summary,
        },
    ]
