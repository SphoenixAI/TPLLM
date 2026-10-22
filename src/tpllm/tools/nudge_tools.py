"""Nudge tools — smart follow-up engine with engagement budgets."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.nudge_engine import NudgeEngine


def get_nudge_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    engine = NudgeEngine(store)

    def create_nudge(
        program_id: str,
        target_person: str,
        subject: str,
        why_it_matters: str = "",
        blocks: list[str] | None = None,
        quick_options: list[str] | None = None,
        escalate_to: str = "",
        channel: str = "IN_TOOL",
    ) -> str:
        record = engine.create_nudge(
            program_id, target_person, subject, why_it_matters,
            blocks, "", "", quick_options, escalate_to, channel,
        )
        return json.dumps({"id": record.id, "person": record.target_person, "subject": record.subject, "tier": record.tier.value})

    def evaluate_nudges(program_id: str) -> str:
        actions = engine.evaluate_pending(program_id)
        if not actions:
            return "No nudges need action right now."
        lines = [f"# Nudge Actions ({len(actions)})"]
        for a in actions:
            if a["action"] == "SEND_NUDGE":
                lines.append(f"- [{a['tier']}] → **{a['person']}**: {a['subject']}")
                if a.get("message"):
                    lines.append(f"  Message: {a['message'][:150]}...")
            elif a["action"] == "BUDGET_EXHAUSTED":
                lines.append(f"- [SKIP] {a['person']}: Budget exhausted — {a['detail']}")
        return "\n".join(lines)

    def record_nudge_response(program_id: str, nudge_id: str, response: str) -> str:
        success = engine.record_response(program_id, nudge_id, response)
        return json.dumps({"success": success})

    def set_engagement_budget(program_id: str, person: str, daily: int = 3, weekly: int = 10) -> str:
        budget = engine.set_budget(program_id, person, daily, weekly)
        return json.dumps({"person": budget.person, "daily": budget.daily_budget, "weekly": budget.weekly_budget})

    def get_nudge_summary(program_id: str) -> str:
        return engine.get_summary(program_id)

    return [
        {
            "name": "create_nudge",
            "description": (
                "Create a smart follow-up for someone. Starts silently (observe), "
                "then escalates through gentle remind → firm remind → escalate. "
                "Always include WHY it matters and quick-response options."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "target_person": {"type": "string"},
                    "subject": {"type": "string", "description": "What you need from them"},
                    "why_it_matters": {"type": "string", "description": "Why this is important / what it blocks"},
                    "blocks": {"type": "array", "items": {"type": "string"}, "description": "What this is blocking"},
                    "quick_options": {
                        "type": "array", "items": {"type": "string"},
                        "description": "One-click response options (e.g. ['On track', 'Slipping', 'Blocked'])",
                    },
                    "escalate_to": {"type": "string", "description": "Who to escalate to at tier 3"},
                    "channel": {"type": "string", "enum": ["SLACK", "GCHAT", "EMAIL", "IN_TOOL"]},
                },
                "required": ["program_id", "target_person", "subject"],
            },
            "executor": create_nudge,
        },
        {
            "name": "evaluate_nudges",
            "description": (
                "Evaluate all pending nudges and determine which need action. "
                "Respects engagement budgets, quiet hours, and escalation timing."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": evaluate_nudges,
        },
        {
            "name": "record_nudge_response",
            "description": "Record that someone responded to a nudge.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "nudge_id": {"type": "string"},
                    "response": {"type": "string"},
                },
                "required": ["program_id", "nudge_id", "response"],
            },
            "executor": record_nudge_response,
        },
        {
            "name": "set_engagement_budget",
            "description": "Set how many times per day/week the agent can ping someone.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "person": {"type": "string"},
                    "daily": {"type": "integer", "description": "Max pings per day (default 3)"},
                    "weekly": {"type": "integer", "description": "Max pings per week (default 10)"},
                },
                "required": ["program_id", "person"],
            },
            "executor": set_engagement_budget,
        },
        {
            "name": "get_nudge_summary",
            "description": "Get summary of all nudges: pending, awaiting response, response rate.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_nudge_summary,
        },
    ]
