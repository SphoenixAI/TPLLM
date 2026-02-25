"""Calendar integration — schedule meetings only when the meeting gate approves.

Supports two backends:
- **Google Calendar** (default): Uses service account or OAuth credentials
  via the Google Calendar API (googleapis.com/calendar/v3).
- **Microsoft Graph**: Uses OAuth client credentials for Microsoft 365
  calendars via the Graph API (graph.microsoft.com/v1.0).

Environment variables:
- GOOGLE_CALENDAR_SERVICE_ACCOUNT — path to service account JSON key
- GOOGLE_CALENDAR_CALENDAR_ID — default calendar (defaults to "primary")
- MS_GRAPH_CLIENT_ID, MS_GRAPH_CLIENT_SECRET, MS_GRAPH_TENANT_ID — for Microsoft
"""

from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

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
    event_id: str = ""


@dataclass
class TimeSlot:
    start: datetime
    end: datetime


class CalendarIntegration(Integration):
    """Calendar integration for scheduling with Google Calendar or Microsoft Graph."""

    def name(self) -> str:
        return "Calendar"

    @property
    def _backend(self) -> str:
        return self._config.extra.get("backend", "google")

    def test_connection(self) -> bool:
        if not self.enabled:
            return False
        try:
            if self._backend == "microsoft":
                return self._ms_test_connection()
            return self._google_test_connection()
        except Exception:
            return False

    def find_available_slots(
        self,
        attendees: list[str],
        duration_minutes: int = 30,
        search_days: int = 5,
    ) -> list[TimeSlot]:
        """Find common free slots across attendees."""
        self._require_enabled()
        if self._backend == "microsoft":
            return self._ms_find_slots(attendees, duration_minutes, search_days)
        return self._google_find_slots(attendees, duration_minutes, search_days)

    def create_event(self, event: CalendarEvent) -> str:
        """Create a calendar event. Returns the event ID."""
        self._require_enabled()
        if self._backend == "microsoft":
            return self._ms_create_event(event)
        return self._google_create_event(event)

    def list_events(
        self,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        calendar_id: str = "",
    ) -> list[CalendarEvent]:
        """List events in a time range."""
        self._require_enabled()
        if self._backend == "microsoft":
            return self._ms_list_events(time_min, time_max)
        return self._google_list_events(time_min, time_max, calendar_id)

    def delete_event(self, event_id: str, calendar_id: str = "") -> bool:
        """Delete a calendar event."""
        self._require_enabled()
        if self._backend == "microsoft":
            return self._ms_delete_event(event_id)
        return self._google_delete_event(event_id, calendar_id)

    # ── Google Calendar backend ──────────────────────────────────

    def _google_test_connection(self) -> bool:
        service = self._get_google_service()
        cal_id = self._config.extra.get("calendar_id", "primary")
        result = service.calendars().get(calendarId=cal_id).execute()
        return "id" in result

    def _google_find_slots(
        self, attendees: list[str], duration_minutes: int, search_days: int
    ) -> list[TimeSlot]:
        service = self._get_google_service()
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=search_days)

        body = {
            "timeMin": now.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": email} for email in attendees],
        }

        try:
            result = service.freebusy().query(body=body).execute()
        except Exception as e:
            raise IntegrationError(f"Google Calendar freebusy query failed: {e}") from e

        # Collect all busy periods
        busy_periods: list[tuple[datetime, datetime]] = []
        for cal_data in result.get("calendars", {}).values():
            for busy in cal_data.get("busy", []):
                start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(busy["end"].replace("Z", "+00:00"))
                busy_periods.append((start, end_dt))

        busy_periods.sort(key=lambda x: x[0])
        return self._compute_free_slots(
            busy_periods, now, end, duration_minutes
        )

    def _google_create_event(self, event: CalendarEvent) -> str:
        service = self._get_google_service()
        cal_id = self._config.extra.get("calendar_id", "primary")

        body: dict[str, Any] = {
            "summary": event.title,
            "start": {"dateTime": event.start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": event.end.isoformat(), "timeZone": "UTC"},
            "attendees": [{"email": e} for e in event.attendees],
            "conferenceData": {
                "createRequest": {"requestId": f"lltpm-{int(time.time())}"}
            },
        }
        if event.description:
            body["description"] = event.description
        if event.location:
            body["location"] = event.location

        try:
            result = service.events().insert(
                calendarId=cal_id,
                body=body,
                conferenceDataVersion=1,
                sendUpdates="all",
            ).execute()
            return result.get("id", "")
        except Exception as e:
            raise IntegrationError(f"Google Calendar create event failed: {e}") from e

    def _google_list_events(
        self,
        time_min: datetime | None,
        time_max: datetime | None,
        calendar_id: str,
    ) -> list[CalendarEvent]:
        service = self._get_google_service()
        cal_id = calendar_id or self._config.extra.get("calendar_id", "primary")
        now = datetime.now(timezone.utc)

        params: dict[str, Any] = {
            "calendarId": cal_id,
            "timeMin": (time_min or now).isoformat(),
            "timeMax": (time_max or now + timedelta(days=7)).isoformat(),
            "maxResults": 50,
            "singleEvents": True,
            "orderBy": "startTime",
        }

        try:
            result = service.events().list(**params).execute()
        except Exception as e:
            raise IntegrationError(f"Google Calendar list events failed: {e}") from e

        events = []
        for item in result.get("items", []):
            start_str = item.get("start", {}).get("dateTime", item.get("start", {}).get("date", ""))
            end_str = item.get("end", {}).get("dateTime", item.get("end", {}).get("date", ""))
            events.append(CalendarEvent(
                event_id=item.get("id", ""),
                title=item.get("summary", ""),
                start=datetime.fromisoformat(start_str.replace("Z", "+00:00")) if start_str else now,
                end=datetime.fromisoformat(end_str.replace("Z", "+00:00")) if end_str else now,
                attendees=[a.get("email", "") for a in item.get("attendees", [])],
                location=item.get("location", ""),
                description=item.get("description", ""),
                meeting_link=item.get("hangoutLink", ""),
            ))
        return events

    def _google_delete_event(self, event_id: str, calendar_id: str) -> bool:
        service = self._get_google_service()
        cal_id = calendar_id or self._config.extra.get("calendar_id", "primary")
        try:
            service.events().delete(calendarId=cal_id, eventId=event_id).execute()
            return True
        except Exception as e:
            raise IntegrationError(f"Google Calendar delete event failed: {e}") from e

    def _get_google_service(self):
        """Build a Google Calendar API service using service account credentials."""
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as e:
            raise IntegrationError(
                "Google Calendar requires google-api-python-client and "
                "google-auth packages. Install with: "
                "pip install google-api-python-client google-auth"
            ) from e

        sa_key = self._config.api_key or self._config.extra.get("service_account_key", "")
        if not sa_key:
            raise IntegrationError("Google Calendar service account key path is required.")

        credentials = service_account.Credentials.from_service_account_file(
            sa_key,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )

        # If delegating to a user
        delegate_email = self._config.extra.get("delegate_email", "")
        if delegate_email:
            credentials = credentials.with_subject(delegate_email)

        return build("calendar", "v3", credentials=credentials)

    # ── Microsoft Graph backend ──────────────────────────────────

    def _ms_test_connection(self) -> bool:
        token = self._get_ms_token()
        data = self._ms_api_get("/me", token)
        return "id" in data

    def _ms_find_slots(
        self, attendees: list[str], duration_minutes: int, search_days: int
    ) -> list[TimeSlot]:
        token = self._get_ms_token()
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=search_days)

        body = {
            "schedules": attendees,
            "startTime": {"dateTime": now.isoformat(), "timeZone": "UTC"},
            "endTime": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "availabilityViewInterval": str(duration_minutes),
        }

        try:
            result = self._ms_api_post("/me/calendar/getSchedule", token, body)
        except Exception as e:
            raise IntegrationError(f"MS Graph schedule query failed: {e}") from e

        busy_periods: list[tuple[datetime, datetime]] = []
        for schedule in result.get("value", []):
            for item in schedule.get("scheduleItems", []):
                start = datetime.fromisoformat(
                    item["start"]["dateTime"].replace("Z", "+00:00")
                )
                end_dt = datetime.fromisoformat(
                    item["end"]["dateTime"].replace("Z", "+00:00")
                )
                busy_periods.append((start, end_dt))

        busy_periods.sort(key=lambda x: x[0])
        return self._compute_free_slots(busy_periods, now, end, duration_minutes)

    def _ms_create_event(self, event: CalendarEvent) -> str:
        token = self._get_ms_token()

        body: dict[str, Any] = {
            "subject": event.title,
            "start": {"dateTime": event.start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": event.end.isoformat(), "timeZone": "UTC"},
            "attendees": [
                {"emailAddress": {"address": e}, "type": "required"}
                for e in event.attendees
            ],
            "isOnlineMeeting": True,
            "onlineMeetingProvider": "teamsForBusiness",
        }
        if event.description:
            body["body"] = {"contentType": "HTML", "content": event.description}
        if event.location:
            body["location"] = {"displayName": event.location}

        try:
            result = self._ms_api_post("/me/events", token, body)
            return result.get("id", "")
        except Exception as e:
            raise IntegrationError(f"MS Graph create event failed: {e}") from e

    def _ms_list_events(
        self, time_min: datetime | None, time_max: datetime | None
    ) -> list[CalendarEvent]:
        token = self._get_ms_token()
        now = datetime.now(timezone.utc)
        start = (time_min or now).isoformat()
        end = (time_max or now + timedelta(days=7)).isoformat()

        try:
            result = self._ms_api_get(
                f"/me/calendarView?startDateTime={start}&endDateTime={end}&$top=50",
                token,
            )
        except Exception as e:
            raise IntegrationError(f"MS Graph list events failed: {e}") from e

        events = []
        for item in result.get("value", []):
            start_str = item.get("start", {}).get("dateTime", "")
            end_str = item.get("end", {}).get("dateTime", "")
            events.append(CalendarEvent(
                event_id=item.get("id", ""),
                title=item.get("subject", ""),
                start=datetime.fromisoformat(start_str) if start_str else now,
                end=datetime.fromisoformat(end_str) if end_str else now,
                attendees=[
                    a.get("emailAddress", {}).get("address", "")
                    for a in item.get("attendees", [])
                ],
                location=item.get("location", {}).get("displayName", ""),
                description=item.get("bodyPreview", ""),
                meeting_link=item.get("onlineMeeting", {}).get("joinUrl", "") if item.get("onlineMeeting") else "",
            ))
        return events

    def _ms_delete_event(self, event_id: str) -> bool:
        token = self._get_ms_token()
        try:
            import urllib.request
            req = urllib.request.Request(
                f"https://graph.microsoft.com/v1.0/me/events/{event_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                },
                method="DELETE",
            )
            with urllib.request.urlopen(req) as resp:
                resp.read()
            return True
        except Exception as e:
            raise IntegrationError(f"MS Graph delete event failed: {e}") from e

    def _get_ms_token(self) -> str:
        """Get a Microsoft Graph access token using client credentials."""
        import urllib.request
        import urllib.parse

        client_id = self._config.extra.get("ms_client_id", "")
        client_secret = self._config.extra.get("ms_client_secret", "")
        tenant_id = self._config.extra.get("ms_tenant_id", "")

        if not all([client_id, client_secret, tenant_id]):
            raise IntegrationError(
                "Microsoft Calendar requires MS_GRAPH_CLIENT_ID, "
                "MS_GRAPH_CLIENT_SECRET, and MS_GRAPH_TENANT_ID"
            )

        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }).encode()

        req = urllib.request.Request(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                token_data = json.loads(resp.read())
        except Exception as e:
            raise IntegrationError(f"MS Graph token request failed: {e}") from e

        token = token_data.get("access_token", "")
        if not token:
            raise IntegrationError("MS Graph returned empty access token.")
        return token

    def _ms_api_get(self, path: str, token: str) -> dict:
        import urllib.request
        url = f"https://graph.microsoft.com/v1.0{path}" if not path.startswith("http") else path
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _ms_api_post(self, path: str, token: str, body: dict) -> dict:
        import urllib.request
        url = f"https://graph.microsoft.com/v1.0{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    # ── Shared helpers ──────────────────────────────────────────

    @staticmethod
    def _compute_free_slots(
        busy_periods: list[tuple[datetime, datetime]],
        search_start: datetime,
        search_end: datetime,
        duration_minutes: int,
    ) -> list[TimeSlot]:
        """Find free slots between busy periods during business hours (9-17 UTC)."""
        slots: list[TimeSlot] = []
        duration = timedelta(minutes=duration_minutes)

        current = search_start
        day = current.date()
        end_date = search_end.date()

        while day <= end_date and len(slots) < 10:
            # Skip weekends
            if day.weekday() >= 5:
                day += timedelta(days=1)
                current = datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc)
                continue

            day_start = datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc)
            day_end = datetime(day.year, day.month, day.day, 17, 0, tzinfo=timezone.utc)

            if current < day_start:
                current = day_start

            # Find gaps in this day
            day_busy = [
                (max(s, day_start), min(e, day_end))
                for s, e in busy_periods
                if s < day_end and e > day_start
            ]
            day_busy.sort(key=lambda x: x[0])

            cursor = max(current, day_start)
            for busy_start, busy_end in day_busy:
                if cursor + duration <= busy_start:
                    slots.append(TimeSlot(start=cursor, end=cursor + duration))
                    if len(slots) >= 10:
                        break
                cursor = max(cursor, busy_end)

            if len(slots) < 10 and cursor + duration <= day_end:
                slots.append(TimeSlot(start=cursor, end=cursor + duration))

            day += timedelta(days=1)
            current = datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc)

        return slots

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Calendar integration is not configured.")
