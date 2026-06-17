"""Tests for the Measurement & Operational Health ontology layer.

Covers:
1. KPI Definitions — categories, units, thresholds, health evaluation
2. KPI Observations — evidence-backed measurements
3. KPI Trends — direction detection over time
4. KPI Insights — cross-KPI analysis
5. Health Scores — aggregate health computation
6. Built-in KPI library — all 10 categories populated
7. Threshold evaluation — healthy/watch/warning/critical logic
8. Serialization round-trips
9. Full measurement flow scenario
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from lltpm.ontology.core import SourceRef, SourceSystem
from lltpm.ontology.measurement import (
    BUILTIN_KPI_DEFINITIONS,
    CalculationType,
    HealthScore,
    HealthStatus,
    InsightType,
    KPICategory,
    KPIDefinition,
    KPIInsight,
    KPIObservation,
    KPITrend,
    KPIUnit,
    MeasuredObjectType,
    TrendDirection,
    get_builtin_definitions,
    get_definitions_by_category,
)


# ── 1. KPI Definition ──────────────────────────────────────────────


class TestKPIDefinition:
    def test_create_definition(self):
        kpi = KPIDefinition(
            id="kpi-test",
            name="Test KPI",
            category=KPICategory.DELIVERY,
            description="A test metric",
            unit=KPIUnit.PERCENT,
            target_value=85.0,
            warning_threshold=60.0,
            critical_threshold=40.0,
        )
        assert kpi.name == "Test KPI"
        assert kpi.category == KPICategory.DELIVERY
        assert kpi.unit == KPIUnit.PERCENT
        assert kpi.enabled is True

    def test_evaluate_health_healthy(self):
        kpi = KPIDefinition(
            target_value=3.0,
            target_direction="lower_is_better",
            warning_threshold=5.0,
            critical_threshold=10.0,
        )
        assert kpi.evaluate_health(2.0) == HealthStatus.HEALTHY

    def test_evaluate_health_watch(self):
        kpi = KPIDefinition(
            target_value=3.0,
            target_direction="lower_is_better",
            warning_threshold=5.0,
            critical_threshold=10.0,
        )
        assert kpi.evaluate_health(4.0) == HealthStatus.WATCH

    def test_evaluate_health_warning(self):
        kpi = KPIDefinition(
            target_value=3.0,
            target_direction="lower_is_better",
            warning_threshold=5.0,
            critical_threshold=10.0,
        )
        assert kpi.evaluate_health(6.0) == HealthStatus.WARNING

    def test_evaluate_health_critical(self):
        kpi = KPIDefinition(
            target_value=3.0,
            target_direction="lower_is_better",
            warning_threshold=5.0,
            critical_threshold=10.0,
        )
        assert kpi.evaluate_health(12.0) == HealthStatus.CRITICAL

    def test_evaluate_health_higher_is_better_healthy(self):
        kpi = KPIDefinition(
            target_value=85.0,
            target_direction="higher_is_better",
            warning_threshold=60.0,
            critical_threshold=40.0,
        )
        assert kpi.evaluate_health(90.0) == HealthStatus.HEALTHY

    def test_evaluate_health_higher_is_better_critical(self):
        kpi = KPIDefinition(
            target_value=85.0,
            target_direction="higher_is_better",
            warning_threshold=60.0,
            critical_threshold=40.0,
        )
        assert kpi.evaluate_health(35.0) == HealthStatus.CRITICAL

    def test_evaluate_health_higher_is_better_warning(self):
        kpi = KPIDefinition(
            target_value=85.0,
            target_direction="higher_is_better",
            warning_threshold=60.0,
            critical_threshold=40.0,
        )
        assert kpi.evaluate_health(55.0) == HealthStatus.WARNING

    def test_evaluate_health_no_thresholds(self):
        kpi = KPIDefinition()
        assert kpi.evaluate_health(999.0) == HealthStatus.HEALTHY

    def test_applies_to_multiple_types(self):
        kpi = KPIDefinition(
            name="Multi-scope KPI",
            applies_to=["program", "project", "milestone"],
        )
        assert len(kpi.applies_to) == 3

    def test_source_imported_type(self):
        kpi = KPIDefinition(
            calculation_type=CalculationType.SOURCE_IMPORTED,
            source_systems=["github", "jira"],
        )
        assert kpi.calculation_type == CalculationType.SOURCE_IMPORTED
        assert len(kpi.source_systems) == 2


# ── 2. KPI Observation ─────────────────────────────────────────────


class TestKPIObservation:
    def test_create_observation(self):
        obs = KPIObservation(
            id="obs-001",
            kpi_definition_id="kpi-decision-latency",
            measured_object_id="prog-1",
            measured_object_type=MeasuredObjectType.PROGRAM,
            value=6.2,
            unit=KPIUnit.DAYS,
            health_status=HealthStatus.WARNING,
            detail="2 decisions pending > 5 days",
        )
        assert obs.value == 6.2
        assert obs.health_status == HealthStatus.WARNING

    def test_observation_is_evidence_backed(self):
        ref = SourceRef(system=SourceSystem.TASK_BOARD, external_id="dec-21")
        obs = KPIObservation(
            id="obs-ev",
            value=8.0,
            confidence=0.9,
            source_refs=[ref],
            evidence_claim_ids=["ec-1", "ec-2"],
        )
        assert obs.confidence == 0.9
        assert len(obs.source_refs) == 1
        assert len(obs.evidence_claim_ids) == 2

    def test_observation_with_period(self):
        now = datetime.utcnow()
        obs = KPIObservation(
            id="obs-period",
            value=12.0,
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        assert obs.period_start is not None
        assert obs.period_end is not None

    def test_observation_generated_by(self):
        obs = KPIObservation(
            generated_by=CalculationType.AI_INFERRED,
            confidence=0.7,
        )
        assert obs.generated_by == CalculationType.AI_INFERRED


# ── 3. KPI Trend ───────────────────────────────────────────────────


class TestKPITrend:
    def test_create_trend(self):
        trend = KPITrend(
            id="trend-001",
            kpi_definition_id="kpi-blocked-count",
            measured_object_id="prog-1",
            direction=TrendDirection.WORSENING,
            current_value=5.0,
            previous_value=2.0,
            change_percent=150.0,
            summary="Blocked items increased from 2 to 5 over 7 days",
            observation_ids=["obs-1", "obs-2", "obs-3"],
            observation_count=3,
            period_days=7,
        )
        assert trend.direction == TrendDirection.WORSENING
        assert trend.change_percent == 150.0
        assert len(trend.observation_ids) == 3

    def test_trend_stable(self):
        trend = KPITrend(direction=TrendDirection.STABLE, change_percent=0.0)
        assert trend.direction == TrendDirection.STABLE

    def test_trend_improving(self):
        trend = KPITrend(
            direction=TrendDirection.IMPROVING,
            current_value=2.0,
            previous_value=8.0,
            summary="Blockers decreased significantly",
        )
        assert trend.current_value < trend.previous_value

    def test_all_trend_directions(self):
        expected = {"improving", "stable", "worsening", "volatile", "unknown"}
        actual = {d.value for d in TrendDirection}
        assert actual == expected


# ── 4. KPI Insight ──────────────────────────────────────────────────


class TestKPIInsight:
    def test_create_insight(self):
        insight = KPIInsight(
            id="ins-001",
            insight_type=InsightType.DECISION_BOTTLENECK,
            title="Decision latency blocking milestone",
            summary="The project is not blocked by engineering execution — "
                    "it's blocked by unresolved product/security decisions.",
            related_kpi_observation_ids=["obs-1", "obs-2"],
            affected_object_ids=["ms-beta", "dec-21", "dec-22"],
            recommended_action_ids=["rec-1"],
            confidence=0.85,
        )
        assert insight.insight_type == InsightType.DECISION_BOTTLENECK
        assert len(insight.affected_object_ids) == 3
        assert insight.confidence == 0.85

    def test_insight_is_evidence_backed(self):
        ref = SourceRef(system=SourceSystem.TASK_BOARD, external_id="kpi-obs-1")
        insight = KPIInsight(
            source_refs=[ref],
            evidence_claim_ids=["ec-1"],
        )
        assert len(insight.source_refs) == 1

    def test_all_insight_types(self):
        expected = {
            "milestone_at_risk", "decision_bottleneck", "dependency_drag",
            "ownership_gap", "scope_churn", "execution_slowdown",
            "communication_gap", "quality_regression", "risk_acceleration",
            "agent_quality_issue", "positive_momentum",
        }
        actual = {t.value for t in InsightType}
        assert actual == expected


# ── 5. Health Score ─────────────────────────────────────────────────


class TestHealthScore:
    def test_create_health_score(self):
        hs = HealthScore(
            id="hs-prog-1",
            measured_object_id="prog-1",
            measured_object_type=MeasuredObjectType.PROGRAM,
            overall_status=HealthStatus.WARNING,
            score=62.0,
            worst_kpis=["kpi-decision-latency", "kpi-blocked-count"],
            trend=TrendDirection.WORSENING,
        )
        assert hs.overall_status == HealthStatus.WARNING
        assert hs.score == 62.0
        assert len(hs.worst_kpis) == 2

    def test_compute_status_critical_dominates(self):
        statuses = [HealthStatus.HEALTHY, HealthStatus.WARNING, HealthStatus.CRITICAL]
        assert HealthScore.compute_status(statuses) == HealthStatus.CRITICAL

    def test_compute_status_warning_without_critical(self):
        statuses = [HealthStatus.HEALTHY, HealthStatus.WARNING, HealthStatus.WATCH]
        assert HealthScore.compute_status(statuses) == HealthStatus.WARNING

    def test_compute_status_watch_without_warning(self):
        statuses = [HealthStatus.HEALTHY, HealthStatus.WATCH, HealthStatus.HEALTHY]
        assert HealthScore.compute_status(statuses) == HealthStatus.WATCH

    def test_compute_status_all_healthy(self):
        statuses = [HealthStatus.HEALTHY, HealthStatus.HEALTHY]
        assert HealthScore.compute_status(statuses) == HealthStatus.HEALTHY

    def test_compute_status_empty(self):
        assert HealthScore.compute_status([]) == HealthStatus.UNKNOWN


# ── 6. Built-in KPI Library ────────────────────────────────────────


class TestBuiltinKPIs:
    def test_builtin_count(self):
        assert len(BUILTIN_KPI_DEFINITIONS) == 31

    def test_all_categories_covered(self):
        """All 10 KPI categories should have at least one built-in definition."""
        categories_with_definitions = {d.category for d in BUILTIN_KPI_DEFINITIONS}
        # Business KPIs are excluded from builtins (org-specific)
        expected = {
            KPICategory.DELIVERY,
            KPICategory.EXECUTION_HEALTH,
            KPICategory.DECISION,
            KPICategory.DEPENDENCY,
            KPICategory.RISK,
            KPICategory.OWNERSHIP,
            KPICategory.COMMUNICATION,
            KPICategory.QUALITY,
            KPICategory.AGENT_PERFORMANCE,
        }
        assert expected.issubset(categories_with_definitions)

    def test_all_have_ids(self):
        for d in BUILTIN_KPI_DEFINITIONS:
            assert d.id, f"KPI missing id: {d.name}"
            assert d.id.startswith("kpi-")

    def test_all_have_names(self):
        for d in BUILTIN_KPI_DEFINITIONS:
            assert d.name, f"KPI missing name: {d.id}"

    def test_all_have_descriptions(self):
        for d in BUILTIN_KPI_DEFINITIONS:
            assert d.description, f"KPI missing description: {d.id}"

    def test_all_have_calculation_methods(self):
        for d in BUILTIN_KPI_DEFINITIONS:
            assert d.calculation_method, f"KPI missing calculation_method: {d.id}"

    def test_no_duplicate_ids(self):
        ids = [d.id for d in BUILTIN_KPI_DEFINITIONS]
        assert len(ids) == len(set(ids)), "Duplicate KPI IDs found"

    def test_get_builtin_definitions_dict(self):
        defs = get_builtin_definitions()
        assert isinstance(defs, dict)
        assert "kpi-decision-latency" in defs
        assert defs["kpi-decision-latency"].name == "Decision Latency"

    def test_get_definitions_by_category(self):
        delivery = get_definitions_by_category(KPICategory.DELIVERY)
        assert len(delivery) >= 2
        for d in delivery:
            assert d.category == KPICategory.DELIVERY

        decisions = get_definitions_by_category(KPICategory.DECISION)
        assert len(decisions) >= 2
        for d in decisions:
            assert d.category == KPICategory.DECISION

    def test_specific_kpis_exist(self):
        defs = get_builtin_definitions()
        assert "kpi-sprint-completion" in defs
        assert "kpi-blocked-count" in defs
        assert "kpi-decision-latency" in defs
        assert "kpi-open-dependencies" in defs
        assert "kpi-risk-materialization-rate" in defs
        assert "kpi-ownership-clarity" in defs
        assert "kpi-followup-completion" in defs
        assert "kpi-pr-review-cycle" in defs
        assert "kpi-human-correction-rate" in defs


# ── 7. Threshold Evaluation Scenarios ──────────────────────────────


class TestThresholdEvaluation:
    """Test the full threshold evaluation against real KPI definitions."""

    def test_sprint_completion_rate(self):
        kpi = get_builtin_definitions()["kpi-sprint-completion"]
        assert kpi.evaluate_health(90.0) == HealthStatus.HEALTHY
        assert kpi.evaluate_health(70.0) == HealthStatus.WATCH
        assert kpi.evaluate_health(55.0) == HealthStatus.WARNING
        assert kpi.evaluate_health(30.0) == HealthStatus.CRITICAL

    def test_decision_latency(self):
        kpi = get_builtin_definitions()["kpi-decision-latency"]
        assert kpi.evaluate_health(2.0) == HealthStatus.HEALTHY
        assert kpi.evaluate_health(4.0) == HealthStatus.WATCH
        assert kpi.evaluate_health(7.0) == HealthStatus.WARNING
        assert kpi.evaluate_health(15.0) == HealthStatus.CRITICAL

    def test_blocked_count(self):
        kpi = get_builtin_definitions()["kpi-blocked-count"]
        assert kpi.evaluate_health(0.0) == HealthStatus.HEALTHY
        assert kpi.evaluate_health(2.0) == HealthStatus.WATCH
        assert kpi.evaluate_health(4.0) == HealthStatus.WARNING
        assert kpi.evaluate_health(6.0) == HealthStatus.CRITICAL

    def test_ownership_clarity(self):
        kpi = get_builtin_definitions()["kpi-ownership-clarity"]
        assert kpi.evaluate_health(98.0) == HealthStatus.HEALTHY
        assert kpi.evaluate_health(88.0) == HealthStatus.WATCH
        assert kpi.evaluate_health(70.0) == HealthStatus.WARNING
        assert kpi.evaluate_health(50.0) == HealthStatus.CRITICAL

    def test_human_correction_rate(self):
        kpi = get_builtin_definitions()["kpi-human-correction-rate"]
        assert kpi.evaluate_health(5.0) == HealthStatus.HEALTHY
        assert kpi.evaluate_health(15.0) == HealthStatus.WATCH
        assert kpi.evaluate_health(30.0) == HealthStatus.WARNING
        assert kpi.evaluate_health(60.0) == HealthStatus.CRITICAL


# ── 8. Serialization Round-Trips ───────────────────────────────────


class TestSerialization:
    def test_definition_round_trip(self):
        kpi = KPIDefinition(
            id="kpi-rt",
            name="Round Trip Test",
            category=KPICategory.DELIVERY,
            unit=KPIUnit.PERCENT,
            target_value=85.0,
            warning_threshold=60.0,
        )
        data = kpi.model_dump()
        restored = KPIDefinition.model_validate(data)
        assert restored.id == "kpi-rt"
        assert restored.target_value == 85.0
        assert restored.category == KPICategory.DELIVERY

    def test_observation_round_trip(self):
        obs = KPIObservation(
            id="obs-rt",
            kpi_definition_id="kpi-test",
            value=42.0,
            health_status=HealthStatus.WARNING,
            source_refs=[SourceRef(system=SourceSystem.GITHUB, external_id="pr-1")],
            evidence_claim_ids=["ec-1"],
        )
        data = obs.model_dump()
        restored = KPIObservation.model_validate(data)
        assert restored.value == 42.0
        assert restored.health_status == HealthStatus.WARNING
        assert len(restored.source_refs) == 1

    def test_trend_round_trip(self):
        trend = KPITrend(
            id="trend-rt",
            direction=TrendDirection.IMPROVING,
            change_percent=-25.0,
            observation_ids=["obs-1", "obs-2"],
        )
        data = trend.model_dump()
        restored = KPITrend.model_validate(data)
        assert restored.direction == TrendDirection.IMPROVING
        assert len(restored.observation_ids) == 2

    def test_insight_round_trip(self):
        insight = KPIInsight(
            id="ins-rt",
            insight_type=InsightType.DEPENDENCY_DRAG,
            title="External dependency blocking beta",
            confidence=0.8,
        )
        data = insight.model_dump()
        restored = KPIInsight.model_validate(data)
        assert restored.insight_type == InsightType.DEPENDENCY_DRAG

    def test_health_score_round_trip(self):
        hs = HealthScore(
            id="hs-rt",
            overall_status=HealthStatus.CRITICAL,
            score=25.0,
            worst_kpis=["kpi-a", "kpi-b"],
        )
        data = hs.model_dump()
        restored = HealthScore.model_validate(data)
        assert restored.overall_status == HealthStatus.CRITICAL
        assert restored.score == 25.0

    def test_json_serialization(self):
        obs = KPIObservation(
            id="obs-json",
            value=6.2,
            health_status=HealthStatus.WARNING,
        )
        json_str = obs.model_dump_json()
        assert "6.2" in json_str
        assert "warning" in json_str


# ── 9. Full Measurement Flow Scenario ──────────────────────────────


class TestMeasurementFlowScenario:
    """Test a realistic scenario: decision latency KPI flow from definition
    through observation, trend, insight, to health score."""

    def test_full_flow(self):
        # 1. Get the KPI definition
        kpi_def = get_builtin_definitions()["kpi-decision-latency"]
        assert kpi_def.name == "Decision Latency"
        assert kpi_def.target_value == 3.0

        # 2. Create observations over time
        obs_week1 = KPIObservation(
            id="obs-dl-w1",
            kpi_definition_id=kpi_def.id,
            measured_object_id="prog-agentic-tpm",
            measured_object_type=MeasuredObjectType.PROGRAM,
            value=2.5,
            unit=kpi_def.unit,
            health_status=kpi_def.evaluate_health(2.5),
            observed_at=datetime(2026, 6, 1),
            evidence_claim_ids=["ec-d1"],
            confidence=0.95,
        )
        obs_week2 = KPIObservation(
            id="obs-dl-w2",
            kpi_definition_id=kpi_def.id,
            measured_object_id="prog-agentic-tpm",
            measured_object_type=MeasuredObjectType.PROGRAM,
            value=4.0,
            unit=kpi_def.unit,
            health_status=kpi_def.evaluate_health(4.0),
            observed_at=datetime(2026, 6, 8),
            evidence_claim_ids=["ec-d2", "ec-d3"],
            confidence=0.9,
        )
        obs_week3 = KPIObservation(
            id="obs-dl-w3",
            kpi_definition_id=kpi_def.id,
            measured_object_id="prog-agentic-tpm",
            measured_object_type=MeasuredObjectType.PROGRAM,
            value=6.2,
            unit=kpi_def.unit,
            health_status=kpi_def.evaluate_health(6.2),
            observed_at=datetime(2026, 6, 15),
            evidence_claim_ids=["ec-d4", "ec-d5"],
            confidence=0.9,
            detail="Decision D-21 proposed June 4, decided June 12. "
                   "Decision D-22 proposed June 8, still pending.",
        )

        assert obs_week1.health_status == HealthStatus.HEALTHY
        assert obs_week2.health_status == HealthStatus.WATCH
        assert obs_week3.health_status == HealthStatus.WARNING

        # 3. Detect trend
        trend = KPITrend(
            id="trend-dl",
            kpi_definition_id=kpi_def.id,
            measured_object_id="prog-agentic-tpm",
            direction=TrendDirection.WORSENING,
            current_value=6.2,
            previous_value=2.5,
            change_percent=148.0,
            summary="Decision latency increased from 2.5 to 6.2 days over 3 weeks",
            observation_ids=[obs_week1.id, obs_week2.id, obs_week3.id],
            observation_count=3,
            period_days=14,
        )
        assert trend.direction == TrendDirection.WORSENING
        assert trend.change_percent == 148.0

        # 4. Generate insight
        insight = KPIInsight(
            id="ins-dl",
            insight_type=InsightType.DECISION_BOTTLENECK,
            title="Architecture decisions are moving slowly",
            summary="The project is not blocked by engineering execution. "
                    "It is blocked by unresolved product/security decisions. "
                    "Decision D-22 (ontology schema) has been pending 7 days.",
            related_kpi_observation_ids=[obs_week3.id],
            affected_object_ids=["ms-beta", "dec-21", "dec-22"],
            recommended_action_ids=["rec-review-checkpoint"],
            confidence=0.85,
        )
        assert insight.insight_type == InsightType.DECISION_BOTTLENECK
        assert "dec-22" in insight.affected_object_ids

        # 5. Compute health score
        # Imagine other KPIs are healthy but decision latency is warning
        kpi_statuses = [
            HealthStatus.HEALTHY,   # sprint completion
            HealthStatus.HEALTHY,   # blocked count
            HealthStatus.WARNING,   # decision latency
            HealthStatus.HEALTHY,   # ownership clarity
        ]
        overall = HealthScore.compute_status(kpi_statuses)
        assert overall == HealthStatus.WARNING

        health = HealthScore(
            id="hs-prog",
            measured_object_id="prog-agentic-tpm",
            measured_object_type=MeasuredObjectType.PROGRAM,
            overall_status=overall,
            score=72.0,
            worst_kpis=[kpi_def.id],
            trend=trend.direction,
        )
        assert health.overall_status == HealthStatus.WARNING
        assert health.worst_kpis == ["kpi-decision-latency"]
        assert health.trend == TrendDirection.WORSENING

    def test_blocker_age_flow(self):
        """Test the blocker age KPI scenario from the concept."""
        kpi_def = get_builtin_definitions()["kpi-avg-blocker-age"]

        # Milestone has blocked work items
        obs = KPIObservation(
            id="obs-ba",
            kpi_definition_id=kpi_def.id,
            measured_object_id="ms-dashboard-frontend",
            measured_object_type=MeasuredObjectType.MILESTONE,
            value=5.4,
            unit=KPIUnit.DAYS,
            health_status=kpi_def.evaluate_health(5.4),
            source_refs=[
                SourceRef(system=SourceSystem.TASK_BOARD, external_id="wi-xpanel"),
            ],
            evidence_claim_ids=["ec-blocked-1"],
            detail="WorkItem 'Add cross-program intelligence panel' blocked "
                   "by Issue 'missing normalized project graph' for 5.4 days",
        )
        assert obs.health_status == HealthStatus.WARNING

        insight = KPIInsight(
            id="ins-ba",
            insight_type=InsightType.DEPENDENCY_DRAG,
            title="Dashboard milestone blocked by ontology dependency",
            summary="Prioritize ontology schema before adding more integrations",
            related_kpi_observation_ids=[obs.id],
            affected_object_ids=["ms-dashboard-frontend", "wi-xpanel"],
        )
        assert insight.insight_type == InsightType.DEPENDENCY_DRAG


# ── 10. Enum Coverage ──────────────────────────────────────────────


class TestEnums:
    def test_all_kpi_categories(self):
        expected = {
            "delivery", "execution_health", "decision", "dependency",
            "risk", "ownership", "communication", "quality",
            "business", "agent_performance",
        }
        actual = {c.value for c in KPICategory}
        assert actual == expected

    def test_all_kpi_units(self):
        expected = {
            "count", "percent", "days", "hours",
            "score", "currency", "boolean", "ratio",
        }
        actual = {u.value for u in KPIUnit}
        assert actual == expected

    def test_all_calculation_types(self):
        expected = {"derived", "source_imported", "ai_inferred", "manual"}
        actual = {c.value for c in CalculationType}
        assert actual == expected

    def test_all_health_statuses(self):
        expected = {"healthy", "watch", "warning", "critical", "unknown"}
        actual = {s.value for s in HealthStatus}
        assert actual == expected

    def test_all_measured_object_types(self):
        expected = {
            "program", "project", "workstream", "milestone",
            "team", "person", "sprint", "agent",
        }
        actual = {t.value for t in MeasuredObjectType}
        assert actual == expected
