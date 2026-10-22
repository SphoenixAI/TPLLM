"""Calendar models — smart scheduling, availability optimization, and meeting coordination.

Supports intelligent meeting scheduling that considers:
- Attendee availability and working hours
- Engagement profiles (who is most active/relevant)
- Scope relevance (who needs to be in this meeting)
- Meeting load balancing (avoid meeting overload)
"""

from __future__ import annotations

from datetime import datetime, time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MeetingPriority(str, Enum):
    CRITICAL = "critical"      # must happen, optimize for soonest
    HIGH = "high"              # important, within 2 days
    MEDIUM = "medium"          # standard, within a week
    LOW = "low"                # nice-to-have, flexible timing


class AttendeeRole(str, Enum):
    REQUIRED = "required"      # must attend
    OPTIONAL = "optional"      # should attend if available
    INFORMED = "informed"      # just needs the notes afterward


class RecurrencePattern(str, Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


class AvailabilitySlot(BaseModel):
    """A time slot with availability information."""
    start: datetime
    end: datetime
    score: float = 1.0  # 0.0 to 1.0, higher = better fit
    conflicts: list[str] = Field(default_factory=list)
    attendees_available: list[str] = Field(default_factory=list)
    attendees_unavailable: list[str] = Field(default_factory=list)


class AttendeeInfo(BaseModel):
    """Information about a meeting attendee for scheduling optimization."""
    person: str
    email: str = ""
    role: AttendeeRole = AttendeeRole.REQUIRED
    scope_relevance: float = 1.0  # how relevant they are to this topic
    # engagement_score was removed. It was a per-person
    # score, and it made somebody a *required* attendee for
    # answering messages promptly rather than for being relevant to
    # the topic. scope_relevance is the criterion that was meant.
    preferred_hours: tuple[int, int] = (9, 17)  # start, end hour
    timezone: str = "UTC"
    meeting_load_today: int = 0  # how many meetings they already have today
    max_daily_meetings: int = 6


class SmartScheduleRequest(BaseModel):
    """A request to intelligently schedule a meeting."""
    title: str
    description: str = ""
    program_id: str = ""
    duration_minutes: int = 30
    priority: MeetingPriority = MeetingPriority.MEDIUM
    attendees: list[AttendeeInfo] = Field(default_factory=list)
    preferred_window_days: int = 5  # look ahead this many days
    avoid_mondays: bool = False
    avoid_fridays: bool = True  # meetings on Fridays = low engagement
    avoid_lunch: bool = True  # 12-1pm local time
    recurrence: RecurrencePattern = RecurrencePattern.NONE
    related_scope_items: list[str] = Field(default_factory=list)
    agenda_items: list[str] = Field(default_factory=list)
    pre_read_url: str = ""
    meeting_gate_approved: bool = False


class ScheduleResult(BaseModel):
    """Result of a smart scheduling operation."""
    request_title: str = ""
    recommended_slots: list[AvailabilitySlot] = Field(default_factory=list)
    best_slot: AvailabilitySlot | None = None
    scheduling_notes: list[str] = Field(default_factory=list)
    attendee_optimization: dict[str, str] = Field(default_factory=dict)
    created_event_id: str = ""
    calendar_link: str = ""


class CalendarBlock(BaseModel):
    """A focus time or hold block on someone's calendar."""
    person: str
    title: str
    start: datetime
    end: datetime
    block_type: str = "focus_time"  # focus_time | hold | tentative
    reason: str = ""
    created_by: str = "TPLLM"


class MeetingReschedule(BaseModel):
    """A request to reschedule an existing meeting."""
    event_id: str
    reason: str
    new_slots: list[AvailabilitySlot] = Field(default_factory=list)
    notify_attendees: bool = True
    message: str = ""
