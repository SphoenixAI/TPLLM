"""Tests for Risk Prediction Engine and Quarterly Evaluations."""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.prediction import (
    PerformanceGrade,
    PersonMetrics,
    PredictionConfidence,
    RiskSignalType,
)
from lltpm.workflows.risk_predictor import RiskPredictor


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def _seed_milestones(store, at_risk=True):
    from lltpm.models.milestone import MilestonePlan, Milestone, MilestoneStatus
    milestones = [
        Milestone(
            id="MS-001", name="Beta", owner="Alice",
            target_date=date.today() + timedelta(days=5),
            status=MilestoneStatus.AT_RISK if at_risk else MilestoneStatus.IN_PROGRESS,
            on_critical_path=True,
        ),
    ]
    if at_risk:
        milestones.append(Milestone(
            id="MS-002", name="Launch", owner="Bob",
            target_date=date.today() - timedelta(days=2),
            status=MilestoneStatus.AT_RISK,
            on_critical_path=True,
        ))
    store.save("test-prog", "milestone_plan", MilestonePlan(milestones=milestones))


def _seed_delegations(store, person="Alice", count=6):
    from lltpm.models.delegation import DelegationPlan, DelegationItem, DelegationStatus
    items = [
        DelegationItem(
            id=f"DEL-{i:03}", title=f"Task {i}",
            assigned_to=person, status=DelegationStatus.IN_PROGRESS,
        )
        for i in range(count)
    ]
    store.save("test-prog", "delegation_plan", DelegationPlan(items=items))


def _seed_engagement(store, person="Alice", grade="SLOW"):
    from lltpm.models.engagement import EngagementProfile, PreferredChannel, ResponsivenessGrade
    profile = EngagementProfile(
        person=person,
        program_id="test-prog",
        avg_response_hours=48.0 if grade == "SLOW" else 2.0,
        total_requests=10,
        total_ignored=3 if grade == "SLOW" else 0,
        grade=ResponsivenessGrade(grade),
        preferred_channel=PreferredChannel.SLACK,
    )
    data = {person: profile.model_dump()}
    store.save_raw("test-prog", "engagement_profiles", json.dumps(data, default=str))


def _seed_scope(store, change_count=5):
    from lltpm.models.scope import ScopeBaseline, ScopeChangeRequest, ScopeChangeStatus
    crs = [
        ScopeChangeRequest(
            id=f"SCR-{i:03}", title=f"Change {i}",
            justification="Needed", requested_by="Bob",
            status=ScopeChangeStatus.PROPOSED,
        )
        for i in range(change_count)
    ]
    store.save("test-prog", "scope_baseline", ScopeBaseline(change_requests=crs))


# ── Risk prediction tests ────────────────────────────────

def test_predict_milestone_slips():
    store = _make_store()
    _seed_milestones(store, at_risk=True)

    predictor = RiskPredictor(store)
    predictions = predictor.predict_risks("test-prog")

    milestone_preds = [p for p in predictions if p.signal_type == RiskSignalType.MILESTONE_SLIP]
    assert len(milestone_preds) >= 1


def test_predict_capacity_overload():
    store = _make_store()
    _seed_delegations(store, person="Alice", count=7)

    predictor = RiskPredictor(store)
    predictions = predictor.predict_risks("test-prog")

    capacity_preds = [p for p in predictions if p.signal_type == RiskSignalType.CAPACITY_OVERLOAD]
    assert len(capacity_preds) == 1
    assert "Alice" in capacity_preds[0].affected_people


def test_predict_engagement_drop():
    store = _make_store()
    _seed_engagement(store, person="Alice", grade="SLOW")

    predictor = RiskPredictor(store)
    predictions = predictor.predict_risks("test-prog")

    eng_preds = [p for p in predictions if p.signal_type == RiskSignalType.ENGAGEMENT_DROP]
    assert len(eng_preds) == 1
    assert "Alice" in eng_preds[0].title


def test_predict_scope_creep():
    store = _make_store()
    _seed_scope(store, change_count=5)

    predictor = RiskPredictor(store)
    predictions = predictor.predict_risks("test-prog")

    scope_preds = [p for p in predictions if p.signal_type == RiskSignalType.SCOPE_CREEP]
    assert len(scope_preds) == 1
    assert scope_preds[0].confidence == PredictionConfidence.HIGH


def test_no_predictions_for_healthy_program():
    store = _make_store()
    _seed_milestones(store, at_risk=False)

    predictor = RiskPredictor(store)
    predictions = predictor.predict_risks("test-prog")

    # Healthy milestones shouldn't trigger slip predictions
    slip_preds = [p for p in predictions if p.signal_type == RiskSignalType.MILESTONE_SLIP]
    assert len(slip_preds) == 0


def test_prediction_summary():
    store = _make_store()
    _seed_milestones(store, at_risk=True)
    _seed_delegations(store, count=8)

    predictor = RiskPredictor(store)
    predictor.predict_risks("test-prog")

    summary = predictor.get_prediction_summary("test-prog")
    assert "Risk Predictions" in summary


def test_predictions_persist():
    store = _make_store()
    _seed_milestones(store, at_risk=True)

    predictor = RiskPredictor(store)
    predictor.predict_risks("test-prog")

    # Load from persistence
    loaded = predictor.get_predictions("test-prog")
    assert len(loaded) > 0


# ── Quarterly evaluation tests ────────────────────────────

def test_generate_evaluation_with_metrics():
    store = _make_store()
    predictor = RiskPredictor(store)

    metrics = PersonMetrics(
        person="Alice",
        total_requests_received=50,
        total_responses=48,
        avg_response_hours=3.5,
        high_priority_requests=10,
        high_priority_responses=10,
        commitments_made=20,
        commitments_met=18,
        unblocked_others=5,
        was_blocker=1,
    )

    evaluation = predictor.generate_evaluation(
        "test-prog", "Alice", "2026-Q1", metrics,
    )

    assert evaluation.person == "Alice"
    assert evaluation.quarter == "2026-Q1"
    assert evaluation.responsiveness_grade == PerformanceGrade.EXCEPTIONAL
    assert evaluation.reliability_grade in (PerformanceGrade.EXCEPTIONAL, PerformanceGrade.STRONG)


def test_evaluation_identifies_strengths():
    store = _make_store()
    predictor = RiskPredictor(store)

    metrics = PersonMetrics(
        person="Alice",
        total_requests_received=20,
        total_responses=19,
        avg_response_hours=2.0,
        commitments_made=10,
        commitments_met=10,
    )

    evaluation = predictor.generate_evaluation(
        "test-prog", "Alice", "2026-Q1", metrics,
    )

    assert len(evaluation.strengths) > 0
    assert any("response" in s.lower() for s in evaluation.strengths)


def test_evaluation_identifies_improvements():
    store = _make_store()
    predictor = RiskPredictor(store)

    metrics = PersonMetrics(
        person="Bob",
        total_requests_received=20,
        total_responses=10,
        avg_response_hours=30.0,
        ignored_messages=5,
        commitments_made=10,
        commitments_met=4,
    )

    evaluation = predictor.generate_evaluation(
        "test-prog", "Bob", "2026-Q1", metrics,
    )

    assert evaluation.responsiveness_grade in (PerformanceGrade.BELOW, PerformanceGrade.CRITICAL)
    assert len(evaluation.areas_for_improvement) > 0


def test_render_evaluation():
    store = _make_store()
    predictor = RiskPredictor(store)

    metrics = PersonMetrics(
        person="Alice",
        total_requests_received=30,
        total_responses=28,
        avg_response_hours=4.0,
        commitments_made=15,
        commitments_met=14,
    )

    evaluation = predictor.generate_evaluation(
        "test-prog", "Alice", "2026-Q1", metrics,
    )

    rendered = predictor.render_evaluation(evaluation)
    assert "Quarterly Evaluation: Alice" in rendered
    assert "Grades" in rendered
    assert "Key Metrics" in rendered


def test_evaluation_persists():
    store = _make_store()
    predictor = RiskPredictor(store)

    metrics = PersonMetrics(person="Alice", total_requests_received=10, total_responses=9)
    predictor.generate_evaluation("test-prog", "Alice", "2026-Q1", metrics)

    loaded = predictor.get_evaluation("test-prog", "Alice", "2026-Q1")
    assert loaded is not None
    assert loaded.person == "Alice"


def test_auto_quarter_detection():
    store = _make_store()
    predictor = RiskPredictor(store)

    metrics = PersonMetrics(person="Alice")
    evaluation = predictor.generate_evaluation("test-prog", "Alice", metrics=metrics)

    assert evaluation.quarter != ""
    assert "Q" in evaluation.quarter
