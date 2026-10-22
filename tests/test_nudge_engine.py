"""Tests for Smart Nudge Engine."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.nudge_engine import NudgeEngine
from tpllm.models.nudge import NudgeStatus, NudgeTier


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_create_nudge():
    store = _make_store()
    engine = NudgeEngine(store)

    record = engine.create_nudge(
        "test-prog",
        target_person="Alice",
        subject="Status on auth migration",
        why_it_matters="Blocking beta release",
        blocks=["Beta milestone"],
        quick_options=["On track", "Slipping", "Blocked"],
        escalate_to="Bob (EM)",
    )

    assert record.id.startswith("NDG-")
    assert record.target_person == "Alice"
    assert record.tier == NudgeTier.OBSERVE
    assert len(record.quick_response_options) == 3


def test_record_response():
    store = _make_store()
    engine = NudgeEngine(store)

    record = engine.create_nudge("test-prog", "Alice", "Update on auth")
    assert engine.record_response("test-prog", record.id, "On track for Mar 1")

    pending = engine.get_pending("test-prog")
    assert len(pending) == 0  # Should be responded, not pending


def test_engagement_budget():
    store = _make_store()
    engine = NudgeEngine(store)

    budget = engine.set_budget("test-prog", "Alice", daily=2, weekly=5)
    assert budget.daily_budget == 2
    assert budget.weekly_budget == 5
    assert budget.can_nudge is True


def test_nudge_summary():
    store = _make_store()
    engine = NudgeEngine(store)

    engine.create_nudge("test-prog", "Alice", "Task A")
    engine.create_nudge("test-prog", "Bob", "Task B")

    summary = engine.get_summary("test-prog")
    assert "Nudge Summary" in summary
    assert "Pending: 2" in summary


def test_evaluate_pending_within_observe_window():
    """Nudges within observe window should produce no actions."""
    store = _make_store()
    engine = NudgeEngine(store)

    # Create a nudge just now — should be within observe window
    engine.create_nudge("test-prog", "Alice", "Fresh request")

    actions = engine.evaluate_pending("test-prog")
    # No actions expected since we're within the observe window (< 24h)
    assert all(a["action"] != "SEND_NUDGE" for a in actions)
