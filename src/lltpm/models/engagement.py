"""Outreach routing — how to reach someone, and when to expect a reply.

This model answers a logistics question: which channel reaches this
person, what time of day they are reachable, whether they act on
one-click options, and how long a reply typically takes so a plan can
allow for it. Response latency is a routing and scheduling fact of the
same kind as "this approval queue takes three days".

**What was removed, and why.** This module used to compute
a ``ResponsivenessGrade`` — HIGHLY_RESPONSIVE, RESPONSIVE, SLOW,
UNRELIABLE — from a per-person response rate and ignored-message count,
and the outreach recommendation built on it escalated: "Bob has a 30%
response rate; loop in their lead for important requests."

That is performance management, not routing. It also reads a structural
cause as a personal quality: somebody copied on forty requests a week
across four programs responds to a smaller fraction of them, and the
finding there is about how requests are distributed, not about them.

So there is no grade, no response rate and no ignored count. What
remains cannot be used to rank anyone: a latency estimate and the
number of observations behind it, which is a property of the estimate
rather than of the person.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PreferredChannel(str, Enum):
    SLACK = "SLACK"
    GCHAT = "GCHAT"
    EMAIL = "EMAIL"
    PR_COMMENT = "PR_COMMENT"
    TICKET = "TICKET"


class ResponseEvent(BaseModel):
    """One observed reply, and how it arrived.

    A request that was never answered contributes no observation. It is
    deliberately not counted as a non-response against anyone: the
    reason is unknowable from here, and counting it is how a latency
    estimate turns into a reliability score.
    """

    nudge_id: str = ""
    request_type: str = ""     # "status_update", "decision", "review", etc.
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    responded_at: Optional[datetime] = None
    response_hours: Optional[float] = None
    channel: PreferredChannel = PreferredChannel.SLACK
    had_quick_options: bool = False
    used_quick_option: bool = False


class EngagementProfile(BaseModel):
    """Where to send something, and how long to allow for a reply."""

    person: str
    program_id: str = ""

    # Observed latency, and how many observations it rests on.
    avg_response_hours: float = 24.0
    median_response_hours: float = 24.0
    observed_responses: int = 0

    # Routing preferences, learned from what worked.
    preferred_channel: PreferredChannel = PreferredChannel.SLACK
    best_response_window: str = "9am-12pm"  # When replies usually arrive
    prefers_quick_options: bool = True       # Do they use one-click options?
    prefers_batched: bool = False            # Do they prefer all items at once?

    # Tracking
    events: list[ResponseEvent] = Field(default_factory=list)
    last_interaction: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def has_observations(self) -> bool:
        """Whether the latency estimate rests on anything.

        Callers should say "no observations yet" rather than quoting the
        default as though it had been measured.
        """
        return self.observed_responses > 0

    def record_response(self, event: ResponseEvent) -> None:
        """Record an observed reply and update the latency estimate."""
        self.events.append(event)
        if event.responded_at and event.response_hours is not None:
            self.observed_responses += 1
            # Running mean over observations, not over requests sent.
            self.avg_response_hours = (
                (
                    self.avg_response_hours * (self.observed_responses - 1)
                    + event.response_hours
                ) / self.observed_responses
            )
            self.median_response_hours = self._median_latency()

        if event.used_quick_option:
            self.prefers_quick_options = True

        self.last_interaction = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def _median_latency(self) -> float:
        samples = sorted(
            event.response_hours for event in self.events
            if event.response_hours is not None
        )
        if not samples:
            return self.median_response_hours
        middle = len(samples) // 2
        if len(samples) % 2:
            return samples[middle]
        return (samples[middle - 1] + samples[middle]) / 2

    def summary(self) -> str:
        if not self.has_observations:
            return (
                f"**{self.person}** — no replies observed yet\n"
                f"  Channel: {self.preferred_channel.value} | "
                f"Window: {self.best_response_window}"
            )
        return (
            f"**{self.person}** — replies in ~"
            f"{self.median_response_hours:.1f}h "
            f"({self.observed_responses} observation(s))\n"
            f"  Best window: {self.best_response_window} | "
            f"Channel: {self.preferred_channel.value} | "
            f"Quick options: {'yes' if self.prefers_quick_options else 'no'}"
        )
