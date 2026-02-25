"""Calendar tools — scheduling (requires calendar integration)."""

from __future__ import annotations

from typing import Any

from lltpm.config import LLTPMConfig
from lltpm.integrations.calendar_integration import CalendarIntegration


def get_calendar_tools(config: LLTPMConfig) -> list[dict[str, Any]]:
    cal = CalendarIntegration(config.calendar)

    def find_meeting_slot(
        attendees: list[str],
        duration_minutes: int = 30,
        search_days: int = 5,
    ) -> str:
        try:
            slots = cal.find_available_slots(attendees, duration_minutes, search_days)
            if not slots:
                return "No common slots found in the next {search_days} days."
            lines = ["Available slots:"]
            for s in slots[:5]:
                lines.append(f"  - {s.start.isoformat()} to {s.end.isoformat()}")
            return "\n".join(lines)
        except Exception as e:
            return f"Calendar error: {e}"

    return [
        {
            "name": "find_meeting_slot",
            "description": (
                "Find available meeting slots across attendees. "
                "Only use this AFTER the meeting gate has approved the meeting."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of attendee emails",
                    },
                    "duration_minutes": {"type": "integer", "description": "Meeting duration in minutes"},
                    "search_days": {"type": "integer", "description": "Days ahead to search"},
                },
                "required": ["attendees"],
            },
            "executor": find_meeting_slot,
        },
    ]
