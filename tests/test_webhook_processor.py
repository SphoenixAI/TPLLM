"""Tests for Webhook Processor."""

import tempfile
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.webhook import EventPriority, WebhookSource
from lltpm.workflows.webhook_processor import WebhookProcessor


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_ingest_slack_event():
    store = _make_store()
    processor = WebhookProcessor(store)

    event = processor.ingest_slack_event(
        "test-prog",
        event_type="message",
        user="Alice",
        channel="#engineering",
        text="Just pushed the auth fix",
    )

    assert event.id.startswith("EVT-")
    assert event.source == WebhookSource.SLACK
    assert event.actor == "Alice"
    assert event.priority == EventPriority.NORMAL


def test_slack_urgent_priority():
    store = _make_store()
    processor = WebhookProcessor(store)

    event = processor.ingest_slack_event(
        "test-prog",
        event_type="message",
        text="We're blocked on the API. This is urgent.",
    )

    assert event.priority == EventPriority.IMMEDIATE


def test_ingest_gchat_event():
    store = _make_store()
    processor = WebhookProcessor(store)

    event = processor.ingest_gchat_event(
        "test-prog",
        event_type="message",
        user="Bob",
        space="spaces/123",
        text="Status update: auth migration at 80%",
    )

    assert event.source == WebhookSource.GCHAT
    assert event.priority == EventPriority.HIGH  # contains "update"


def test_ingest_github_event():
    store = _make_store()
    processor = WebhookProcessor(store)

    event = processor.ingest_github_event(
        "test-prog",
        event_type="pull_request.merged",
        actor="Alice",
        repo="company/auth-service",
        title="Implement OAuth2 flow",
    )

    assert event.source == WebhookSource.GITHUB
    assert event.priority == EventPriority.HIGH


def test_ingest_zoom_event():
    store = _make_store()
    processor = WebhookProcessor(store)

    event = processor.ingest_zoom_event(
        "test-prog",
        event_type="recording.completed",
        meeting_id="ZM-123",
        host="Alice",
    )

    assert event.source == WebhookSource.ZOOM
    assert event.priority == EventPriority.HIGH


def test_get_pending_events():
    store = _make_store()
    processor = WebhookProcessor(store)

    processor.ingest_slack_event("test-prog", "message", text="Hello")
    processor.ingest_github_event("test-prog", "pull_request.merged")

    pending = processor.get_pending_events("test-prog")
    assert len(pending) == 2
    assert all(not e.processed for e in pending)


def test_filter_by_source():
    store = _make_store()
    processor = WebhookProcessor(store)

    processor.ingest_slack_event("test-prog", "message", text="Hi")
    processor.ingest_github_event("test-prog", "issue.opened")

    slack_only = processor.get_pending_events("test-prog", source="slack")
    assert len(slack_only) == 1
    assert slack_only[0].source == WebhookSource.SLACK


def test_filter_by_priority():
    store = _make_store()
    processor = WebhookProcessor(store)

    processor.ingest_slack_event("test-prog", "message", text="blocked urgent")
    processor.ingest_slack_event("test-prog", "message", text="just an FYI")

    immediate = processor.get_pending_events("test-prog", priority="immediate")
    assert len(immediate) == 1
    assert immediate[0].priority == EventPriority.IMMEDIATE


def test_mark_processed():
    store = _make_store()
    processor = WebhookProcessor(store)

    event = processor.ingest_slack_event("test-prog", "message", text="Test")
    assert processor.mark_processed("test-prog", event.id, ["Acknowledged"])

    pending = processor.get_pending_events("test-prog")
    assert len(pending) == 0


def test_create_batch():
    store = _make_store()
    processor = WebhookProcessor(store)

    processor.ingest_slack_event("test-prog", "message", text="A")
    processor.ingest_slack_event("test-prog", "message", text="B")

    batch = processor.create_batch("test-prog")
    assert batch.batch_id.startswith("BATCH-")
    assert len(batch.events) == 2


def test_event_actions_slack_blocked():
    store = _make_store()
    processor = WebhookProcessor(store)

    event = processor.ingest_slack_event("test-prog", "message", text="I'm blocked on auth")
    actions = processor.get_event_actions(event)
    assert any("blocker" in a.lower() for a in actions)


def test_event_actions_github_merged():
    store = _make_store()
    processor = WebhookProcessor(store)

    event = processor.ingest_github_event("test-prog", "pull_request.merged")
    actions = processor.get_event_actions(event)
    assert any("delegation" in a.lower() for a in actions)


def test_webhook_summary():
    store = _make_store()
    processor = WebhookProcessor(store)

    processor.ingest_slack_event("test-prog", "message", text="Test")
    processor.ingest_github_event("test-prog", "issue.opened")

    summary = processor.get_summary("test-prog")
    assert "Webhook Events Summary" in summary
    assert "Pending: 2" in summary


def test_generic_event():
    store = _make_store()
    processor = WebhookProcessor(store)

    event = processor.ingest_generic_event(
        "test-prog",
        source="custom",
        event_type="custom_event",
        subject="Custom notification",
        priority="high",
    )

    assert event.source == WebhookSource.CUSTOM
    assert event.priority == EventPriority.HIGH
