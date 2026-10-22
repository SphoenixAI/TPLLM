"""Webhook tools — real-time event ingestion and processing."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.task_board_engine import TaskBoardEngine
from tpllm.workflows.webhook_processor import WebhookProcessor


def get_webhook_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    processor = WebhookProcessor(store)
    task_engine = TaskBoardEngine(store)

    def ingest_slack_event(
        program_id: str,
        event_type: str,
        user: str = "",
        channel: str = "",
        text: str = "",
        thread_ts: str = "",
    ) -> str:
        event = processor.ingest_slack_event(
            program_id, event_type, user, channel, text, thread_ts,
        )
        actions = processor.get_event_actions(event)
        return json.dumps({
            "event_id": event.id,
            "priority": event.priority.value,
            "recommended_actions": actions,
        })

    def ingest_gchat_event(
        program_id: str,
        event_type: str,
        user: str = "",
        space: str = "",
        text: str = "",
    ) -> str:
        event = processor.ingest_gchat_event(
            program_id, event_type, user, space, text,
        )
        actions = processor.get_event_actions(event)
        return json.dumps({
            "event_id": event.id,
            "priority": event.priority.value,
            "recommended_actions": actions,
        })

    def ingest_github_event(
        program_id: str,
        event_type: str,
        actor: str = "",
        repo: str = "",
        title: str = "",
        body: str = "",
    ) -> str:
        event = processor.ingest_github_event(
            program_id, event_type, actor, repo, title, body,
        )
        actions = processor.get_event_actions(event)
        return json.dumps({
            "event_id": event.id,
            "priority": event.priority.value,
            "recommended_actions": actions,
        })

    def ingest_zoom_event(
        program_id: str,
        event_type: str,
        meeting_id: str = "",
        host: str = "",
    ) -> str:
        event = processor.ingest_zoom_event(
            program_id, event_type, meeting_id, host,
        )
        actions = processor.get_event_actions(event)
        result: dict[str, Any] = {
            "event_id": event.id,
            "priority": event.priority.value,
            "recommended_actions": actions,
        }

        # Auto-trigger: when a meeting ends or recording completes,
        # check for ingested notes and extract tasks to the board
        if event_type in ("meeting.ended", "recording.completed") and meeting_id:
            from tpllm.workflows.zoom_engine import ZoomEngine
            zoom = ZoomEngine(store)
            ingestion = zoom._load_ingestion(program_id, meeting_id)
            if ingestion:
                extracted = task_engine.extract_tasks_from_zoom_notes(
                    program_id,
                    meeting_id=meeting_id,
                    raw_notes=ingestion.raw_notes,
                    action_items=ingestion.action_items,
                    key_decisions=ingestion.key_decisions,
                    follow_ups=ingestion.follow_ups,
                    meeting_topic=ingestion.summary or meeting_id,
                )
                result["tasks_auto_extracted"] = len(extracted)
                result["task_ids"] = [t.id for t in extracted]

        return json.dumps(result)

    def get_pending_events(
        program_id: str,
        source: str = "",
        priority: str = "",
    ) -> str:
        events = processor.get_pending_events(program_id, source, priority)
        return json.dumps({
            "pending_count": len(events),
            "events": [
                {
                    "id": e.id,
                    "source": e.source.value,
                    "type": e.event_type,
                    "priority": e.priority.value,
                    "actor": e.actor,
                    "subject": e.subject,
                    "timestamp": str(e.timestamp),
                }
                for e in events[:20]
            ],
        })

    def mark_event_processed(
        program_id: str,
        event_id: str,
        actions_taken: list[str] | None = None,
    ) -> str:
        success = processor.mark_processed(program_id, event_id, actions_taken)
        return json.dumps({"success": success, "event_id": event_id})

    def get_webhook_summary(program_id: str) -> str:
        return processor.get_summary(program_id)

    return [
        {
            "name": "ingest_slack_event",
            "description": "Ingest a real-time Slack webhook event for processing.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "event_type": {"type": "string"},
                    "user": {"type": "string"},
                    "channel": {"type": "string"},
                    "text": {"type": "string"},
                    "thread_ts": {"type": "string"},
                },
                "required": ["program_id", "event_type"],
            },
            "executor": ingest_slack_event,
        },
        {
            "name": "ingest_gchat_event",
            "description": "Ingest a real-time Google Chat webhook event for processing.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "event_type": {"type": "string"},
                    "user": {"type": "string"},
                    "space": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["program_id", "event_type"],
            },
            "executor": ingest_gchat_event,
        },
        {
            "name": "ingest_github_event",
            "description": "Ingest a GitHub webhook event (PR, issue, comment, etc.).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "event_type": {"type": "string"},
                    "actor": {"type": "string"},
                    "repo": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["program_id", "event_type"],
            },
            "executor": ingest_github_event,
        },
        {
            "name": "ingest_zoom_event",
            "description": "Ingest a Zoom webhook event (meeting started/ended, recording complete).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "event_type": {"type": "string"},
                    "meeting_id": {"type": "string"},
                    "host": {"type": "string"},
                },
                "required": ["program_id", "event_type"],
            },
            "executor": ingest_zoom_event,
        },
        {
            "name": "get_pending_events",
            "description": "Get pending webhook events, optionally filtered by source and priority.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "source": {"type": "string", "enum": ["slack", "gchat", "zoom", "github", "jira", "linear"]},
                    "priority": {"type": "string", "enum": ["immediate", "high", "normal", "low"]},
                },
                "required": ["program_id"],
            },
            "executor": get_pending_events,
        },
        {
            "name": "mark_event_processed",
            "description": "Mark a webhook event as processed with actions taken.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "event_id": {"type": "string"},
                    "actions_taken": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["program_id", "event_id"],
            },
            "executor": mark_event_processed,
        },
        {
            "name": "get_webhook_summary",
            "description": "Get a summary of webhook event processing state.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": get_webhook_summary,
        },
    ]
