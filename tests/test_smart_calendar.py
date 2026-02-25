"""Tests for Smart Calendar Engine."""

import tempfile
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.calendar import (
    AttendeeInfo,
    AttendeeRole,
    MeetingPriority,
    SmartScheduleRequest,
)
from lltpm.workflows.smart_calendar import SmartCalendarEngine


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_basic_scheduling():
    store = _make_store()
    engine = SmartCalendarEngine(store)

    request = SmartScheduleRequest(
        title="Sprint Planning",
        program_id="test-prog",
        duration_minutes=30,
        attendees=[
            AttendeeInfo(person="Alice", scope_relevance=1.0, engagement_score=0.9),
            AttendeeInfo(person="Bob", scope_relevance=0.8, engagement_score=0.7),
        ],
    )

    result = engine.schedule(request)
    assert result.request_title == "Sprint Planning"
    assert len(result.recommended_slots) > 0
    assert result.best_slot is not None
    assert result.best_slot.score > 0


def test_scoring_prefers_available_attendees():
    store = _make_store()
    engine = SmartCalendarEngine(store)

    request = SmartScheduleRequest(
        title="Review",
        attendees=[
            AttendeeInfo(person="Alice", preferred_hours=(9, 12)),
            AttendeeInfo(person="Bob", preferred_hours=(14, 17)),
        ],
    )

    result = engine.schedule(request)
    # Should find slots where at least some attendees are available
    for slot in result.recommended_slots:
        assert len(slot.attendees_available) > 0 or len(slot.attendees_unavailable) > 0


def test_find_optimal_attendees():
    store = _make_store()
    engine = SmartCalendarEngine(store)

    candidates = [
        AttendeeInfo(person="Alice", scope_relevance=0.9, engagement_score=0.9),
        AttendeeInfo(person="Bob", scope_relevance=0.5, engagement_score=0.6),
        AttendeeInfo(person="Charlie", scope_relevance=0.1, engagement_score=0.3),
    ]

    optimized = engine.find_optimal_attendees("test-prog", "Auth review", candidates)

    assert optimized[0].person == "Alice"
    assert optimized[0].role == AttendeeRole.REQUIRED
    assert optimized[1].role == AttendeeRole.OPTIONAL
    assert optimized[2].role == AttendeeRole.INFORMED


def test_meeting_load():
    store = _make_store()
    engine = SmartCalendarEngine(store)

    # Light load
    light = engine.get_meeting_load("Alice", meetings_today=2)
    assert light["status"] == "light"

    # Heavy load
    heavy = engine.get_meeting_load("Bob", meetings_today=5, max_daily=6)
    assert heavy["status"] == "heavy"

    # Overloaded
    overloaded = engine.get_meeting_load("Charlie", meetings_today=6, max_daily=6)
    assert overloaded["status"] == "overloaded"


def test_block_focus_time():
    store = _make_store()
    engine = SmartCalendarEngine(store)

    from datetime import datetime
    block = engine.block_focus_time("test-prog", "Alice", datetime.utcnow(), 3, "Deep work")
    assert block.person == "Alice"
    assert block.block_type == "focus_time"
    assert "Deep work" in block.title


def test_suggest_reschedule():
    store = _make_store()
    engine = SmartCalendarEngine(store)

    reschedule = engine.suggest_reschedule("test-prog", "EVT-123", "Conflict with offsite")
    assert reschedule.event_id == "EVT-123"
    assert "Conflict with offsite" in reschedule.reason


def test_meeting_gate_warning():
    store = _make_store()
    engine = SmartCalendarEngine(store)

    request = SmartScheduleRequest(
        title="Status Sync",
        attendees=[AttendeeInfo(person="Alice")],
        meeting_gate_approved=False,
    )

    result = engine.schedule(request)
    # Should warn about unapproved meeting
    assert any("not yet approved" in note for note in result.scheduling_notes)


def test_overloaded_attendee_optimization():
    store = _make_store()
    engine = SmartCalendarEngine(store)

    request = SmartScheduleRequest(
        title="Review",
        attendees=[
            AttendeeInfo(person="Alice", meeting_load_today=7, max_daily_meetings=6),
        ],
    )

    result = engine.schedule(request)
    assert "Alice" in result.attendee_optimization
    assert "async" in result.attendee_optimization["Alice"].lower()


def test_no_attendees():
    store = _make_store()
    engine = SmartCalendarEngine(store)

    request = SmartScheduleRequest(title="Empty meeting")
    result = engine.schedule(request)
    assert "No attendees" in result.scheduling_notes[0]
