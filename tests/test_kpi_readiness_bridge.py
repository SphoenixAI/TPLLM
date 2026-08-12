"""Tests for Commit 16 — KPI → launch readiness bridge."""

from __future__ import annotations

from lltpm.intelligence import (
    KPIHealthStatus,
    KPIIndicatorRole,
    KPIMilestoneLink,
    LaunchReadinessDimensionType,
    ReadinessSignalDirection,
    assess_kpi_health,
    kpi_to_readiness_contributions,
)
from tests._kpi_fixtures import AS_OF, definition, observation, target


def _link(launch_blocking=True):
    return KPIMilestoneLink(
        id="ml-1", kpi_id="kpi-activation", milestone_id="milestone-beta",
        importance="critical", launch_blocking=launch_blocking,
        required_target_id="tgt-1", explanation="Gates beta.",
        evidence_ids=["ev-launch-doc"],
    )


def _trend(value=61.0, days_ago=1.0):
    return assess_kpi_health(
        definition=definition(),
        target=target(),
        observations=[observation(value=value, days_ago=days_ago)],
        profile=None,
        as_of=AS_OF,
    )


def _bridge(trend, links=None, kpi=None):
    return kpi_to_readiness_contributions(
        kpi_definitions=[kpi or definition()],
        trends=[trend],
        milestone_links=links if links is not None else [_link()],
        as_of=AS_OF,
    )


class TestReadinessBridge:
    def test_launch_blocking_breach_produces_negative_contribution(self):
        contributions = _bridge(_trend(value=61.0))
        validation = [
            c for c in contributions
            if c.dimension == LaunchReadinessDimensionType.VALIDATION_READINESS
        ]
        assert validation
        assert validation[0].direction == ReadinessSignalDirection.NEGATIVE
        assert validation[0].blocker_candidate is True
        assert "Launch-blocking" in validation[0].rationale
        assert validation[0].evidence_ids

    def test_stale_kpi_produces_low_freshness_signal(self):
        stale_trend = _trend(value=75.0, days_ago=30)
        assert stale_trend.health == KPIHealthStatus.STALE
        contributions = _bridge(stale_trend)
        assert len(contributions) == 1
        assert contributions[0].freshness_factor <= 0.2
        assert "stale" in contributions[0].rationale.lower()

    def test_deteriorating_leading_kpi_flags_early_risk(self):
        leading = definition(
            id="kpi-latency", canonical_name="Graph lookup latency",
            kpi_type="technical",
            indicator_role=KPIIndicatorRole.LEADING, unit="ms",
        )
        trend = assess_kpi_health(
            definition=leading,
            target=target(
                id="tgt-lat", kpi_id="kpi-latency", operator="less_than",
                target_value=200.0,
            ),
            observations=[
                observation("obs-a", value=120.0, days_ago=8,
                            kpi_id="kpi-latency"),
                observation("obs-b", value=170.0, days_ago=1,
                            kpi_id="kpi-latency"),
            ],
            profile=None,
            as_of=AS_OF,
        )
        assert trend.direction == "deteriorating"
        contributions = kpi_to_readiness_contributions(
            kpi_definitions=[leading], trends=[trend],
            milestone_links=[], as_of=AS_OF,
        )
        assert len(contributions) == 1
        assert contributions[0].dimension == (
            LaunchReadinessDimensionType.TECHNICAL_CONFIDENCE
        )
        assert "Leading KPI" in contributions[0].rationale

    def test_healthy_blocking_kpi_produces_positive_evidence(self):
        contributions = _bridge(_trend(value=82.0))
        assert len(contributions) == 1
        assert contributions[0].direction == ReadinessSignalDirection.POSITIVE

    def test_no_direct_score_mutation(self):
        import inspect
        import lltpm.intelligence.kpi_readiness_bridge as mod

        source = inspect.getsource(mod)
        for pattern in (
            "overall_score =",
            ".overall_score=",
            "scorecard.overall",
            "model_copy(update={\"overall_score\"",
            "LaunchReadinessScorecard(",
        ):
            assert pattern not in source, (
                f"bridge must not contain '{pattern}' — KPIs influence "
                f"readiness only through evidence contributions."
            )

    def test_bridge_deterministic(self):
        first = [c.model_dump() for c in _bridge(_trend())]
        second = [c.model_dump() for c in _bridge(_trend())]
        assert first == second
