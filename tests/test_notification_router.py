"""Tests for Notification Router."""

import tempfile
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.notifications import (
    NotificationCategory,
    NotificationChannel,
    NotificationPriority,
)
from lltpm.workflows.notification_router import NotificationRouter


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_send_notification():
    store = _make_store()
    router = NotificationRouter(store)

    n = router.send(
        "alice", "Test", "Test body",
        NotificationCategory.GENERAL,
        NotificationPriority.HIGH,
    )

    assert n.recipient == "alice"
    assert n.channel == NotificationChannel.SLACK  # default first channel


def test_batches_low_priority():
    store = _make_store()
    router = NotificationRouter(store)

    n = router.send(
        "alice", "Low priority", "Info",
        NotificationCategory.STATUS_UPDATE,
        NotificationPriority.LOW,
    )

    assert n.batched is True


def test_immediate_not_batched():
    store = _make_store()
    router = NotificationRouter(store)

    n = router.send(
        "alice", "Urgent", "P0 blocker",
        NotificationCategory.ESCALATION,
        NotificationPriority.IMMEDIATE,
    )

    assert n.is_sent


def test_out_of_office_routes_to_delegate():
    store = _make_store()
    router = NotificationRouter(store)

    # Set alice OOO with bob as delegate
    router.set_out_of_office("alice", ooo=True, delegate="bob")

    n = router.send(
        "alice", "For Alice", "But she's OOO",
        NotificationCategory.DECISION_NEEDED,
        NotificationPriority.HIGH,
    )

    # Should be routed to bob
    assert n.recipient == "bob"


def test_get_preferences_default():
    store = _make_store()
    router = NotificationRouter(store)

    prefs = router.get_preferences("new_person")

    assert prefs.person == "new_person"
    assert len(prefs.channels) > 0
    assert prefs.quiet_hours is not None


def test_set_and_get_preferences():
    store = _make_store()
    router = NotificationRouter(store)

    prefs = router.get_preferences("alice")
    prefs.max_notifications_per_day = 5
    router.set_preferences("alice", prefs)

    loaded = router.get_preferences("alice")
    assert loaded.max_notifications_per_day == 5


def test_generate_digest():
    store = _make_store()
    router = NotificationRouter(store)

    # Send several low-priority notifications that get batched
    for i in range(3):
        router.send(
            "alice", f"Update {i}", f"Body {i}",
            NotificationCategory.STATUS_UPDATE,
            NotificationPriority.LOW,
        )

    digest = router.generate_digest("alice")

    assert digest is not None
    assert len(digest.notifications) == 3


def test_empty_digest():
    store = _make_store()
    router = NotificationRouter(store)

    digest = router.generate_digest("alice")
    assert digest is None


def test_notification_history():
    store = _make_store()
    router = NotificationRouter(store)

    router.send("alice", "First", "Body")
    router.send("alice", "Second", "Body")

    history = router.get_notification_history("alice")
    assert len(history) == 2


def test_notification_summary():
    store = _make_store()
    router = NotificationRouter(store)

    router.send("alice", "Test", "Body", NotificationCategory.NUDGE)
    summary = router.get_summary("alice")

    assert "Notifications" in summary
    assert "alice" in summary
