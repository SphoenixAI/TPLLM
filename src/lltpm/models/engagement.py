"""Models for Engagement Scoring — track responsiveness and optimize outreach.

Not punitive. Used to understand *when* and *how* each person prefers
to be reached, so the agent can adapt its communication style.
"""

from __future__ import annotations

from datetime import datetime, date, time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ResponsivenessGrade(str, Enum):
    """How responsive someone typically is."""

    HIGHLY_RESPONSIVE = "HIGHLY_RESPONSIVE"  # Usually responds within hours
    RESPONSIVE = "RESPONSIVE"                 # Usually responds within a day
    SLOW = "SLOW"                            # Takes 2-3 days
    UNRELIABLE = "UNRELIABLE"                # Often doesn't respond


class PreferredChannel(str, Enum):
    SLACK = "SLACK"
    GCHAT = "GCHAT"
    EMAIL = "EMAIL"
    PR_COMMENT = "PR_COMMENT"
    TICKET = "TICKET"


class ResponseEvent(BaseModel):
    """A single response (or non-response) event for scoring."""

    nudge_id: str = ""
    request_type: str = ""     # "status_update", "decision", "review", etc.
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    responded_at: Optional[datetime] = None
    response_hours: Optional[float] = None
    channel: PreferredChannel = PreferredChannel.SLACK
    had_quick_options: bool = False
    used_quick_option: bool = False
    ignored: bool = False


class EngagementProfile(BaseModel):
    """Per-person engagement profile built from observed behavior.

    The agent uses this to optimize *when*, *how*, and *what* to send.
    """

    person: str
    program_id: str = ""

    # Observed patterns
    avg_response_hours: float = 24.0
    median_response_hours: float = 24.0
    response_rate: float = 1.0      # 0.0-1.0
    grade: ResponsivenessGrade = ResponsivenessGrade.RESPONSIVE

    # Preferences (learned from behavior)
    preferred_channel: PreferredChannel = PreferredChannel.SLACK
    best_response_window: str = "9am-12pm"  # When they typically respond fastest
    prefers_quick_options: bool = True       # Do they use one-click options?
    prefers_batched: bool = False            # Do they prefer all items at once?

    # History
    total_requests: int = 0
    total_responses: int = 0
    total_ignored: int = 0
    events: list[ResponseEvent] = Field(default_factory=list)

    # Tracking
    last_interaction: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def record_response(self, event: ResponseEvent) -> None:
        """Record a response event and update scoring."""
        self.events.append(event)
        self.total_requests += 1
        if event.responded_at:
            self.total_responses += 1
            if event.response_hours is not None:
                # Update running average
                self.avg_response_hours = (
                    (self.avg_response_hours * (self.total_responses - 1) + event.response_hours)
                    / self.total_responses
                )
        elif event.ignored:
            self.total_ignored += 1

        # Update rate
        self.response_rate = self.total_responses / self.total_requests if self.total_requests > 0 else 1.0

        # Update grade
        self._update_grade()

        # Update preference detection
        if event.used_quick_option:
            self.prefers_quick_options = True

        self.last_interaction = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def _update_grade(self) -> None:
        if self.response_rate < 0.5:
            self.grade = ResponsivenessGrade.UNRELIABLE
        elif self.avg_response_hours > 48:
            self.grade = ResponsivenessGrade.SLOW
        elif self.avg_response_hours > 12:
            self.grade = ResponsivenessGrade.RESPONSIVE
        else:
            self.grade = ResponsivenessGrade.HIGHLY_RESPONSIVE

    def summary(self) -> str:
        return (
            f"**{self.person}** — {self.grade.value}\n"
            f"  Avg response: {self.avg_response_hours:.1f}h | "
            f"Rate: {self.response_rate:.0%} | "
            f"Best window: {self.best_response_window} | "
            f"Channel: {self.preferred_channel.value} | "
            f"Quick options: {'yes' if self.prefers_quick_options else 'no'}"
        )
