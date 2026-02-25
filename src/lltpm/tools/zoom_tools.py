"""Zoom tools — meeting tracking and AI notes ingestion."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.workflows.zoom_engine import ZoomEngine


def get_zoom_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    engine = ZoomEngine(store)

    def register_zoom_meeting(
        program_id: str,
        topic: str,
        meeting_id: str = "",
        host: str = "",
        participants: list[str] | None = None,
        duration_minutes: int = 0,
    ) -> str:
        meeting = engine.register_meeting(
            program_id, topic, meeting_id, host, participants, duration_minutes=duration_minutes,
        )
        return json.dumps({
            "meeting_id": meeting.meeting_id,
            "topic": meeting.topic,
            "status": meeting.status.value,
        })

    def ingest_zoom_notes(
        program_id: str,
        meeting_id: str = "",
        provider: str = "manual",
        raw_notes: str = "",
        summary: str = "",
        action_items: list[dict[str, str]] | None = None,
        key_decisions: list[str] | None = None,
        topics_discussed: list[str] | None = None,
        follow_ups: list[dict[str, str]] | None = None,
    ) -> str:
        ingestion = engine.ingest_ai_notes(
            program_id, meeting_id, provider, raw_notes, summary,
            action_items, key_decisions, topics_discussed, follow_ups,
        )
        return json.dumps({
            "meeting_id": ingestion.meeting_id,
            "provider": ingestion.provider.value,
            "sentiment": ingestion.sentiment,
            "action_items_count": len(ingestion.action_items),
            "decisions_count": len(ingestion.key_decisions),
            "message": "Notes ingested. Use get_zoom_analysis_prompt to generate analysis.",
        })

    def get_zoom_analysis_prompt(
        program_id: str,
        meeting_id: str,
    ) -> str:
        return engine.get_meeting_analysis_prompt(program_id, meeting_id)

    def get_zoom_meetings(
        program_id: str,
        status: str = "",
    ) -> str:
        meetings = engine.get_meetings(program_id, status)
        return json.dumps({
            "meetings": [
                {
                    "meeting_id": m.meeting_id,
                    "topic": m.topic,
                    "host": m.host,
                    "status": m.status.value,
                    "participants": len(m.participants),
                    "has_ai_notes": m.has_ai_notes,
                }
                for m in meetings
            ]
        })

    def get_zoom_summary(program_id: str) -> str:
        return engine.get_summary(program_id)

    return [
        {
            "name": "register_zoom_meeting",
            "description": "Register a Zoom meeting for tracking. Use before or after the meeting.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "topic": {"type": "string"},
                    "meeting_id": {"type": "string"},
                    "host": {"type": "string"},
                    "participants": {"type": "array", "items": {"type": "string"}},
                    "duration_minutes": {"type": "integer"},
                },
                "required": ["program_id", "topic"],
            },
            "executor": register_zoom_meeting,
        },
        {
            "name": "ingest_zoom_notes",
            "description": (
                "Ingest AI-generated meeting notes from Zoom AI Companion, Otter.ai, "
                "Fireflies, Fathom, or manual entry. Extracts structured data for analysis."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "meeting_id": {"type": "string"},
                    "provider": {
                        "type": "string",
                        "enum": ["zoom_ai", "otter", "fireflies", "fathom", "granola", "claude", "manual"],
                    },
                    "raw_notes": {"type": "string"},
                    "summary": {"type": "string"},
                    "action_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "owner": {"type": "string"},
                                "description": {"type": "string"},
                            },
                        },
                    },
                    "key_decisions": {"type": "array", "items": {"type": "string"}},
                    "topics_discussed": {"type": "array", "items": {"type": "string"}},
                    "follow_ups": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "person": {"type": "string"},
                                "action": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["program_id"],
            },
            "executor": ingest_zoom_notes,
        },
        {
            "name": "get_zoom_analysis_prompt",
            "description": (
                "Generate an analysis prompt for ingested Zoom meeting notes. "
                "Use this to trigger meeting intelligence extraction."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "meeting_id": {"type": "string"},
                },
                "required": ["program_id", "meeting_id"],
            },
            "executor": get_zoom_analysis_prompt,
        },
        {
            "name": "get_zoom_meetings",
            "description": "List Zoom meetings, optionally filtered by status.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["scheduled", "started", "ended", "cancelled"]},
                },
                "required": ["program_id"],
            },
            "executor": get_zoom_meetings,
        },
        {
            "name": "get_zoom_summary",
            "description": "Get a summary of Zoom meeting tracking state.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": get_zoom_summary,
        },
    ]
