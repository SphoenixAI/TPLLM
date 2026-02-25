"""Notification Router — intelligent notification delivery.

Routes notifications through the optimal channel at the optimal time
based on per-person preferences, quiet hours, and priority.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, time

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.notifications import (
    Notification,
    NotificationCategory,
    NotificationChannel,
    NotificationDigest,
    NotificationPreference,
    NotificationPriority,
)


class NotificationRouter:
    """Route and manage notifications with per-person intelligence."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    # ── Core Routing ──────────────────────────────────────────

    def send(
        self,
        recipient: str,
        title: str,
        body: str,
        category: NotificationCategory = NotificationCategory.GENERAL,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        program_id: str = "",
    ) -> Notification:
        """Route a notification to the best channel for the recipient."""
        prefs = self.get_preferences(recipient)

        # Determine channel
        channel = prefs.get_channel_for(category, priority)
        if channel is None and prefs.out_of_office and prefs.ooo_delegate:
            # Re-route to delegate
            return self.send(
                prefs.ooo_delegate, title, body, category, priority, program_id,
            )

        notification = Notification(
            id=f"notif_{secrets.token_hex(4)}",
            recipient=recipient,
            category=category,
            priority=priority,
            title=title,
            body=body,
            channel=channel or NotificationChannel.IN_APP,
            program_id=program_id,
        )

        # Check if should batch or send immediately
        if (
            priority in (NotificationPriority.LOW, NotificationPriority.NORMAL)
            and prefs.batch_low_priority
            and not self._is_immediate(priority)
        ):
            notification.batched = True
            self._queue_for_digest(notification)
        else:
            # Check quiet hours
            if self._in_quiet_hours(prefs) and priority != NotificationPriority.IMMEDIATE:
                notification.batched = True
                self._queue_for_digest(notification)
            else:
                notification.sent_at = datetime.utcnow()
                self._deliver(notification)

        # Record
        self._record_notification(notification)
        return notification

    def generate_digest(
        self,
        recipient: str,
        period: str = "daily",
    ) -> NotificationDigest | None:
        """Generate a notification digest from queued notifications."""
        queued = self._get_queued(recipient)
        if not queued:
            return None

        digest = NotificationDigest(
            recipient=recipient,
            notifications=queued,
            period=period,
        )

        # Mark all as sent
        for n in queued:
            n.sent_at = datetime.utcnow()

        # Clear queue
        self._clear_queue(recipient)

        return digest

    # ── Preferences ───────────────────────────────────────────

    def get_preferences(self, person: str) -> NotificationPreference:
        """Load notification preferences for a person."""
        raw = self._store.load_raw("_system", f"notif_prefs_{person}")
        if raw:
            try:
                return NotificationPreference.model_validate(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                pass
        return NotificationPreference(person=person)

    def set_preferences(
        self,
        person: str,
        prefs: NotificationPreference,
    ) -> None:
        """Save notification preferences for a person."""
        prefs.updated_at = datetime.utcnow()
        self._store.save_raw(
            "_system", f"notif_prefs_{person}",
            prefs.model_dump_json(indent=2),
        )

    def set_out_of_office(
        self,
        person: str,
        ooo: bool = True,
        delegate: str = "",
    ) -> None:
        """Set a person's out-of-office status and delegate."""
        prefs = self.get_preferences(person)
        prefs.out_of_office = ooo
        prefs.ooo_delegate = delegate
        self.set_preferences(person, prefs)

    # ── Query ─────────────────────────────────────────────────

    def get_notification_history(
        self,
        recipient: str,
        limit: int = 50,
    ) -> list[Notification]:
        """Get recent notifications for a person."""
        history = self._load_history(recipient)
        return sorted(
            history, key=lambda n: n.created_at, reverse=True
        )[:limit]

    def get_summary(self, recipient: str) -> str:
        """Get a summary of notification activity for a person."""
        history = self.get_notification_history(recipient, limit=100)
        prefs = self.get_preferences(recipient)

        lines = [
            f"# Notifications — {recipient}",
            f"Total recent: {len(history)}",
            f"Preferred channels: {', '.join(c.channel.value for c in prefs.channels if c.enabled)}",
            f"OOO: {'Yes → ' + prefs.ooo_delegate if prefs.out_of_office else 'No'}",
        ]

        # By category
        by_cat: dict[str, int] = {}
        for n in history:
            by_cat[n.category.value] = by_cat.get(n.category.value, 0) + 1
        if by_cat:
            lines.append("\nBy category:")
            for cat, count in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  - {cat}: {count}")

        return "\n".join(lines)

    # ── Internal ──────────────────────────────────────────────

    def _is_immediate(self, priority: NotificationPriority) -> bool:
        return priority == NotificationPriority.IMMEDIATE

    def _in_quiet_hours(self, prefs: NotificationPreference) -> bool:
        """Check if current time is in the person's quiet hours."""
        now = datetime.utcnow().time()
        qh = prefs.quiet_hours

        # Handle overnight quiet hours (e.g., 22:00 to 08:00)
        if qh.start > qh.end:
            return now >= qh.start or now <= qh.end
        return qh.start <= now <= qh.end

    def _deliver(self, notification: Notification) -> None:
        """Deliver a notification through its channel.

        In production, this would call Slack API, SendGrid, etc.
        For now, we just record it as sent.
        """
        # Delivery stubs would go here for each channel:
        # - SLACK: slack_client.chat_postMessage(...)
        # - EMAIL: sendgrid.send(...)
        # - GCHAT: gchat_client.send(...)
        # - VOICE: tts_engine.speak(...)
        pass

    def _queue_for_digest(self, notification: Notification) -> None:
        """Add notification to the digest queue."""
        queued = self._get_queued_raw(notification.recipient)
        queued.append(notification.model_dump())
        self._store.save_raw(
            "_system", f"notif_queue_{notification.recipient}",
            json.dumps(queued, default=str),
        )

    def _get_queued(self, recipient: str) -> list[Notification]:
        raw_list = self._get_queued_raw(recipient)
        return [Notification.model_validate(n) for n in raw_list]

    def _get_queued_raw(self, recipient: str) -> list[dict]:
        raw = self._store.load_raw("_system", f"notif_queue_{recipient}")
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def _clear_queue(self, recipient: str) -> None:
        self._store.save_raw("_system", f"notif_queue_{recipient}", "[]")

    def _record_notification(self, notification: Notification) -> None:
        history = self._load_history_raw(notification.recipient)
        history.append(notification.model_dump())
        # Keep last 200
        history = history[-200:]
        self._store.save_raw(
            "_system", f"notif_history_{notification.recipient}",
            json.dumps(history, default=str),
        )

    def _load_history(self, recipient: str) -> list[Notification]:
        raw_list = self._load_history_raw(recipient)
        return [Notification.model_validate(n) for n in raw_list]

    def _load_history_raw(self, recipient: str) -> list[dict]:
        raw = self._store.load_raw("_system", f"notif_history_{recipient}")
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
