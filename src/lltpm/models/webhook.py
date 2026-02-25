"""Real-time webhook models — event ingestion from Slack, GChat, Zoom, GitHub.

Provides a unified event model that normalizes events from different
sources so the agent can process them through a single pipeline.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WebhookSource(str, Enum):
    SLACK = "slack"
    GCHAT = "gchat"
    ZOOM = "zoom"
    GITHUB = "github"
    JIRA = "jira"
    LINEAR = "linear"
    CALENDAR = "calendar"
    CUSTOM = "custom"


class EventPriority(str, Enum):
    IMMEDIATE = "immediate"  # process now (blocker reported, meeting starting)
    HIGH = "high"            # process within minutes
    NORMAL = "normal"        # process in next batch
    LOW = "low"              # process when convenient


class NormalizedEvent(BaseModel):
    """A normalized event from any webhook source.

    All incoming webhooks get converted to this format so the
    agent processes them through a single pipeline.
    """
    id: str = ""
    source: WebhookSource = WebhookSource.CUSTOM
    event_type: str = ""  # message, reaction, pr_merged, meeting_ended, etc.
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    priority: EventPriority = EventPriority.NORMAL

    # Who/what
    actor: str = ""        # who triggered the event
    channel: str = ""      # where it happened (channel, room, repo)
    program_id: str = ""   # which program this relates to

    # Content
    subject: str = ""
    body: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Processing state
    processed: bool = False
    processed_at: datetime | None = None
    actions_taken: list[str] = Field(default_factory=list)


class WebhookConfig(BaseModel):
    """Configuration for a webhook endpoint."""
    source: WebhookSource
    endpoint_path: str = ""
    secret: str = ""  # for signature verification
    enabled: bool = True
    event_types: list[str] = Field(default_factory=list)  # filter by event type
    program_mapping: dict[str, str] = Field(default_factory=dict)  # channel -> program_id


class EventBatch(BaseModel):
    """A batch of events to be processed together."""
    events: list[NormalizedEvent] = Field(default_factory=list)
    batch_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processed: bool = False

    @property
    def immediate_events(self) -> list[NormalizedEvent]:
        return [e for e in self.events if e.priority == EventPriority.IMMEDIATE]

    @property
    def high_priority_events(self) -> list[NormalizedEvent]:
        return [e for e in self.events if e.priority == EventPriority.HIGH]
