"""Models for AI meeting intelligence — extracted context from meetings."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class UrgencyLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SentimentSignal(str, Enum):
    """Detected sentiment / energy level from meeting context."""

    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    CONCERNED = "CONCERNED"
    FRUSTRATED = "FRUSTRATED"
    BLOCKED = "BLOCKED"


class TopicCategory(str, Enum):
    """Classification of what a meeting topic is about."""

    DECISION = "DECISION"
    STATUS = "STATUS"
    BLOCKER = "BLOCKER"
    PLANNING = "PLANNING"
    RISK = "RISK"
    DEPENDENCY = "DEPENDENCY"
    ESCALATION = "ESCALATION"
    CELEBRATION = "CELEBRATION"
    OTHER = "OTHER"


class ExtractedCommitment(BaseModel):
    """A commitment or promise extracted from meeting context."""

    owner: str
    description: str
    due_date: Optional[date] = None
    confidence: float = 0.8  # 0.0-1.0 how confident we are in the extraction
    source_quote: str = ""  # the raw text this was extracted from


class ExtractedRisk(BaseModel):
    """A risk signal extracted from meeting context."""

    title: str
    description: str
    severity: UrgencyLevel = UrgencyLevel.MEDIUM
    mentioned_by: str = ""
    source_quote: str = ""


class ExtractedDecision(BaseModel):
    """A decision (or pending decision) extracted from meeting context."""

    title: str
    decision_text: str = ""  # empty if still pending
    decided_by: str = ""
    is_final: bool = False
    options_discussed: list[str] = Field(default_factory=list)
    source_quote: str = ""


class ExtractedDependency(BaseModel):
    """A dependency or cross-team need extracted from meeting context."""

    title: str
    upstream: str = ""  # team or person providing
    downstream: str = ""  # team or person needing
    target_date: Optional[date] = None
    source_quote: str = ""


class ExtractedTimeline(BaseModel):
    """A timeline mention or deadline extracted from meeting context."""

    milestone: str
    target_date: Optional[date] = None
    date_text: str = ""  # raw date reference (e.g. "end of Q2")
    confidence: float = 0.8
    is_slip: bool = False  # was this a revised/slipped date?
    original_date: Optional[date] = None


class TopicInsight(BaseModel):
    """Analysis of a single topic discussed in a meeting."""

    topic: str
    category: TopicCategory = TopicCategory.OTHER
    time_spent_minutes: float = 0.0
    urgency: UrgencyLevel = UrgencyLevel.MEDIUM
    sentiment: SentimentSignal = SentimentSignal.NEUTRAL
    key_points: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    value_signal: str = ""  # why this topic matters to the program


class MeetingIntelligence(BaseModel):
    """The full intelligence extraction from a single meeting.

    This is the output of running AI analysis on raw meeting notes,
    transcripts, or AI meeting tool exports.
    """

    meeting_id: str = ""
    meeting_title: str = ""
    meeting_date: date = Field(default_factory=date.today)
    source: str = ""  # e.g. "Google Meet transcript", "Otter.ai notes", "manual"
    program_id: str = ""
    attendees: list[str] = Field(default_factory=list)
    duration_minutes: float = 0.0

    # Extracted intelligence
    executive_summary: str = ""
    topics: list[TopicInsight] = Field(default_factory=list)
    commitments: list[ExtractedCommitment] = Field(default_factory=list)
    risks: list[ExtractedRisk] = Field(default_factory=list)
    decisions: list[ExtractedDecision] = Field(default_factory=list)
    dependencies: list[ExtractedDependency] = Field(default_factory=list)
    timelines: list[ExtractedTimeline] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    # Computed assessment
    overall_urgency: UrgencyLevel = UrgencyLevel.MEDIUM
    meeting_effectiveness: str = ""  # was this meeting worth having?
    async_alternative: str = ""  # could this have been async?

    created_at: datetime = Field(default_factory=datetime.utcnow)

    def summary(self) -> str:
        lines = [
            f"# Meeting Intelligence — {self.meeting_title}",
            f"Date: {self.meeting_date.isoformat()} | Source: {self.source} | "
            f"Duration: {self.duration_minutes:.0f}m | Urgency: {self.overall_urgency.value}",
        ]
        if self.executive_summary:
            lines.append(f"\n**Summary**: {self.executive_summary}")

        if self.commitments:
            lines.append(f"\n## Commitments ({len(self.commitments)})")
            for c in self.commitments:
                due = c.due_date.isoformat() if c.due_date else "TBD"
                lines.append(f"- **{c.owner}**: {c.description} — Due: {due}")

        if self.decisions:
            lines.append(f"\n## Decisions ({len(self.decisions)})")
            for d in self.decisions:
                status = "DECIDED" if d.is_final else "PENDING"
                lines.append(f"- [{status}] {d.title}: {d.decision_text or 'Awaiting decision'}")

        if self.risks:
            lines.append(f"\n## Risks ({len(self.risks)})")
            for r in self.risks:
                lines.append(f"- [{r.severity.value}] {r.title}: {r.description}")

        if self.dependencies:
            lines.append(f"\n## Dependencies ({len(self.dependencies)})")
            for dep in self.dependencies:
                lines.append(f"- {dep.title}: {dep.upstream} → {dep.downstream}")

        if self.timelines:
            lines.append(f"\n## Timelines ({len(self.timelines)})")
            for t in self.timelines:
                slip = " (SLIPPED)" if t.is_slip else ""
                date_str = t.target_date.isoformat() if t.target_date else t.date_text
                lines.append(f"- {t.milestone}: {date_str}{slip}")

        if self.open_questions:
            lines.append(f"\n## Open Questions ({len(self.open_questions)})")
            for q in self.open_questions:
                lines.append(f"- {q}")

        if self.meeting_effectiveness:
            lines.append(f"\n**Effectiveness**: {self.meeting_effectiveness}")
        if self.async_alternative:
            lines.append(f"**Could be async**: {self.async_alternative}")

        return "\n".join(lines)
