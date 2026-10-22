"""Tests for Zoom Integration Engine."""

import tempfile
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.zoom import ZoomMeetingStatus, ZoomWebhookEvent
from tpllm.workflows.zoom_engine import ZoomEngine


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_register_meeting():
    store = _make_store()
    engine = ZoomEngine(store)

    meeting = engine.register_meeting(
        "test-prog",
        topic="Sprint Planning",
        host="Alice",
        participants=["Bob", "Charlie"],
        duration_minutes=30,
    )

    assert meeting.meeting_id.startswith("ZM-")
    assert meeting.topic == "Sprint Planning"
    assert meeting.host == "Alice"
    assert len(meeting.participants) == 2


def test_ingest_ai_notes():
    store = _make_store()
    engine = ZoomEngine(store)

    ingestion = engine.ingest_ai_notes(
        "test-prog",
        meeting_id="ZM-001",
        provider="zoom_ai",
        raw_notes="Alice agreed to finish auth by March 1. Bob will review the API spec.",
        summary="Sprint planning discussion about auth migration and API review.",
        action_items=[
            {"owner": "Alice", "description": "Finish auth migration"},
            {"owner": "Bob", "description": "Review API spec"},
        ],
        key_decisions=["Auth migration uses OAuth2"],
    )

    assert ingestion.meeting_id == "ZM-001"
    assert ingestion.provider.value == "zoom_ai"
    assert len(ingestion.action_items) == 2
    assert ingestion.sentiment != ""


def test_meeting_analysis_prompt():
    store = _make_store()
    engine = ZoomEngine(store)

    engine.ingest_ai_notes(
        "test-prog",
        meeting_id="ZM-002",
        provider="otter",
        raw_notes="Discussion about API rate limits.",
        summary="API rate limit discussion",
        action_items=[{"owner": "Charlie", "description": "Research rate limit options"}],
    )

    prompt = engine.get_meeting_analysis_prompt("test-prog", "ZM-002")
    assert "API rate limit" in prompt
    assert "Charlie" in prompt
    assert "Your Task" in prompt


def test_missing_meeting_prompt():
    store = _make_store()
    engine = ZoomEngine(store)

    prompt = engine.get_meeting_analysis_prompt("test-prog", "nonexistent")
    assert "No ingested notes found" in prompt


def test_process_meeting_ended_webhook():
    store = _make_store()
    engine = ZoomEngine(store)

    engine.register_meeting("test-prog", "Daily Standup", meeting_id="ZM-003")

    event = ZoomWebhookEvent(
        event_type="meeting.ended",
        meeting_id="ZM-003",
    )

    result = engine.process_webhook("test-prog", event)
    assert result["event_type"] == "meeting.ended"
    assert any("ENDED" in a for a in result["actions"])


def test_process_recording_webhook():
    store = _make_store()
    engine = ZoomEngine(store)

    engine.register_meeting("test-prog", "Design Review", meeting_id="ZM-004")

    event = ZoomWebhookEvent(
        event_type="recording.completed",
        meeting_id="ZM-004",
        payload={
            "recording_url": "https://zoom.us/rec/123",
            "transcript_url": "https://zoom.us/trans/123",
        },
    )

    result = engine.process_webhook("test-prog", event)
    assert "Recording URL captured" in result["actions"]


def test_get_meetings_by_status():
    store = _make_store()
    engine = ZoomEngine(store)

    engine.register_meeting("test-prog", "Meeting A")
    engine.register_meeting("test-prog", "Meeting B")

    scheduled = engine.get_meetings("test-prog", status="scheduled")
    assert len(scheduled) == 2

    ended = engine.get_meetings("test-prog", status="ended")
    assert len(ended) == 0


def test_sentiment_detection():
    store = _make_store()
    engine = ZoomEngine(store)

    # Positive notes
    pos = engine.ingest_ai_notes(
        "test-prog", raw_notes="Great progress! Everything is on track and ahead of schedule."
    )
    assert pos.sentiment == "positive"

    # Negative notes
    neg = engine.ingest_ai_notes(
        "test-prog", raw_notes="We're blocked and slipping. The risk is very high."
    )
    assert neg.sentiment == "negative"

    # Mixed notes
    mixed = engine.ingest_ai_notes(
        "test-prog", raw_notes="Great progress on auth, but blocked on API. Risk of delay."
    )
    assert mixed.sentiment == "mixed"


def test_zoom_summary():
    store = _make_store()
    engine = ZoomEngine(store)

    engine.register_meeting("test-prog", "Meeting 1")
    engine.register_meeting("test-prog", "Meeting 2")

    summary = engine.get_summary("test-prog")
    assert "Zoom Meeting Summary" in summary
    assert "Total meetings tracked: 2" in summary
