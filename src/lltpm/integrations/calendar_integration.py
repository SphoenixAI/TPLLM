"""Calendar integration — schedule meetings only when the meeting gate approves.

This is a stub that models the interface. Real implementations would
integrate with Google Calendar API or Microsoft Graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from lltpm.integrations.base import Integration, IntegrationError


@dataclass
class CalendarEvent:
    title: str
    start: datetime
    end: datetime
    attendees: list[str]
    location: str = ""
    description: str = ""
    meeting_link: str = ""


@dataclass
class TimeSlot:
    start: datetime
    end: datetime


class CalendarIntegration(Integration):
    """Calendar integration for scheduling (stub implementation)."""

    def name(self) -> str:
        return "Calendar"

    def test_connection(self) -> bool:
        # Stub — always returns False until configured
        return self.enabled

    def find_available_slots(
        self,
        attendees: list[str],
        duration_minutes: int = 30,
        search_days: int = 5,
    ) -> list[TimeSlot]:
        """Find common free slots across attendees.

        This is a stub — real implementation would query Google Calendar
        or Microsoft Graph for free/busy data.
        """
        self._require_enabled()
        raise IntegrationError(
            "Calendar integration is a stub. Implement with Google Calendar "
            "or Microsoft Graph API for production use."
        )

    def create_event(self, event: CalendarEvent) -> str:
        """Create a calendar event. Returns the event ID.

        Stub implementation — returns a placeholder.
        """
        self._require_enabled()
        raise IntegrationError(
            "Calendar integration is a stub. Implement with Google Calendar "
            "or Microsoft Graph API for production use."
        )

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Calendar integration is not configured.")
