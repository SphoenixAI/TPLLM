"""Tests for outreach routing and personalized briefings (Commit 41).

The responsiveness grade this module used to assert — HIGHLY_RESPONSIVE
through UNRELIABLE — is gone, along with the response rate and
ignored-message count that produced it and the escalation they drove.
What remains routes outreach: channel, window, quick options, and how
long to allow for a reply. The last class asserts nothing here can rank
a colleague.
"""

import tempfile
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.artifacts.milestone_plan import MilestonePlanManager
from lltpm.workflows.engagement_tracker import EngagementTracker
from lltpm.workflows.personalized_briefing import PersonalizedBriefingEngine
from lltpm.workflows.delegation_engine import DelegationEngine


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
    assert profile.observed_responses == 1
    assert profile.avg_response_hours == 2.0
    assert profile.median_response_hours == 2.0
    assert profile.prefers_quick_options is True


def test_outreach_recommendation():
    store = _make_store()
    tracker = EngagementTracker(store)

    tracker.record_response("test-prog", "Alice", response_hours=2.0, channel="GCHAT")
    rec = tracker.get_outreach_recommendation("test-prog", "Alice")

    assert rec["person"] == "Alice"
    assert "recommendation" in rec
    assert rec["typical_response_hours"] == 2.0
    assert rec["observations"] == 1
    # Note: preferred_channel is not learned from the channel a reply
    # arrived on — pre-existing behaviour, unrelated to Commit 41.
    assert rec["best_channel"] in {"SLACK", "GCHAT"}


def test_engagement_summary():
    store = _make_store()
    tracker = EngagementTracker(store)

    tracker.record_response("test-prog", "Alice", response_hours=2.0)
    tracker.record_response("test-prog", "Bob", response_hours=48.0)

    summary = tracker.get_summary("test-prog")
    assert "Outreach routing" in summary
    assert "Alice" in summary
    assert "Bob" in summary


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


class TestNothingHereRanksAnybody:
    """The grade and the rate are gone, and must stay gone."""

    def test_the_profile_has_no_grade(self):
        import lltpm.models.engagement as engagement

        assert not hasattr(engagement, "ResponsivenessGrade")
        for field in ("grade", "response_rate", "total_ignored",
                      "total_requests"):
            assert field not in engagement.EngagementProfile.model_fields, (
                f"'{field}' is back; it is what the grade was computed "
                f"from"
            )

    def test_a_latency_estimate_states_its_sample_size(self):
        """So a caller can say 'no observations yet' instead of quoting
        the default as though it had been measured."""
        store = _make_store()
        tracker = EngagementTracker(store)
        profile = tracker._get_or_create("test-prog", "Alice")
        assert profile.has_observations is False

        tracker.record_response("test-prog", "Alice", response_hours=3.0)
        assert tracker.get_profile(
            "test-prog", "Alice",
        ).has_observations is True

    def test_an_unanswered_request_is_not_recorded_against_anyone(self):
        store = _make_store()
        tracker = EngagementTracker(store)
        tracker.record_response("test-prog", "Charlie")

        profile = tracker.get_profile("test-prog", "Charlie")
        assert profile.observed_responses == 0
        assert profile.has_observations is False

    def test_a_long_latency_changes_the_plan_not_the_person(self):
        store = _make_store()
        tracker = EngagementTracker(store)
        for _ in range(4):
            tracker.record_response("test-prog", "Bob", response_hours=72.0)

        advice = tracker.get_outreach_recommendation("test-prog", "Bob")
        text = advice["recommendation"].lower()
        assert "ask earlier than the deadline" in text
        for word in ("slow", "unreliable", "unresponsive", "their lead",
                     "response rate", "grade"):
            assert word not in text, (
                f"'{word}' turns routing advice into a verdict on Bob"
            )

    def test_the_advice_shape_does_not_depend_on_how_fast_they_are(self):
        """The old version branched on a grade and grew more punitive as
        the number got worse."""
        store = _make_store()
        tracker = EngagementTracker(store)
        tracker.record_response("test-prog", "Fast", response_hours=1.0)
        tracker.record_response("test-prog", "Slow", response_hours=50.0)

        fast = tracker.get_outreach_recommendation("test-prog", "Fast")
        slow = tracker.get_outreach_recommendation("test-prog", "Slow")
        for advice in (fast, slow):
            assert advice["recommendation"].startswith("Reach ")
            assert "Allow ~" in advice["recommendation"]

    def test_the_summary_cannot_be_read_as_a_ranking(self):
        store = _make_store()
        tracker = EngagementTracker(store)
        tracker.record_response("test-prog", "Alice", response_hours=2.0)
        tracker.record_response("test-prog", "Bob", response_hours=48.0)

        summary = tracker.get_summary("test-prog")
        for word in ("HIGHLY_RESPONSIVE", "UNRELIABLE", "SLOW", "Rate:"):
            assert word not in summary
