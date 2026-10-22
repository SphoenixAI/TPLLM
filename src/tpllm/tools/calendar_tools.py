"""Calendar tools — scheduling via Google Calendar or Microsoft Graph."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from tpllm.config import TPLLMConfig
from tpllm.integrations.calendar_integration import CalendarIntegration, CalendarEvent


def get_calendar_tools(config: TPLLMConfig) -> list[dict[str, Any]]:
    cal = CalendarIntegration(config.calendar)

    def find_meeting_slot(
        attendees: list[str],
        duration_minutes: int = 30,
        search_days: int = 5,
    ) -> str:
        try:
            slots = cal.find_available_slots(attendees, duration_minutes, search_days)
            if not slots:
                return f"No common slots found in the next {search_days} days."
            lines = ["Available slots:"]
            for s in slots[:5]:
                lines.append(f"  - {s.start.isoformat()} to {s.end.isoformat()}")
            return "\n".join(lines)
        except Exception as e:
            return f"Calendar error: {e}"

    def create_calendar_event(
        title: str,
        start_time: str,
        end_time: str,
        attendees: list[str] | None = None,
        description: str = "",
        location: str = "",
    ) -> str:
        try:
            start = datetime.fromisoformat(start_time)
            end = datetime.fromisoformat(end_time)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)

            event = CalendarEvent(
                title=title,
                start=start,
                end=end,
                attendees=attendees or [],
                description=description,
                location=location,
            )
            event_id = cal.create_event(event)
            return f"Event '{title}' created (ID: {event_id}). Invites sent to attendees."
        except Exception as e:
            return f"Calendar error: {e}"

    def list_calendar_events(
        days_ahead: int = 7,
    ) -> str:
        try:
            now = datetime.now(timezone.utc)
            events = cal.list_events(
                time_min=now,
                time_max=now + timedelta(days=days_ahead),
            )
            if not events:
                return f"No events in the next {days_ahead} days."
            lines = [f"Calendar events (next {days_ahead} days):"]
            for e in events:
                lines.append(
                    f"  - {e.title} | {e.start.strftime('%b %d %H:%M')} - "
                    f"{e.end.strftime('%H:%M')} | {len(e.attendees)} attendees"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Calendar error: {e}"

    def delete_calendar_event(event_id: str) -> str:
        try:
            cal.delete_event(event_id)
            return f"Event {event_id} deleted."
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
        {
            "name": "create_calendar_event",
            "description": "Create a calendar event with attendees and optional video link.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Event title"},
                    "start_time": {"type": "string", "description": "Start time (ISO 8601)"},
                    "end_time": {"type": "string", "description": "End time (ISO 8601)"},
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Attendee emails",
                    },
                    "description": {"type": "string", "description": "Event description"},
                    "location": {"type": "string", "description": "Location or room"},
                },
                "required": ["title", "start_time", "end_time"],
            },
            "executor": create_calendar_event,
        },
        {
            "name": "list_calendar_events",
            "description": "List upcoming calendar events.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "description": "Number of days to look ahead"},
                },
                "required": [],
            },
            "executor": list_calendar_events,
        },
        {
            "name": "delete_calendar_event",
            "description": "Delete/cancel a calendar event by ID.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Calendar event ID"},
                },
                "required": ["event_id"],
            },
            "executor": delete_calendar_event,
        },
    ]
