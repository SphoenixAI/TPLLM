"""Zoom Integration Engine — ingest AI meeting notes and extract intelligence.

Works with any AI note-taker (Zoom AI Companion, Otter.ai, Fireflies,
Fathom, etc.) to automatically:
1. Ingest meeting recordings and AI-generated notes
2. Extract commitments, decisions, risks, and action items
3. Feed extracted intelligence into the meeting intelligence system
4. Trigger delegation and nudge workflows
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.zoom import (
    AINotesIngestion,
    AINotesProvider,
    ZoomMeeting,
    ZoomMeetingStatus,
    ZoomParticipant,
    ZoomWebhookEvent,
)

ZOOM_ARTIFACT = "zoom_meetings"


class ZoomEngine:
    """Process Zoom meetings and AI-generated notes."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def register_meeting(
        self,
        program_id: str,
        topic: str,
        meeting_id: str = "",
        host: str = "",
        participants: list[str] | None = None,
        start_time: datetime | None = None,
        duration_minutes: int = 0,
    ) -> ZoomMeeting:
        """Register a Zoom meeting for tracking."""
        meeting = ZoomMeeting(
            meeting_id=meeting_id or f"ZM-{uuid.uuid4().hex[:8]}",
            topic=topic,
            host=host,
            start_time=start_time or datetime.utcnow(),
            duration_minutes=duration_minutes,
            status=ZoomMeetingStatus.SCHEDULED,
            participants=[
                ZoomParticipant(name=p) for p in (participants or [])
            ],
        )

        meetings = self._load_meetings(program_id)
        meetings.append(meeting)
        self._save_meetings(program_id, meetings)
        return meeting

    def ingest_ai_notes(
        self,
        program_id: str,
        meeting_id: str = "",
        provider: str = "manual",
        raw_notes: str = "",
        summary: str = "",
        action_items: list[dict[str, str]] | None = None,
        key_decisions: list[str] | None = None,
        topics_discussed: list[str] | None = None,
        follow_ups: list[dict[str, str]] | None = None,
    ) -> AINotesIngestion:
        """Ingest AI-generated meeting notes from any provider.

        Accepts notes from Zoom AI Companion, Otter.ai, Fireflies,
        Fathom, or manual entry. Extracts structured data and prepares
        it for the meeting intelligence pipeline.
        """
        try:
            prov = AINotesProvider(provider)
        except ValueError:
            prov = AINotesProvider.MANUAL

        ingestion = AINotesIngestion(
            meeting_id=meeting_id or f"ZM-{uuid.uuid4().hex[:8]}",
            provider=prov,
            raw_notes=raw_notes,
            summary=summary,
            action_items=action_items or [],
            key_decisions=key_decisions or [],
            topics_discussed=topics_discussed or [],
            follow_ups=follow_ups or [],
            program_id=program_id,
        )

        # Auto-detect sentiment from notes
        if raw_notes:
            ingestion.sentiment = self._detect_sentiment(raw_notes)

        # Persist the ingestion
        self._save_ingestion(program_id, ingestion)
        return ingestion

    def get_meeting_analysis_prompt(
        self,
        program_id: str,
        meeting_id: str,
    ) -> str:
        """Generate a prompt for the agent to analyze ingested meeting notes.

        The agent uses this prompt with its own reasoning to extract
        meeting intelligence, then feeds results back through
        the meeting intelligence engine.
        """
        ingestion = self._load_ingestion(program_id, meeting_id)
        if not ingestion:
            return f"No ingested notes found for meeting {meeting_id}"

        prompt_parts = [
            f"Analyze the following {ingestion.provider.value} meeting notes "
            f"for program '{program_id}'.",
            "",
            f"## Meeting: {meeting_id}",
        ]

        if ingestion.summary:
            prompt_parts.append(f"\n### AI-Generated Summary\n{ingestion.summary}")

        if ingestion.raw_notes:
            prompt_parts.append(f"\n### Full Notes\n{ingestion.raw_notes}")

        if ingestion.action_items:
            prompt_parts.append("\n### Action Items (from AI)")
            for item in ingestion.action_items:
                owner = item.get("owner", "Unassigned")
                desc = item.get("description", item.get("text", ""))
                prompt_parts.append(f"- [{owner}] {desc}")

        if ingestion.key_decisions:
            prompt_parts.append("\n### Key Decisions (from AI)")
            for dec in ingestion.key_decisions:
                prompt_parts.append(f"- {dec}")

        prompt_parts.extend([
            "",
            "## Your Task",
            "1. Extract ALL commitments (who committed to what, by when)",
            "2. Identify risks and dependencies mentioned",
            "3. Flag any vague timelines or missing owners",
            "4. Note any decisions that need documentation",
            "5. Create delegation items for each commitment",
            "6. Flag anything that conflicts with existing program state",
            "",
            "Use ingest_parsed_meeting and delegate_from_meeting tools to process.",
        ])

        return "\n".join(prompt_parts)

    def process_webhook(
        self,
        program_id: str,
        event: ZoomWebhookEvent,
    ) -> dict:
        """Process a Zoom webhook event.

        Handles events like meeting.ended, recording.completed,
        and triggers appropriate workflows.
        """
        result = {
            "event_type": event.event_type,
            "meeting_id": event.meeting_id,
            "actions": [],
        }

        if event.event_type == "meeting.ended":
            # Update meeting status
            meetings = self._load_meetings(program_id)
            for m in meetings:
                if m.meeting_id == event.meeting_id:
                    m.status = ZoomMeetingStatus.ENDED
                    m.end_time = event.timestamp
                    break
            self._save_meetings(program_id, meetings)
            result["actions"].append("Updated meeting status to ENDED")
            result["actions"].append("Ready for AI notes ingestion")

        elif event.event_type == "recording.completed":
            meetings = self._load_meetings(program_id)
            for m in meetings:
                if m.meeting_id == event.meeting_id:
                    m.recording_url = event.payload.get("recording_url", "")
                    m.transcript_url = event.payload.get("transcript_url", "")
                    break
            self._save_meetings(program_id, meetings)
            result["actions"].append("Recording URL captured")
            if event.payload.get("transcript_url"):
                result["actions"].append("Transcript available — queue for analysis")

        return result

    def get_meetings(
        self,
        program_id: str,
        status: str = "",
    ) -> list[ZoomMeeting]:
        """Get meetings, optionally filtered by status."""
        meetings = self._load_meetings(program_id)
        if status:
            try:
                s = ZoomMeetingStatus(status)
                meetings = [m for m in meetings if m.status == s]
            except ValueError:
                pass
        return meetings

    def get_summary(self, program_id: str) -> str:
        """Get a summary of Zoom meeting state."""
        meetings = self._load_meetings(program_id)
        if not meetings:
            return "No Zoom meetings tracked."

        lines = [
            "# Zoom Meeting Summary",
            f"Total meetings tracked: {len(meetings)}",
        ]

        by_status = {}
        for m in meetings:
            by_status.setdefault(m.status.value, []).append(m)

        for status, ms in by_status.items():
            lines.append(f"  {status}: {len(ms)}")

        with_notes = [m for m in meetings if m.has_ai_notes]
        if with_notes:
            lines.append(f"  With AI notes: {len(with_notes)}")

        return "\n".join(lines)

    # ── Sentiment detection ───────────────────────────────────

    def _detect_sentiment(self, text: str) -> str:
        """Simple keyword-based sentiment detection."""
        text_lower = text.lower()
        positive = ["great", "excellent", "on track", "ahead", "done", "shipped", "resolved"]
        negative = ["blocked", "slipping", "risk", "concern", "behind", "failed", "issue"]

        pos_count = sum(1 for w in positive if w in text_lower)
        neg_count = sum(1 for w in negative if w in text_lower)

        if pos_count > neg_count * 2:
            return "positive"
        elif neg_count > pos_count * 2:
            return "negative"
        elif pos_count > 0 and neg_count > 0:
            return "mixed"
        return "neutral"

    # ── Persistence ───────────────────────────────────────────

    def _load_meetings(self, program_id: str) -> list[ZoomMeeting]:
        raw = self._store.load_raw(program_id, ZOOM_ARTIFACT)
        if raw is None:
            return []
        try:
            data = json.loads(raw)
            return [ZoomMeeting.model_validate(m) for m in data]
        except (json.JSONDecodeError, TypeError):
            return []

    def _save_meetings(self, program_id: str, meetings: list[ZoomMeeting]) -> None:
        data = [m.model_dump() for m in meetings]
        self._store.save_raw(program_id, ZOOM_ARTIFACT, json.dumps(data, indent=2, default=str))

    def _save_ingestion(self, program_id: str, ingestion: AINotesIngestion) -> None:
        name = f"zoom_notes_{ingestion.meeting_id}"
        self._store.save_raw(
            program_id, name,
            json.dumps(ingestion.model_dump(), indent=2, default=str),
        )

    def _load_ingestion(self, program_id: str, meeting_id: str) -> AINotesIngestion | None:
        name = f"zoom_notes_{meeting_id}"
        raw = self._store.load_raw(program_id, name)
        if raw is None:
            return None
        try:
            return AINotesIngestion.model_validate(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return None
