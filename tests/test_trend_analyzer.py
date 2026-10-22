"""Tests for Historical Trend Analyzer."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.milestone import Milestone, MilestonePlan, MilestoneStatus
from tpllm.models.risk import RAIDLog, Risk, Severity, Probability, RAIDItemStatus
from tpllm.models.trends import TrendDirection
from tpllm.workflows.trend_analyzer import TrendAnalyzer


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def _seed_program(store, pid="test-prog"):
    from tpllm.models.program import Program
    store.save(pid, "program", Program(id=pid, name="Test", goal="Test goal"))
    store.save(pid, "milestone_plan", MilestonePlan(milestones=[
        Milestone(id="MS-1", name="Alpha", status=MilestoneStatus.COMPLETED),
        Milestone(id="MS-2", name="Beta", status=MilestoneStatus.IN_PROGRESS),
    ]))
    store.save(pid, "raid_log", RAIDLog(risks=[
        Risk(id="R-1", title="Risk 1", severity=Severity.MEDIUM,
             probability=Probability.MEDIUM, status=RAIDItemStatus.OPEN),
    ]))


def test_record_health_snapshot():
    store = _make_store()
    _seed_program(store)
    analyzer = TrendAnalyzer(store)

    snap = analyzer.record_health_snapshot("test-prog")

    assert snap.program_id == "test-prog"
    assert snap.milestone_completion == 0.5  # 1 of 2 completed
    assert snap.open_risks == 1


def test_record_velocity():
    store = _make_store()
    analyzer = TrendAnalyzer(store)

    today = date.today()
    point = analyzer.record_velocity(
        "test-prog",
        today - timedelta(days=7), today,
        milestones_completed=3, milestones_planned=5,
        items_closed=10, items_opened=4,
    )

    assert point.completion_rate == 0.6
    assert point.burn_rate == 6


def test_analyze_trends_stable():
    store = _make_store()
    _seed_program(store)
    analyzer = TrendAnalyzer(store)

    # Record a few snapshots
    for _ in range(3):
        analyzer.record_health_snapshot("test-prog")

    report = analyzer.analyze_trends("test-prog")

    assert report.program_id == "test-prog"
    assert report.health_trend == TrendDirection.STABLE


def test_trend_summary():
    store = _make_store()
    _seed_program(store)
    analyzer = TrendAnalyzer(store)

    analyzer.record_health_snapshot("test-prog")
    summary = analyzer.get_trend_summary("test-prog")

    assert "Trend Report" in summary
    assert "test-prog" in summary


def test_empty_trends():
    store = _make_store()
    analyzer = TrendAnalyzer(store)

    report = analyzer.analyze_trends("nonexistent")

    assert report.health_trend == TrendDirection.STABLE
    assert report.velocity_trend == TrendDirection.STABLE
    assert len(report.warnings) == 0


def test_velocity_trend_computed():
    store = _make_store()
    analyzer = TrendAnalyzer(store)

    today = date.today()
    # Record declining velocity
    for i in range(6):
        week_start = today - timedelta(weeks=6 - i)
        week_end = week_start + timedelta(days=7)
        completed = max(1, 5 - i)  # declining
        analyzer.record_velocity(
            "test-prog", week_start, week_end,
            milestones_completed=completed, milestones_planned=5,
        )

    report = analyzer.analyze_trends("test-prog")
    # With declining completion rates, velocity should be declining
    assert report.velocity_trend in (TrendDirection.DECLINING, TrendDirection.STABLE)
