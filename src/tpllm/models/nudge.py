"""Models for the Smart Nudge Engine — graduated follow-ups with engagement budgets.

The nudge system respects people's time while ensuring nothing falls through
the cracks. Every person has an engagement budget, and every nudge has a
clear escalation path: observe → remind → escalate.
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NudgeTier(str, Enum):
    """Escalation tiers for follow-ups."""

    OBSERVE = "OBSERVE"       # Tier 0: Track silently, no outreach
    GENTLE_REMIND = "GENTLE_REMIND"  # Tier 1: Friendly nudge with context
    FIRM_REMIND = "FIRM_REMIND"    # Tier 2: Direct reminder + impact statement
    ESCALATE = "ESCALATE"       # Tier 3: Loop in manager/lead with full context


class NudgeChannel(str, Enum):
    """Where the nudge is delivered."""

    SLACK = "SLACK"
    GCHAT = "GCHAT"
    EMAIL = "EMAIL"
    IN_TOOL = "IN_TOOL"  # In-app notification


class NudgeStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESPONDED = "RESPONDED"
    EXPIRED = "EXPIRED"
    ESCALATED = "ESCALATED"


class NudgePolicy(BaseModel):
    """Configurable policy for how nudges escalate.

    Timing is in hours after the original request was sent.
    """

    tier1_after_hours: float = 24.0    # Gentle reminder after 24h
    tier2_after_hours: float = 48.0    # Firm reminder after 48h
    tier3_after_hours: float = 72.0    # Escalation after 72h
    max_nudges_per_item: int = 3       # Don't nudge more than 3 times per item
    respect_ooo: bool = True           # Skip nudges for people marked OOO
    weekend_pause: bool = True         # Don't count weekends
    quiet_hours_start: int = 18        # No nudges after 6pm
    quiet_hours_end: int = 9           # No nudges before 9am


class EngagementBudget(BaseModel):
    """Per-person interaction budget to prevent over-pinging.

    The agent tracks how many interactions it has with each person
    and stays within budget. Higher-priority items consume more budget.
    """

    person: str
    daily_budget: int = 3          # Max interactions per day
    weekly_budget: int = 10        # Max interactions per week
    daily_spent: int = 0
    weekly_spent: int = 0
    last_reset_daily: date = Field(default_factory=date.today)
    last_reset_weekly: date = Field(default_factory=date.today)

    @property
    def daily_remaining(self) -> int:
        self._maybe_reset()
        return max(0, self.daily_budget - self.daily_spent)

    @property
    def weekly_remaining(self) -> int:
        self._maybe_reset()
        return max(0, self.weekly_budget - self.weekly_spent)

    @property
    def can_nudge(self) -> bool:
        self._maybe_reset()
        return self.daily_remaining > 0 and self.weekly_remaining > 0

    def spend(self, cost: int = 1) -> bool:
        """Attempt to spend interaction budget. Returns False if insufficient."""
        self._maybe_reset()
        if self.daily_spent + cost > self.daily_budget:
            return False
        if self.weekly_spent + cost > self.weekly_budget:
            return False
        self.daily_spent += cost
        self.weekly_spent += cost
        return True

    def _maybe_reset(self) -> None:
        today = date.today()
        if self.last_reset_daily < today:
            self.daily_spent = 0
            self.last_reset_daily = today
        # Reset weekly on Monday
        if (today - self.last_reset_weekly).days >= 7:
            self.weekly_spent = 0
            self.last_reset_weekly = today


class NudgeRecord(BaseModel):
    """A single nudge event — what was sent, to whom, and what happened."""

    id: str = ""
    target_person: str
    subject: str              # What we're nudging about
    source_type: str = ""     # e.g. "delegation", "action_item", "scope_review"
    source_id: str = ""       # Reference to the item being nudged about
    program_id: str = ""

    tier: NudgeTier = NudgeTier.OBSERVE
    channel: NudgeChannel = NudgeChannel.IN_TOOL
    status: NudgeStatus = NudgeStatus.PENDING

    # The actual message
    message: str = ""
    quick_response_options: list[str] = Field(default_factory=list)

    # Context for why this nudge matters
    why_it_matters: str = ""
    blocks: list[str] = Field(default_factory=list)  # What this blocks

    # Timing
    original_request_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None
    response: str = ""

    # Escalation
    escalate_to: str = ""     # Manager/lead to escalate to at tier 3
    nudge_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def hours_since_request(self) -> float:
        delta = datetime.utcnow() - self.original_request_at
        return delta.total_seconds() / 3600

    @property
    def is_stale(self) -> bool:
        return self.status in (NudgeStatus.PENDING, NudgeStatus.SENT) and self.hours_since_request > 72


class NudgeLog(BaseModel):
    """All nudges for a program."""

    program_id: str = ""
    records: list[NudgeRecord] = Field(default_factory=list)
    policies: dict[str, NudgePolicy] = Field(default_factory=dict)  # Per-person overrides
    budgets: dict[str, EngagementBudget] = Field(default_factory=dict)
    default_policy: NudgePolicy = Field(default_factory=NudgePolicy)

    @property
    def pending(self) -> list[NudgeRecord]:
        return [r for r in self.records if r.status in (NudgeStatus.PENDING, NudgeStatus.SENT)]

    @property
    def awaiting_response(self) -> list[NudgeRecord]:
        return [r for r in self.records if r.status == NudgeStatus.SENT]

    @property
    def response_rate(self) -> float:
        total = len([r for r in self.records if r.status != NudgeStatus.PENDING])
        responded = len([r for r in self.records if r.status == NudgeStatus.RESPONDED])
        return responded / total if total > 0 else 0.0
