"""Zoom tools — meeting tracking and AI notes ingestion."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.workflows.task_board_engine import TaskBoardEngine
from lltpm.workflows.zoom_engine import ZoomEngine


def get_zoom_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    engine = ZoomEngine(store)
    task_engine = TaskBoardEngine(store)

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

        # Auto-extract tasks from the ingested notes onto the task board
        extracted_tasks = task_engine.extract_tasks_from_zoom_notes(
            program_id,
            meeting_id=ingestion.meeting_id,
            raw_notes=raw_notes,
            action_items=action_items,
            key_decisions=key_decisions,
            follow_ups=follow_ups,
            meeting_topic=summary or ingestion.meeting_id,
        )

        return json.dumps({
            "meeting_id": ingestion.meeting_id,
            "provider": ingestion.provider.value,
            "sentiment": ingestion.sentiment,
            "action_items_count": len(ingestion.action_items),
            "decisions_count": len(ingestion.key_decisions),
            "tasks_extracted": len(extracted_tasks),
            "task_ids": [t.id for t in extracted_tasks],
            "message": (
                f"Notes ingested and {len(extracted_tasks)} task(s) auto-extracted to the task board. "
                "Use get_zoom_analysis_prompt for deeper analysis."
            ),
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
                "Fireflies, Fathom, or manual entry. Extracts structured data for analysis "
                "and automatically creates tasks on the task board from action items, "
                "follow-ups, implicit commitments, and key decisions."
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
