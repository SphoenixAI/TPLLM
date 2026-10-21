"""Tests for the risk prediction engine (Commit 41).

Half this file used to test a quarterly employee evaluation —
responsiveness, reliability and collaboration letter grades — and one
prediction model that reported "Alice has UNRESPONSIVE engagement". Both
are gone. What is tested here predicts milestones, scope and allocation
load, and the last class asserts that the removed surface stays removed:
a deletion nothing guards is a deletion that comes back.
"""

import tempfile
from datetime import date, timedelta
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.prediction import PredictionConfidence, RiskSignalType
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


def test_predict_allocation_load():
    store = _make_store()
    _seed_delegations(store, person="Alice", count=7)

    predictor = RiskPredictor(store)
    predictions = predictor.predict_risks("test-prog")

    load = [
        p for p in predictions
        if p.signal_type == RiskSignalType.ALLOCATION_LOAD
    ]
    assert len(load) == 1
    assert "Alice" in load[0].affected_people


def test_allocation_load_reads_as_distribution_not_judgement():
    """The same count can be written as a capacity observation or as an
    accusation. This asserts which one it is."""
    store = _make_store()
    _seed_delegations(store, person="Alice", count=9)

    predictions = RiskPredictor(store).predict_risks("test-prog")
    load = next(
        p for p in predictions
        if p.signal_type == RiskSignalType.ALLOCATION_LOAD
    )

    assert "assigned to" in load.title
    assert "whoever holds it" in load.description
    text = f"{load.title} {load.description} {' '.join(load.recommended_actions)}"
    for word in ("overloaded", "workload", "performance", "slow"):
        assert word not in text.lower(), (
            f"'{word}' makes this a statement about Alice rather than "
            f"about how work was distributed"
        )
    assert any("Redistribute" in a for a in load.recommended_actions)


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


class TestThePersonGradingSurfaceIsGone:
    """Commit 41 removed it. A deletion nothing guards comes back."""

    def test_no_performance_grade_model_exists(self):
        import lltpm.models.prediction as prediction

        for name in ("PerformanceGrade", "PersonMetrics",
                     "QuarterlyEvaluation"):
            assert not hasattr(prediction, name), (
                f"{name} is back; it graded a colleague"
            )

    def test_no_predictor_method_generates_an_evaluation(self):
        for name in ("generate_evaluation", "get_evaluation",
                     "render_evaluation", "_collect_metrics",
                     "_grade_responsiveness", "_grade_reliability",
                     "_grade_collaboration", "_compute_overall_grade"):
            assert not hasattr(RiskPredictor, name), f"{name} is back"

    def test_no_signal_type_names_an_individual_as_the_risk(self):
        for name in ("RESPONSE_DEGRADATION", "ENGAGEMENT_DROP",
                     "COMMITMENT_MISS", "BLOCKER_PATTERN"):
            assert not hasattr(RiskSignalType, name), (
                f"{name} made a person the risk rather than the structure"
            )

    def test_the_remaining_signals_are_structural(self):
        assert {s.value for s in RiskSignalType} == {
            "milestone_slip", "scope_creep", "dependency_chain",
            "allocation_load",
        }

    def test_the_agent_cannot_ask_for_an_evaluation(self):
        from lltpm.tools.prediction_tools import get_prediction_tools

        names = {t["name"] for t in get_prediction_tools(_make_store())}
        assert names == {"predict_risks", "get_prediction_summary"}
        assert "generate_evaluation" not in names

    def test_no_prediction_names_a_person_outside_affected_people(self):
        """A person may be named as somewhere a risk lands. The risk
        itself must be about structure."""
        store = _make_store()
        _seed_milestones(store, at_risk=True)
        _seed_delegations(store, person="Alice", count=8)
        _seed_scope(store, change_count=5)

        for prediction in RiskPredictor(store).predict_risks("test-prog"):
            blob = " ".join([
                prediction.title, prediction.description,
                *prediction.recommended_actions, *prediction.data_points,
            ]).lower()
            for word in ("unresponsive", "response rate", "escalat",
                         "reliability", "grade", "pattern of not"):
                assert word not in blob, (
                    f"'{word}' in {prediction.signal_type.value}: "
                    f"{prediction.title}"
                )
