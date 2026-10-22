"""Webhook Processor — real-time event ingestion from Slack, GChat, Zoom, GitHub.

Normalizes events from different sources into a unified format,
prioritizes them, and triggers appropriate agent workflows.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.webhook import (
    EventBatch,
    EventPriority,
    NormalizedEvent,
    WebhookConfig,
    WebhookSource,
)

WEBHOOK_ARTIFACT = "webhook_events"
WEBHOOK_CONFIG_ARTIFACT = "webhook_config"


class WebhookProcessor:
    """Process real-time webhook events from all integrated services."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    # ── Event ingestion ───────────────────────────────────────

    def ingest_slack_event(
        self,
        program_id: str,
        event_type: str,
        user: str = "",
        channel: str = "",
        text: str = "",
        thread_ts: str = "",
        metadata: dict | None = None,
    ) -> NormalizedEvent:
        """Ingest a Slack webhook event."""
        priority = self._classify_slack_priority(event_type, text)

        event = NormalizedEvent(
            id=f"EVT-{uuid.uuid4().hex[:8]}",
            source=WebhookSource.SLACK,
            event_type=event_type,
            priority=priority,
            actor=user,
            channel=channel,
            program_id=program_id,
            subject=f"Slack {event_type}",
            body=text,
            metadata={
                **(metadata or {}),
                "thread_ts": thread_ts,
            },
        )

        self._store_event(program_id, event)
        return event

    def ingest_gchat_event(
        self,
        program_id: str,
        event_type: str,
        user: str = "",
        space: str = "",
        text: str = "",
        metadata: dict | None = None,
    ) -> NormalizedEvent:
        """Ingest a Google Chat webhook event."""
        priority = self._classify_gchat_priority(event_type, text)

        event = NormalizedEvent(
            id=f"EVT-{uuid.uuid4().hex[:8]}",
            source=WebhookSource.GCHAT,
            event_type=event_type,
            priority=priority,
            actor=user,
            channel=space,
            program_id=program_id,
            subject=f"GChat {event_type}",
            body=text,
            metadata=metadata or {},
        )

        self._store_event(program_id, event)
        return event

    def ingest_github_event(
        self,
        program_id: str,
        event_type: str,
        actor: str = "",
        repo: str = "",
        title: str = "",
        body: str = "",
        metadata: dict | None = None,
    ) -> NormalizedEvent:
        """Ingest a GitHub webhook event (PR, issue, comment, etc.)."""
        priority = self._classify_github_priority(event_type, metadata or {})

        event = NormalizedEvent(
            id=f"EVT-{uuid.uuid4().hex[:8]}",
            source=WebhookSource.GITHUB,
            event_type=event_type,
            priority=priority,
            actor=actor,
            channel=repo,
            program_id=program_id,
            subject=title,
            body=body,
            metadata=metadata or {},
        )

        self._store_event(program_id, event)
        return event

    def ingest_zoom_event(
        self,
        program_id: str,
        event_type: str,
        meeting_id: str = "",
        host: str = "",
        metadata: dict | None = None,
    ) -> NormalizedEvent:
        """Ingest a Zoom webhook event."""
        priority = EventPriority.NORMAL
        if event_type == "meeting.started":
            priority = EventPriority.HIGH
        elif event_type == "recording.completed":
            priority = EventPriority.HIGH

        event = NormalizedEvent(
            id=f"EVT-{uuid.uuid4().hex[:8]}",
            source=WebhookSource.ZOOM,
            event_type=event_type,
            priority=priority,
            actor=host,
            channel=meeting_id,
            program_id=program_id,
            subject=f"Zoom {event_type}",
            body=f"Meeting {meeting_id}: {event_type}",
            metadata=metadata or {},
        )

        self._store_event(program_id, event)
        return event

    def ingest_generic_event(
        self,
        program_id: str,
        source: str,
        event_type: str,
        actor: str = "",
        channel: str = "",
        subject: str = "",
        body: str = "",
        priority: str = "normal",
        metadata: dict | None = None,
    ) -> NormalizedEvent:
        """Ingest an event from any source."""
        try:
            src = WebhookSource(source)
        except ValueError:
            src = WebhookSource.CUSTOM

        try:
            pri = EventPriority(priority)
        except ValueError:
            pri = EventPriority.NORMAL

        event = NormalizedEvent(
            id=f"EVT-{uuid.uuid4().hex[:8]}",
            source=src,
            event_type=event_type,
            priority=pri,
            actor=actor,
            channel=channel,
            program_id=program_id,
            subject=subject,
            body=body,
            metadata=metadata or {},
        )

        self._store_event(program_id, event)
        return event

    # ── Event processing ──────────────────────────────────────

    def get_pending_events(
        self,
        program_id: str,
        source: str = "",
        priority: str = "",
    ) -> list[NormalizedEvent]:
        """Get unprocessed events, optionally filtered."""
        events = self._load_events(program_id)
        pending = [e for e in events if not e.processed]

        if source:
            try:
                src = WebhookSource(source)
                pending = [e for e in pending if e.source == src]
            except ValueError:
                pass

        if priority:
            try:
                pri = EventPriority(priority)
                pending = [e for e in pending if e.priority == pri]
            except ValueError:
                pass

        return sorted(pending, key=lambda e: (
            list(EventPriority).index(e.priority),
            e.timestamp,
        ))

    def mark_processed(
        self,
        program_id: str,
        event_id: str,
        actions_taken: list[str] | None = None,
    ) -> bool:
        """Mark an event as processed."""
        events = self._load_events(program_id)
        for event in events:
            if event.id == event_id:
                event.processed = True
                event.processed_at = datetime.utcnow()
                event.actions_taken = actions_taken or []
                self._save_events(program_id, events)
                return True
        return False

    def create_batch(self, program_id: str) -> EventBatch:
        """Create a batch of pending events for processing."""
        pending = self.get_pending_events(program_id)
        return EventBatch(
            events=pending,
            batch_id=f"BATCH-{uuid.uuid4().hex[:8]}",
        )

    def get_event_actions(self, event: NormalizedEvent) -> list[str]:
        """Determine what actions should be taken for an event.

        Returns a list of recommended agent actions.
        """
        actions = []

        if event.source == WebhookSource.SLACK:
            if event.event_type == "message" and "blocked" in event.body.lower():
                actions.append("Check RAID log for related blockers")
                actions.append("Create nudge for resolution")
            elif event.event_type == "reaction_added":
                actions.append("Record engagement response")
            elif event.event_type == "message" and event.metadata.get("thread_ts"):
                actions.append("Check if this is a response to a nudge")

        elif event.source == WebhookSource.GITHUB:
            if event.event_type == "pull_request.merged":
                actions.append("Update delegation status if related")
                actions.append("Check milestone progress")
            elif event.event_type == "issue.opened":
                actions.append("Triage new issue")
                actions.append("Check scope baseline for impact")

        elif event.source == WebhookSource.ZOOM:
            if event.event_type == "recording.completed":
                actions.append("Ingest AI notes when available")
                actions.append("Process through meeting intelligence")

        elif event.source == WebhookSource.GCHAT:
            if event.event_type == "message":
                actions.append("Check for quick response option selection")

        return actions

    def get_summary(self, program_id: str) -> str:
        """Get a summary of webhook event state."""
        events = self._load_events(program_id)
        if not events:
            return "No webhook events tracked."

        pending = [e for e in events if not e.processed]
        processed = [e for e in events if e.processed]

        lines = [
            "# Webhook Events Summary",
            f"Total events: {len(events)}",
            f"Pending: {len(pending)}",
            f"Processed: {len(processed)}",
        ]

        # By source
        by_source: dict[str, int] = {}
        for e in pending:
            by_source[e.source.value] = by_source.get(e.source.value, 0) + 1
        if by_source:
            lines.append("\nPending by source:")
            for src, count in sorted(by_source.items()):
                lines.append(f"  {src}: {count}")

        # Immediate priority
        immediate = [e for e in pending if e.priority == EventPriority.IMMEDIATE]
        if immediate:
            lines.append(f"\n⚠ {len(immediate)} IMMEDIATE priority events pending!")

        return "\n".join(lines)

    # ── Priority classification ───────────────────────────────

    def _classify_slack_priority(self, event_type: str, text: str) -> EventPriority:
        text_lower = text.lower()
        if any(w in text_lower for w in ["blocked", "urgent", "p0", "outage", "incident"]):
            return EventPriority.IMMEDIATE
        if any(w in text_lower for w in ["help", "question", "review", "p1"]):
            return EventPriority.HIGH
        if event_type in ("reaction_added", "channel_join"):
            return EventPriority.LOW
        return EventPriority.NORMAL

    def _classify_gchat_priority(self, event_type: str, text: str) -> EventPriority:
        text_lower = text.lower()
        if any(w in text_lower for w in ["blocked", "urgent", "critical"]):
            return EventPriority.IMMEDIATE
        if any(w in text_lower for w in ["update", "status", "question"]):
            return EventPriority.HIGH
        return EventPriority.NORMAL

    def _classify_github_priority(self, event_type: str, metadata: dict) -> EventPriority:
        if event_type == "pull_request.merged":
            return EventPriority.HIGH
        if event_type == "issue.opened" and metadata.get("labels"):
            labels = metadata["labels"]
            if any("p0" in l.lower() or "critical" in l.lower() for l in labels):
                return EventPriority.IMMEDIATE
        return EventPriority.NORMAL

    # ── Persistence ───────────────────────────────────────────

    def _store_event(self, program_id: str, event: NormalizedEvent) -> None:
        events = self._load_events(program_id)
        events.append(event)
        # Keep only last 500 events
        if len(events) > 500:
            events = events[-500:]
        self._save_events(program_id, events)

    def _load_events(self, program_id: str) -> list[NormalizedEvent]:
        raw = self._store.load_raw(program_id, WEBHOOK_ARTIFACT)
        if raw is None:
            return []
        try:
            data = json.loads(raw)
            return [NormalizedEvent.model_validate(e) for e in data]
        except (json.JSONDecodeError, TypeError):
            return []

    def _save_events(self, program_id: str, events: list[NormalizedEvent]) -> None:
        data = [e.model_dump() for e in events]
        self._store.save_raw(
            program_id, WEBHOOK_ARTIFACT,
            json.dumps(data, indent=2, default=str),
        )
