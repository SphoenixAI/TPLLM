"""Zoom integration models — meeting data, AI notes, and recording artifacts.

Works with company-integrated AI note-takers (Zoom AI Companion,
Otter.ai, Fathom, Fireflies, etc.) to extract meeting intelligence.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ZoomMeetingStatus(str, Enum):
    SCHEDULED = "scheduled"
    STARTED = "started"
    ENDED = "ended"
    CANCELLED = "cancelled"


class AINotesProvider(str, Enum):
    """AI note-taking providers that can be integrated."""
    ZOOM_AI = "zoom_ai"           # Zoom AI Companion
    OTTER = "otter"               # Otter.ai
    FIREFLIES = "fireflies"       # Fireflies.ai
    FATHOM = "fathom"             # Fathom
    GRANOLA = "granola"           # Granola
    CLAUDE = "claude"             # Claude-based (internal)
    MANUAL = "manual"             # Manually entered


class ZoomParticipant(BaseModel):
    """A participant in a Zoom meeting."""
    name: str
    email: str = ""
    join_time: datetime | None = None
    leave_time: datetime | None = None
    duration_minutes: int = 0
    role: str = "attendee"  # host | co-host | attendee


class ZoomMeeting(BaseModel):
    """Metadata about a Zoom meeting."""
    meeting_id: str = ""
    topic: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_minutes: int = 0
    host: str = ""
    participants: list[ZoomParticipant] = Field(default_factory=list)
    status: ZoomMeetingStatus = ZoomMeetingStatus.SCHEDULED
    recording_url: str = ""
    transcript_url: str = ""
    has_ai_notes: bool = False
    ai_notes_provider: AINotesProvider = AINotesProvider.MANUAL


class AINotesIngestion(BaseModel):
    """Ingested AI-generated meeting notes from any provider."""
    meeting_id: str = ""
    provider: AINotesProvider = AINotesProvider.MANUAL
    raw_notes: str = ""
    summary: str = ""
    action_items: list[dict[str, str]] = Field(default_factory=list)
    key_decisions: list[str] = Field(default_factory=list)
    topics_discussed: list[str] = Field(default_factory=list)
    follow_ups: list[dict[str, str]] = Field(default_factory=list)
    sentiment: str = ""  # positive | neutral | negative | mixed
    ingested_at: datetime = Field(default_factory=datetime.utcnow)
    program_id: str = ""


class ZoomWebhookEvent(BaseModel):
    """A webhook event from Zoom."""
    event_type: str = ""  # meeting.started, meeting.ended, recording.completed, etc.
    meeting_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
