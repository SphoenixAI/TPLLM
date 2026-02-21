"""Tests for Engagement Tracker and Personalized Briefings."""

import tempfile
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.artifacts.milestone_plan import MilestonePlanManager
from lltpm.workflows.engagement_tracker import EngagementTracker
from lltpm.workflows.personalized_briefing import PersonalizedBriefingEngine
from lltpm.workflows.delegation_engine import DelegationEngine
from lltpm.models.engagement import ResponsivenessGrade


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


# ── Engagement Tracker ────────────────────────────────────

def test_record_response():
    store = _make_store()
    tracker = EngagementTracker(store)

    profile = tracker.record_response(
        "test-prog", "Alice",
        request_type="status_check",
        response_hours=2.0,
        channel="SLACK",
        had_quick_options=True,
        used_quick_option=True,
    )

    assert profile.person == "Alice"
    assert profile.total_requests == 1
    assert profile.total_responses == 1
    assert profile.avg_response_hours == 2.0
    assert profile.prefers_quick_options is True


def test_engagement_grade():
    store = _make_store()
    tracker = EngagementTracker(store)

    # Fast responder
    for _ in range(5):
        tracker.record_response("test-prog", "Alice", response_hours=1.0)

    profile = tracker.get_profile("test-prog", "Alice")
    assert profile.grade == ResponsivenessGrade.HIGHLY_RESPONSIVE

    # Slow responder
    for _ in range(5):
        tracker.record_response("test-prog", "Bob", response_hours=60.0)

    bob = tracker.get_profile("test-prog", "Bob")
    assert bob.grade == ResponsivenessGrade.SLOW


def test_outreach_recommendation():
    store = _make_store()
    tracker = EngagementTracker(store)

    tracker.record_response("test-prog", "Alice", response_hours=2.0, channel="GCHAT")
    rec = tracker.get_outreach_recommendation("test-prog", "Alice")

    assert rec["person"] == "Alice"
    assert "recommendation" in rec
    assert rec["avg_response_hours"] == 2.0


def test_engagement_summary():
    store = _make_store()
    tracker = EngagementTracker(store)

    tracker.record_response("test-prog", "Alice", response_hours=2.0)
    tracker.record_response("test-prog", "Bob", response_hours=48.0)

    summary = tracker.get_summary("test-prog")
    assert "Engagement Profiles" in summary
    assert "Alice" in summary
    assert "Bob" in summary


def test_ignored_response():
    store = _make_store()
    tracker = EngagementTracker(store)

    tracker.record_response("test-prog", "Charlie", ignored=True)
    profile = tracker.get_profile("test-prog", "Charlie")
    assert profile.total_ignored == 1
    assert profile.response_rate == 0.0


# ── Personalized Briefings ────────────────────────────────

def test_engineer_briefing():
    store = _make_store()
    # Set up some data
    delegation = DelegationEngine(store)
    milestones = MilestonePlanManager(store)

    delegation.delegate("test-prog", "Fix auth bug", assigned_to="Alice", priority="P1")
    milestones.add_milestone("test-prog", name="Beta", owner="Alice", target_date="2026-04-01")

    briefings = PersonalizedBriefingEngine(store)
    briefing = briefings.generate("test-prog", "Alice", "ENGINEER")

    rendered = briefing.render()
    assert "Alice's Briefing" in rendered
    assert "Your Items" in rendered or "Your Milestones" in rendered


def test_exec_briefing():
    store = _make_store()

    briefings = PersonalizedBriefingEngine(store)
    briefing = briefings.generate("test-prog", "CEO", "EXECUTIVE")

    rendered = briefing.render()
    assert "CEO" in rendered
    assert "executive summary" in rendered.lower() or "Program Health" in rendered
