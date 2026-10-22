"""Notification preference and routing models."""

from __future__ import annotations

from datetime import time, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NotificationChannel(str, Enum):
    """Channels through which notifications can be delivered."""

    SLACK = "slack"
    EMAIL = "email"
    GCHAT = "gchat"
    SMS = "sms"
    IN_APP = "in_app"
    VOICE = "voice"


class NotificationPriority(str, Enum):
    IMMEDIATE = "immediate"  # send now regardless of preferences
    HIGH = "high"  # send soon, respect quiet hours
    NORMAL = "normal"  # batch-eligible
    LOW = "low"  # batch only, daily digest


class NotificationCategory(str, Enum):
    """Categories for notification routing rules."""

    STATUS_UPDATE = "status_update"
    RISK_ALERT = "risk_alert"
    DECISION_NEEDED = "decision_needed"
    APPROVAL_REQUEST = "approval_request"
    NUDGE = "nudge"
    ESCALATION = "escalation"
    DELEGATION = "delegation"
    MILESTONE = "milestone"
    DIGEST = "digest"
    MEETING = "meeting"
    GENERAL = "general"


class QuietHours(BaseModel):
    """Time window during which notifications should not be sent."""

    start: time = time(22, 0)  # 10 PM
    end: time = time(8, 0)  # 8 AM
    timezone: str = "UTC"
    weekends_quiet: bool = True  # no notifications on weekends
    holidays: list[str] = Field(default_factory=list)  # ISO date strings


class ChannelPreference(BaseModel):
    """Preference for a specific notification channel."""

    channel: NotificationChannel
    enabled: bool = True
    priority_threshold: NotificationPriority = NotificationPriority.NORMAL
    categories: list[NotificationCategory] = Field(default_factory=list)


class NotificationPreference(BaseModel):
    """Per-person notification preferences."""

    person: str
    email: str = ""
    slack_id: str = ""
    gchat_id: str = ""
    phone: str = ""

    # Channel preferences (ordered by priority — first match wins)
    channels: list[ChannelPreference] = Field(default_factory=lambda: [
        ChannelPreference(
            channel=NotificationChannel.SLACK,
            categories=[
                NotificationCategory.NUDGE,
                NotificationCategory.DECISION_NEEDED,
                NotificationCategory.DELEGATION,
                NotificationCategory.GENERAL,
            ],
        ),
        ChannelPreference(
            channel=NotificationChannel.EMAIL,
            categories=[
                NotificationCategory.STATUS_UPDATE,
                NotificationCategory.DIGEST,
                NotificationCategory.APPROVAL_REQUEST,
            ],
        ),
    ])

    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    batch_low_priority: bool = True  # batch LOW priority into digests
    max_notifications_per_day: int = 15
    out_of_office: bool = False
    ooo_delegate: str = ""  # who receives notifications when OOO
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def get_channel_for(
        self,
        category: NotificationCategory,
        priority: NotificationPriority,
    ) -> NotificationChannel | None:
        """Determine the best channel for a given notification."""
        if self.out_of_office:
            return None  # route to delegate instead

        for pref in self.channels:
            if not pref.enabled:
                continue
            # Check if this channel handles this category
            if pref.categories and category not in pref.categories:
                continue
            # Check priority threshold
            priority_order = list(NotificationPriority)
            if priority_order.index(priority) > priority_order.index(pref.priority_threshold):
                continue
            return pref.channel

        # Fallback to first enabled channel
        for pref in self.channels:
            if pref.enabled:
                return pref.channel
        return NotificationChannel.IN_APP


class Notification(BaseModel):
    """A queued notification."""

    id: str = ""
    recipient: str
    category: NotificationCategory = NotificationCategory.GENERAL
    priority: NotificationPriority = NotificationPriority.NORMAL
    title: str = ""
    body: str = ""
    channel: NotificationChannel = NotificationChannel.IN_APP
    program_id: str = ""
    action_url: str = ""

    # State
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    batched: bool = False  # part of a digest

    @property
    def is_sent(self) -> bool:
        return self.sent_at is not None


class NotificationDigest(BaseModel):
    """A batched collection of notifications sent as one message."""

    recipient: str
    notifications: list[Notification] = Field(default_factory=list)
    period: str = "daily"  # daily, weekly
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    def render(self) -> str:
        lines = [f"# Notification Digest ({self.period})"]
        by_category: dict[str, list[Notification]] = {}
        for n in self.notifications:
            by_category.setdefault(n.category.value, []).append(n)

        for cat, notifs in by_category.items():
            lines.append(f"\n## {cat.replace('_', ' ').title()} ({len(notifs)})")
            for n in notifs:
                lines.append(f"- **{n.title}**: {n.body}")
        return "\n".join(lines)
