"""Tests for workflow engines."""

import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.artifacts.raid_log import RAIDLogManager
from tpllm.artifacts.milestone_plan import MilestonePlanManager
from tpllm.artifacts.meeting_notes import MeetingNotesManager
from tpllm.workflows.dependency_tracker import DependencyTracker
from tpllm.workflows.risk_engine import RiskEngine
from tpllm.workflows.update_aggregator import UpdateAggregator
from tpllm.workflows.daily_cadence import DailyCadence
from tpllm.workflows.weekly_planner import WeeklyPlanner
from tpllm.workflows.bug_triage import BugTriageEngine


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_dependency_tracker_detects_overdue():
    store = _make_store()
    raid = RAIDLogManager(store)
    raid.add_dependency(
        "prog",
        title="Auth API",
        upstream_team="Platform",
        owner="Alice",
        commitment_date=(date.today() - timedelta(days=5)).isoformat(),
    )
    tracker = DependencyTracker(store)
    alerts = tracker.scan("prog")
    assert any(a.kind == "overdue_dependency" for a in alerts)


def test_dependency_tracker_detects_unowned():
    store = _make_store()
    raid = RAIDLogManager(store)
    raid.add_dependency("prog", title="Unowned dep", upstream_team="Infra")
    tracker = DependencyTracker(store)
    alerts = tracker.scan("prog")
    assert any(a.kind == "unowned_dependency" for a in alerts)


def test_risk_engine_detects_unmitigated():
    store = _make_store()
    raid = RAIDLogManager(store)
    raid.add_risk("prog", title="Data loss", severity="CRITICAL")
    engine = RiskEngine(store)
    signals = engine.scan("prog")
    assert any(s.category == "unmitigated_risk" for s in signals)


def test_risk_engine_detects_approaching_deadline():
    store = _make_store()
    ms = MilestonePlanManager(store)
    ms.add_milestone(
        "prog",
        name="Beta",
        target_date=(date.today() + timedelta(days=3)).isoformat(),
        owner="Bob",
    )
    engine = RiskEngine(store)
    signals = engine.scan("prog")
    assert any(s.category == "approaching_deadline" for s in signals)


def test_update_aggregator():
    store = _make_store()
    ms = MilestonePlanManager(store)
    ms.add_milestone("prog", name="Alpha", owner="Alice")
    ms.add_milestone("prog", name="Beta", owner="Bob")
    raid = RAIDLogManager(store)
    raid.add_risk("prog", title="Risk 1", severity="CRITICAL")

    agg = UpdateAggregator(store)
    pulse = agg.aggregate("prog")
    assert pulse.total_milestones == 2
    assert pulse.critical_risks == 1
    assert pulse.health_signal == "RED"  # critical risks => RED


def test_daily_cadence():
    store = _make_store()
    ms = MilestonePlanManager(store)
    ms.add_milestone("prog", name="Alpha", owner="Alice")
    cadence = DailyCadence(store)
    brief = cadence.run("prog")
    assert brief.pulse is not None
    rendered = brief.render()
    assert "Daily Brief" in rendered


def test_weekly_planner():
    planner = WeeklyPlanner()
    plan = planner.generate("prog", weekly_goals=["Ship alpha"])
    rendered = plan.render()
    assert "Ship alpha" in rendered
    assert "Plan Integrity" in rendered

    focus = planner.get_today_focus()
    assert "theme" in focus
    assert "focus" in focus


def test_bug_triage():
    store = _make_store()
    engine = BugTriageEngine(store)
    bug = engine.add_bug("prog", "BUG-001", "Crash on login", severity="P0", owner="Alice")
    assert bug.severity.value == "P0"

    summary = engine.get_summary("prog")
    assert "Crash on login" in summary

    stop_ship = engine.get_stop_ship("prog")
    assert len(stop_ship) == 1
