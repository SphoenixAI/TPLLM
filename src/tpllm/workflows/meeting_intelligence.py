"""Meeting Intelligence engine — extract actionable context from meetings.

Takes raw meeting notes, transcripts, or AI meeting tool exports and
produces structured intelligence: commitments, risks, decisions,
timelines, dependencies, and urgency assessments.

Works with any meeting source:
- Google Meet transcripts
- Otter.ai / Fireflies.ai exports
- Manual notes
- Any text-based meeting record

The engine uses the configured LLM to perform deep analysis of meeting
content and extract structured data that feeds into all other TPM
artifacts (RAID log, action tracker, milestone plan, decision log).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid4

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.meeting_intel import (
    ExtractedCommitment,
    ExtractedDecision,
    ExtractedDependency,
    ExtractedRisk,
    ExtractedTimeline,
    MeetingIntelligence,
    TopicCategory,
    TopicInsight,
    UrgencyLevel,
    SentimentSignal,
)


MEETING_INTEL_ARTIFACT = "meeting_intelligence"

# The prompt template for the model to analyze meeting content
MEETING_ANALYSIS_PROMPT = """\
You are analyzing meeting notes/transcript for a Technical Program Manager.
Extract ALL actionable intelligence from this meeting content.

Meeting title: {title}
Meeting date: {meeting_date}
Program: {program_id}
Attendees: {attendees}

---
MEETING CONTENT:
{content}
---

Extract the following as a JSON object. Be thorough — capture every
commitment, risk, decision, dependency, and timeline mention:

{{
  "executive_summary": "2-3 sentence summary of what happened",
  "overall_urgency": "LOW|MEDIUM|HIGH|CRITICAL",
  "meeting_effectiveness": "Was this meeting productive? Could it have been async?",
  "async_alternative": "If this could have been async, describe how",
  "duration_minutes": <estimated duration in minutes>,
  "topics": [
    {{
      "topic": "topic name",
      "category": "DECISION|STATUS|BLOCKER|PLANNING|RISK|DEPENDENCY|ESCALATION|CELEBRATION|OTHER",
      "time_spent_minutes": <estimated minutes>,
      "urgency": "LOW|MEDIUM|HIGH|CRITICAL",
      "sentiment": "POSITIVE|NEUTRAL|CONCERNED|FRUSTRATED|BLOCKED",
      "key_points": ["point 1", "point 2"],
      "open_questions": ["question 1"],
      "value_signal": "why this topic matters"
    }}
  ],
  "commitments": [
    {{
      "owner": "person name",
      "description": "what they committed to",
      "due_date": "YYYY-MM-DD or null",
      "confidence": 0.8,
      "source_quote": "relevant quote from the meeting"
    }}
  ],
  "risks": [
    {{
      "title": "risk title",
      "description": "risk detail",
      "severity": "LOW|MEDIUM|HIGH|CRITICAL",
      "mentioned_by": "person name",
      "source_quote": "relevant quote"
    }}
  ],
  "decisions": [
    {{
      "title": "decision title",
      "decision_text": "what was decided (empty if pending)",
      "decided_by": "person name",
      "is_final": true,
      "options_discussed": ["option A", "option B"],
      "source_quote": "relevant quote"
    }}
  ],
  "dependencies": [
    {{
      "title": "dependency name",
      "upstream": "providing team/person",
      "downstream": "needing team/person",
      "target_date": "YYYY-MM-DD or null",
      "source_quote": "relevant quote"
    }}
  ],
  "timelines": [
    {{
      "milestone": "milestone name",
      "target_date": "YYYY-MM-DD or null",
      "date_text": "raw date reference",
      "confidence": 0.8,
      "is_slip": false,
      "original_date": "YYYY-MM-DD or null"
    }}
  ],
  "open_questions": ["question that wasn't resolved"]
}}

Return ONLY valid JSON. Be precise about dates, owners, and commitments.
If something is implied but not explicit, note it with lower confidence.
"""


@dataclass
class IntelligenceReport:
    """Summary of intelligence extracted across multiple meetings."""

    program_id: str
    meetings_analyzed: int = 0
    total_commitments: int = 0
    total_risks: int = 0
    total_decisions_pending: int = 0
    total_decisions_made: int = 0
    total_dependencies: int = 0
    timeline_slips: int = 0
    unresolved_questions: list[str] = field(default_factory=list)
    high_urgency_items: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"# Meeting Intelligence Report — {self.program_id}",
            f"Meetings analyzed: {self.meetings_analyzed}",
            f"Commitments: {self.total_commitments} | Risks: {self.total_risks} | "
            f"Decisions: {self.total_decisions_made} made, {self.total_decisions_pending} pending",
            f"Dependencies: {self.total_dependencies} | Timeline slips: {self.timeline_slips}",
        ]
        if self.high_urgency_items:
            lines.append("\n## High-Urgency Items")
            for item in self.high_urgency_items:
                lines.append(f"- {item}")
        if self.unresolved_questions:
            lines.append(f"\n## Unresolved Questions ({len(self.unresolved_questions)})")
            for q in self.unresolved_questions:
                lines.append(f"- {q}")
        return "\n".join(lines)


class MeetingIntelligenceEngine:
    """Engine that extracts structured intelligence from raw meeting content.

    Two modes of operation:
    1. **AI-powered**: Pass raw meeting text through the configured model to extract
       structured intelligence (requires provider configuration).
    2. **Manual/pre-parsed**: Accept pre-structured meeting data from
       AI meeting tools (Otter.ai, Fireflies, etc.) that already
       provide structured output.
    """

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def analyze_meeting(
        self,
        program_id: str,
        title: str,
        content: str,
        attendees: list[str] | None = None,
        meeting_date: str | None = None,
        source: str = "manual",
    ) -> MeetingIntelligence:
        """Analyze raw meeting content and extract intelligence.

        This method prepares the meeting for AI analysis. The actual
        LLM API call is made by the agent — the analysis prompt is
        returned as part of the tool result so the agent can process it
        in its own agentic loop.

        For pre-parsed data from AI meeting tools, use ingest_parsed() instead.
        """
        meeting_id = f"MI-{uuid4().hex[:8]}"
        m_date = date.fromisoformat(meeting_date) if meeting_date else date.today()

        # Build the analysis prompt that the agent will process
        prompt = MEETING_ANALYSIS_PROMPT.format(
            title=title,
            meeting_date=m_date.isoformat(),
            program_id=program_id,
            attendees=", ".join(attendees or []),
            content=content,
        )

        # Create a skeleton intelligence record
        intel = MeetingIntelligence(
            meeting_id=meeting_id,
            meeting_title=title,
            meeting_date=m_date,
            source=source,
            program_id=program_id,
            attendees=attendees or [],
        )

        # Save the raw content for reference
        self._store.save_raw(program_id, f"meeting_raw_{meeting_id}", content)
        # Save the skeleton (will be updated after analysis)
        self._save_intel(program_id, intel)

        return intel, prompt

    def ingest_parsed(
        self,
        program_id: str,
        title: str,
        parsed_data: dict[str, Any],
        attendees: list[str] | None = None,
        meeting_date: str | None = None,
        source: str = "ai_tool",
    ) -> MeetingIntelligence:
        """Ingest pre-parsed meeting data from an AI meeting tool.

        Accepts structured data (e.g. from Otter.ai, Fireflies, Google Meet
        AI notes) and converts it into our MeetingIntelligence format.
        """
        meeting_id = f"MI-{uuid4().hex[:8]}"
        m_date = date.fromisoformat(meeting_date) if meeting_date else date.today()

        intel = self._build_from_parsed(meeting_id, title, m_date, program_id, attendees, source, parsed_data)

        self._save_intel(program_id, intel)
        return intel

    def update_from_analysis(
        self,
        program_id: str,
        meeting_id: str,
        analysis: dict[str, Any],
    ) -> MeetingIntelligence:
        """Update a meeting intelligence record with AI analysis results.

        Called after the agent has processed the analysis prompt and
        extracted the structured JSON.
        """
        intel = self._load_intel(program_id, meeting_id)
        if intel is None:
            # Create new if not found
            intel = MeetingIntelligence(meeting_id=meeting_id, program_id=program_id)

        intel = self._apply_analysis(intel, analysis)
        self._save_intel(program_id, intel)
        return intel

    def get_intelligence(self, program_id: str, meeting_id: str) -> MeetingIntelligence | None:
        """Get the intelligence for a specific meeting."""
        return self._load_intel(program_id, meeting_id)

    def get_program_report(self, program_id: str) -> IntelligenceReport:
        """Generate an aggregate intelligence report across all meetings."""
        report = IntelligenceReport(program_id=program_id)
        index = self._load_index(program_id)

        for meeting_id in index:
            intel = self._load_intel(program_id, meeting_id)
            if intel is None:
                continue
            report.meetings_analyzed += 1
            report.total_commitments += len(intel.commitments)
            report.total_risks += len(intel.risks)
            report.total_decisions_pending += len([d for d in intel.decisions if not d.is_final])
            report.total_decisions_made += len([d for d in intel.decisions if d.is_final])
            report.total_dependencies += len(intel.dependencies)
            report.timeline_slips += len([t for t in intel.timelines if t.is_slip])
            report.unresolved_questions.extend(intel.open_questions)

            # Collect high-urgency items
            if intel.overall_urgency in (UrgencyLevel.HIGH, UrgencyLevel.CRITICAL):
                report.high_urgency_items.append(
                    f"[{intel.meeting_date.isoformat()}] {intel.meeting_title}: {intel.executive_summary[:100]}"
                )
            for risk in intel.risks:
                if risk.severity in (UrgencyLevel.HIGH, UrgencyLevel.CRITICAL):
                    report.high_urgency_items.append(f"[RISK] {risk.title}: {risk.description[:80]}")

        return report

    def extract_all_commitments(self, program_id: str) -> list[dict[str, Any]]:
        """Get all commitments across all meetings for a program."""
        commitments = []
        index = self._load_index(program_id)
        for meeting_id in index:
            intel = self._load_intel(program_id, meeting_id)
            if intel is None:
                continue
            for c in intel.commitments:
                commitments.append({
                    "meeting_id": meeting_id,
                    "meeting_title": intel.meeting_title,
                    "meeting_date": intel.meeting_date.isoformat(),
                    "owner": c.owner,
                    "description": c.description,
                    "due_date": c.due_date.isoformat() if c.due_date else None,
                    "confidence": c.confidence,
                })
        return commitments

    def get_analysis_prompt(
        self,
        program_id: str,
        title: str,
        content: str,
        attendees: list[str] | None = None,
        meeting_date: str | None = None,
    ) -> str:
        """Get the analysis prompt without creating a record.

        Useful for re-analysis or when the caller handles persistence.
        """
        m_date = meeting_date or date.today().isoformat()
        return MEETING_ANALYSIS_PROMPT.format(
            title=title,
            meeting_date=m_date,
            program_id=program_id,
            attendees=", ".join(attendees or []),
            content=content,
        )

    # ── internal helpers ──────────────────────────────────────

    def _build_from_parsed(
        self,
        meeting_id: str,
        title: str,
        meeting_date: date,
        program_id: str,
        attendees: list[str] | None,
        source: str,
        data: dict[str, Any],
    ) -> MeetingIntelligence:
        """Build MeetingIntelligence from pre-parsed data."""
        intel = MeetingIntelligence(
            meeting_id=meeting_id,
            meeting_title=title,
            meeting_date=meeting_date,
            source=source,
            program_id=program_id,
            attendees=attendees or [],
        )
        return self._apply_analysis(intel, data)

    def _apply_analysis(self, intel: MeetingIntelligence, data: dict[str, Any]) -> MeetingIntelligence:
        """Apply analysis data to a MeetingIntelligence record."""
        intel.executive_summary = data.get("executive_summary", "")
        intel.duration_minutes = data.get("duration_minutes", 0)
        intel.meeting_effectiveness = data.get("meeting_effectiveness", "")
        intel.async_alternative = data.get("async_alternative", "")
        intel.open_questions = data.get("open_questions", [])

        # Parse urgency
        urgency_str = data.get("overall_urgency", "MEDIUM")
        try:
            intel.overall_urgency = UrgencyLevel(urgency_str)
        except ValueError:
            intel.overall_urgency = UrgencyLevel.MEDIUM

        # Parse topics
        for t in data.get("topics", []):
            try:
                category = TopicCategory(t.get("category", "OTHER"))
            except ValueError:
                category = TopicCategory.OTHER
            try:
                sentiment = SentimentSignal(t.get("sentiment", "NEUTRAL"))
            except ValueError:
                sentiment = SentimentSignal.NEUTRAL
            try:
                urgency = UrgencyLevel(t.get("urgency", "MEDIUM"))
            except ValueError:
                urgency = UrgencyLevel.MEDIUM

            intel.topics.append(TopicInsight(
                topic=t.get("topic", ""),
                category=category,
                time_spent_minutes=t.get("time_spent_minutes", 0),
                urgency=urgency,
                sentiment=sentiment,
                key_points=t.get("key_points", []),
                open_questions=t.get("open_questions", []),
                value_signal=t.get("value_signal", ""),
            ))

        # Parse commitments
        for c in data.get("commitments", []):
            due = None
            if c.get("due_date"):
                try:
                    due = date.fromisoformat(c["due_date"])
                except (ValueError, TypeError):
                    pass
            intel.commitments.append(ExtractedCommitment(
                owner=c.get("owner", ""),
                description=c.get("description", ""),
                due_date=due,
                confidence=c.get("confidence", 0.8),
                source_quote=c.get("source_quote", ""),
            ))

        # Parse risks
        for r in data.get("risks", []):
            try:
                severity = UrgencyLevel(r.get("severity", "MEDIUM"))
            except ValueError:
                severity = UrgencyLevel.MEDIUM
            intel.risks.append(ExtractedRisk(
                title=r.get("title", ""),
                description=r.get("description", ""),
                severity=severity,
                mentioned_by=r.get("mentioned_by", ""),
                source_quote=r.get("source_quote", ""),
            ))

        # Parse decisions
        for d in data.get("decisions", []):
            intel.decisions.append(ExtractedDecision(
                title=d.get("title", ""),
                decision_text=d.get("decision_text", ""),
                decided_by=d.get("decided_by", ""),
                is_final=d.get("is_final", False),
                options_discussed=d.get("options_discussed", []),
                source_quote=d.get("source_quote", ""),
            ))

        # Parse dependencies
        for dep in data.get("dependencies", []):
            target = None
            if dep.get("target_date"):
                try:
                    target = date.fromisoformat(dep["target_date"])
                except (ValueError, TypeError):
                    pass
            intel.dependencies.append(ExtractedDependency(
                title=dep.get("title", ""),
                upstream=dep.get("upstream", ""),
                downstream=dep.get("downstream", ""),
                target_date=target,
                source_quote=dep.get("source_quote", ""),
            ))

        # Parse timelines
        for t in data.get("timelines", []):
            target = None
            if t.get("target_date"):
                try:
                    target = date.fromisoformat(t["target_date"])
                except (ValueError, TypeError):
                    pass
            orig = None
            if t.get("original_date"):
                try:
                    orig = date.fromisoformat(t["original_date"])
                except (ValueError, TypeError):
                    pass
            intel.timelines.append(ExtractedTimeline(
                milestone=t.get("milestone", ""),
                target_date=target,
                date_text=t.get("date_text", ""),
                confidence=t.get("confidence", 0.8),
                is_slip=t.get("is_slip", False),
                original_date=orig,
            ))

        return intel

    def _save_intel(self, program_id: str, intel: MeetingIntelligence) -> None:
        """Save meeting intelligence and update the index."""
        self._store.save(program_id, f"meeting_intel_{intel.meeting_id}", intel)
        # Update index
        index = self._load_index(program_id)
        if intel.meeting_id not in index:
            index.append(intel.meeting_id)
            self._store.save_raw(program_id, "meeting_intel_index", json.dumps(index))

    def _load_intel(self, program_id: str, meeting_id: str) -> MeetingIntelligence | None:
        return self._store.load(program_id, f"meeting_intel_{meeting_id}", MeetingIntelligence)

    def _load_index(self, program_id: str) -> list[str]:
        raw = self._store.load_raw(program_id, "meeting_intel_index")
        if raw is None:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
