"""Tests for Persistent Memory Engine."""

import tempfile
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.workflows.persistent_memory import PersistentMemoryEngine


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_record_and_retrieve_turns():
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    engine.record_turn("test-prog", "user", "What is the status of the auth migration?")
    engine.record_turn("test-prog", "assistant", "The auth migration is on track for March 1.")

    turns = engine.get_recent_turns("test-prog", limit=5)
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[1].role == "assistant"


def test_search_turns():
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    engine.record_turn("test-prog", "user", "Auth migration update")
    engine.record_turn("test-prog", "assistant", "Beta release scheduled for March 15")
    engine.record_turn("test-prog", "user", "What about the API rate limits?")

    results = engine.search_turns("test-prog", "auth migration")
    assert len(results) == 1
    assert "auth" in results[0].content.lower()


def test_context_snapshot():
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    snapshot = engine.take_snapshot(
        "test-prog",
        summary="Sprint 3 midpoint. Auth migration on track.",
        active_milestones=["Beta", "Auth Migration"],
        open_risks=["API rate limits unclear"],
        key_people={"Alice": "Auth lead", "Bob": "EM"},
    )

    assert snapshot.id.startswith("SNAP-")
    assert "Auth Migration" in snapshot.active_milestones

    latest = engine.get_latest_snapshot("test-prog")
    assert latest is not None
    assert latest.id == snapshot.id


def test_memory_anchors():
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    anchor = engine.add_anchor(
        "test-prog",
        fact="Beta release date is March 15, 2026",
        source="Sprint planning meeting",
        source_type="meeting_transcript",
        people=["Alice", "Bob"],
        tags=["deadline", "beta"],
    )

    assert anchor.id.startswith("ANC-")
    assert anchor.confidence == 1.0

    anchors = engine.get_anchors("test-prog", person="Alice")
    assert len(anchors) == 1

    anchors_by_tag = engine.get_anchors("test-prog", tag="deadline")
    assert len(anchors_by_tag) == 1


def test_verify_anchor():
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    anchor = engine.add_anchor("test-prog", fact="API v2 ships in Q1")
    assert engine.verify_anchor("test-prog", anchor.id)
    assert not engine.verify_anchor("test-prog", "nonexistent")


def test_gap_detection():
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    # With no data, should detect missing artifacts
    gaps = engine.detect_gaps("test-prog")
    assert len(gaps) > 0

    missing_gap = [g for g in gaps if "Missing" in g.topic]
    assert len(missing_gap) > 0


def test_fill_gap():
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    # Detect gaps first
    gaps = engine.detect_gaps("test-prog")
    assert len(gaps) > 0

    topic = gaps[0].topic
    assert engine.fill_gap("test-prog", topic, "Created from existing data")

    # Filling creates an anchor
    anchors = engine.get_anchors("test-prog", tag="gap_filled")
    assert len(anchors) == 1


def test_reconstruct_context():
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    # Add some data
    engine.record_turn("test-prog", "user", "Start sprint 3")
    engine.take_snapshot("test-prog", summary="Sprint 3 started")
    engine.add_anchor("test-prog", fact="Ship date is March 15")

    context = engine.reconstruct_context("test-prog")
    assert "Sprint 3" in context
    assert "March 15" in context
    assert "Memory Anchors" in context


def test_memory_summary():
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    engine.record_turn("test-prog", "user", "Hello")
    engine.add_anchor("test-prog", fact="Key fact")

    summary = engine.get_summary("test-prog")
    assert "Persistent Memory" in summary
    assert "Total conversation turns: 1" in summary
    assert "Memory anchors: 1" in summary


def test_auto_snapshot():
    """Auto-snapshot should trigger after SNAPSHOT_INTERVAL_TURNS turns."""
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    # Record enough turns to trigger auto-snapshot (default: 10)
    for i in range(10):
        engine.record_turn("test-prog", "user", f"Turn {i}")

    latest = engine.get_latest_snapshot("test-prog")
    assert latest is not None
    assert "Auto-snapshot" in latest.summary


def test_empty_program_context():
    store = _make_store()
    engine = PersistentMemoryEngine(store)

    context = engine.reconstruct_context("nonexistent-prog")
    # Even an empty program detects missing artifacts as gaps
    assert "Detected Gaps" in context or "No context available" in context
