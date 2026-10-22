"""Tool definitions for notification routing and preferences."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.notifications import (
    NotificationCategory,
    NotificationPriority,
)
from tpllm.workflows.notification_router import NotificationRouter


def get_notification_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    """Return tool definitions for notification management."""
    router = NotificationRouter(store)

    def send_notification(
        recipient: str,
        title: str,
        body: str,
        category: str = "general",
        priority: str = "normal",
        program_id: str = "",
    ) -> str:
        cat = NotificationCategory(category) if category in [c.value for c in NotificationCategory] else NotificationCategory.GENERAL
        pri = NotificationPriority(priority) if priority in [p.value for p in NotificationPriority] else NotificationPriority.NORMAL
        n = router.send(recipient, title, body, cat, pri, program_id)
        return json.dumps({
            "id": n.id,
            "channel": n.channel.value,
            "sent": n.is_sent,
            "batched": n.batched,
        })

    def get_preferences(person: str) -> str:
        prefs = router.get_preferences(person)
        return json.dumps({
            "person": prefs.person,
            "channels": [
                {"channel": c.channel.value, "enabled": c.enabled,
                 "categories": [cat.value for cat in c.categories]}
                for c in prefs.channels
            ],
            "quiet_hours": {
                "start": prefs.quiet_hours.start.isoformat(),
                "end": prefs.quiet_hours.end.isoformat(),
            },
            "ooo": prefs.out_of_office,
            "ooo_delegate": prefs.ooo_delegate,
        })

    def set_ooo(person: str, ooo: bool = True, delegate: str = "") -> str:
        router.set_out_of_office(person, ooo, delegate)
        return json.dumps({"person": person, "ooo": ooo, "delegate": delegate})

    def generate_digest(recipient: str, period: str = "daily") -> str:
        digest = router.generate_digest(recipient, period)
        if not digest:
            return json.dumps({"message": "No queued notifications"})
        return digest.render()

    def get_notification_summary(recipient: str) -> str:
        return router.get_summary(recipient)

    return [
        {
            "name": "send_notification",
            "description": "Send a notification routed through the recipient's preferred channel",
            "input_schema": {
                "type": "object",
                "properties": {
                    "recipient": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [c.value for c in NotificationCategory],
                    },
                    "priority": {
                        "type": "string",
                        "enum": [p.value for p in NotificationPriority],
                    },
                    "program_id": {"type": "string"},
                },
                "required": ["recipient", "title", "body"],
            },
            "executor": send_notification,
        },
        {
            "name": "get_notification_preferences",
            "description": "Get a person's notification channel preferences and quiet hours",
            "input_schema": {
                "type": "object",
                "properties": {"person": {"type": "string"}},
                "required": ["person"],
            },
            "executor": get_preferences,
        },
        {
            "name": "set_out_of_office",
            "description": "Set a person's out-of-office status and notification delegate",
            "input_schema": {
                "type": "object",
                "properties": {
                    "person": {"type": "string"},
                    "ooo": {"type": "boolean"},
                    "delegate": {"type": "string", "description": "Person to route notifications to"},
                },
                "required": ["person"],
            },
            "executor": set_ooo,
        },
        {
            "name": "generate_notification_digest",
            "description": "Generate a batched notification digest for a person",
            "input_schema": {
                "type": "object",
                "properties": {
                    "recipient": {"type": "string"},
                    "period": {"type": "string", "enum": ["daily", "weekly"]},
                },
                "required": ["recipient"],
            },
            "executor": generate_digest,
        },
        {
            "name": "get_notification_summary",
            "description": "Get a summary of notification activity for a person",
            "input_schema": {
                "type": "object",
                "properties": {"recipient": {"type": "string"}},
                "required": ["recipient"],
            },
            "executor": get_notification_summary,
        },
    ]
