"""Tests for Calendar Integration (Google Calendar + MS Graph)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from lltpm.config import IntegrationConfig
from lltpm.integrations.calendar_integration import (
    CalendarIntegration,
    CalendarEvent,
    TimeSlot,
)
from lltpm.integrations.base import IntegrationError

import pytest


def _make_google_config(**overrides):
    defaults = {
        "enabled": True,
        "api_key": "/tmp/test-service-account.json",
        "extra": {
            "backend": "google",
            "calendar_id": "primary",
        },
    }
    defaults.update(overrides)
    return IntegrationConfig(**defaults)


def _make_ms_config(**overrides):
    defaults = {
        "enabled": True,
        "api_key": "",
        "extra": {
            "backend": "microsoft",
            "ms_client_id": "test-client-id",
            "ms_client_secret": "test-client-secret",
            "ms_tenant_id": "test-tenant-id",
        },
    }
    defaults.update(overrides)
    return IntegrationConfig(**defaults)


def test_integration_name():
    cal = CalendarIntegration(_make_google_config())
    assert cal.name() == "Calendar"


def test_google_backend_default():
    cal = CalendarIntegration(_make_google_config())
    assert cal._backend == "google"


def test_microsoft_backend():
    cal = CalendarIntegration(_make_ms_config())
    assert cal._backend == "microsoft"


def test_disabled_raises():
    cal = CalendarIntegration(IntegrationConfig(enabled=False))
    with pytest.raises(IntegrationError, match="not configured"):
        cal.find_available_slots(["alice@example.com"])


def test_disabled_create_event_raises():
    cal = CalendarIntegration(IntegrationConfig(enabled=False))
    event = CalendarEvent(
        title="Test", start=datetime.now(timezone.utc),
        end=datetime.now(timezone.utc), attendees=[],
    )
    with pytest.raises(IntegrationError, match="not configured"):
        cal.create_event(event)


def test_compute_free_slots_no_busy():
    """With no busy periods, should find slots in business hours."""
    now = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)  # Monday
    end = datetime(2026, 3, 3, 17, 0, tzinfo=timezone.utc)  # Tuesday
    slots = CalendarIntegration._compute_free_slots([], now, end, 30)
    assert len(slots) > 0
    for slot in slots:
        assert slot.start.hour >= 9
        assert slot.end.hour <= 17


def test_compute_free_slots_busy_morning():
    """When morning is busy, slots should start after."""
    now = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)  # Monday
    end = datetime(2026, 3, 2, 17, 0, tzinfo=timezone.utc)
    busy = [(
        datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc),
    )]
    slots = CalendarIntegration._compute_free_slots(busy, now, end, 30)
    assert len(slots) > 0
    assert slots[0].start >= datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)


def test_compute_free_slots_skips_weekends():
    """Should not return slots on Saturday or Sunday."""
    saturday = datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc)  # Saturday
    monday = datetime(2026, 3, 9, 17, 0, tzinfo=timezone.utc)  # Monday
    slots = CalendarIntegration._compute_free_slots([], saturday, monday, 30)
    for slot in slots:
        assert slot.start.weekday() < 5  # Not weekend


def test_compute_free_slots_max_10():
    """Should return at most 10 slots."""
    now = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
    slots = CalendarIntegration._compute_free_slots([], now, end, 30)
    assert len(slots) <= 10


def test_compute_free_slots_respects_duration():
    """Slots should be exactly the requested duration."""
    now = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 2, 17, 0, tzinfo=timezone.utc)
    slots = CalendarIntegration._compute_free_slots([], now, end, 60)
    for slot in slots:
        assert (slot.end - slot.start) == timedelta(minutes=60)


def test_compute_free_slots_full_day_busy():
    """When entire day is busy, no slots from that day."""
    now = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 2, 17, 0, tzinfo=timezone.utc)
    busy = [(
        datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 2, 17, 0, tzinfo=timezone.utc),
    )]
    slots = CalendarIntegration._compute_free_slots(busy, now, end, 30)
    assert len(slots) == 0


def test_test_connection_disabled():
    cal = CalendarIntegration(IntegrationConfig(enabled=False))
    assert cal.test_connection() is False


def test_google_requires_service_account_or_libs():
    """Without Google libs, raises import error; with them, requires service account."""
    cal = CalendarIntegration(IntegrationConfig(
        enabled=True, api_key="", extra={"backend": "google"},
    ))
    with pytest.raises(IntegrationError):
        cal._get_google_service()


def test_ms_requires_credentials():
    cal = CalendarIntegration(IntegrationConfig(
        enabled=True, extra={"backend": "microsoft"},
    ))
    with pytest.raises(IntegrationError, match="MS_GRAPH_CLIENT_ID"):
        cal._get_ms_token()


def test_calendar_event_dataclass():
    now = datetime.now(timezone.utc)
    event = CalendarEvent(
        title="Sprint Planning",
        start=now,
        end=now + timedelta(hours=1),
        attendees=["alice@example.com", "bob@example.com"],
        location="Room 42",
        description="Weekly sprint planning",
    )
    assert event.title == "Sprint Planning"
    assert len(event.attendees) == 2
    assert event.event_id == ""


def test_timeslot_dataclass():
    now = datetime.now(timezone.utc)
    slot = TimeSlot(start=now, end=now + timedelta(minutes=30))
    assert (slot.end - slot.start) == timedelta(minutes=30)
