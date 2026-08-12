"""Tests for Commit 16 — KPI core models and health assessment."""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.intelligence import (
    KPIHealthStatus,
    KPIIndicatorRole,
    KPIMeasurementProfile,
    KPIRelationLabel,
    KPIRelationship,
    active_target,
    assess_kpi_health,
    resolve_kpi_by_name,
    set_kpi_target,
)
from tests._kpi_fixtures import AS_OF, definition, observation, target


class TestKPIDefinition:
    def test_requires_why_it_matters(self):
        with pytest.raises(ValueError, match="why_it_matters"):
            definition(why_it_matters="   ")

    def test_alias_resolves_canonical(self):
        kpi = definition()
        assert resolve_kpi_by_name(
            name="Activation %", known_kpis=[kpi],
        ) is kpi
        assert resolve_kpi_by_name(
            name="checkout failures", known_kpis=[kpi],
        ) is None


class TestTargetVersioning:
    def test_new_target_closes_old_never_overwrites(self):
        old = target(id="tgt-old", target_value=60.0)
        new = target(
            id="tgt-new", target_value=70.0,
            effective_from=AS_OF - timedelta(days=10),
        )
        versioned = set_kpi_target(targets=[old], new_target=new)
        assert len(versioned) == 2  # history preserved
        closed = next(t for t in versioned if t.id == "tgt-old")
        assert closed.effective_until == new.effective_from
        assert closed.target_value == 60.0  # never overwritten
        assert active_target(
            targets=versioned, kpi_id="kpi-activation", as_of=AS_OF,
        ).id == "tgt-new"
        # Historical query still finds the old target.
        assert active_target(
            targets=versioned, kpi_id="kpi-activation",
            as_of=AS_OF - timedelta(days=30),
        ).id == "tgt-old"


class TestObservation:
    def test_observed_and_reported_are_distinct(self):
        obs = observation()
        assert obs.reported_at > obs.observed_at

    def test_measurement_lag_distinct_from_lagging_indicator(self):
        profile = KPIMeasurementProfile(
            kpi_id="kpi-activation",
            indicator_role=KPIIndicatorRole.LAGGING,
            expected_refresh_interval_seconds=24 * 3600,
            expected_measurement_lag_seconds=48 * 3600,
            typical_stabilization_window_seconds=7 * 24 * 3600,
            explanation=(
                "Activation lags product changes by days (lagging "
                "indicator); separately, the pipeline reports with a "
                "48-hour measurement delay."
            ),
            evidence_ids=["ev-profile"],
        )
        # Two separate concepts, two separate fields.
        assert profile.indicator_role == KPIIndicatorRole.LAGGING
        assert profile.expected_measurement_lag_seconds == 48 * 3600


class TestRelationships:
    def test_relationship_preserves_mechanism(self):
        relationship = KPIRelationship(
            id="rel-1",
            source_kpi_id="kpi-latency",
            target_kpi_id="kpi-activation",
            relation=KPIRelationLabel.EXPECTED_TO_INFLUENCE.value,
            expected_direction="inverse",
            mechanism=(
                "Graph lookup latency raises authorization timeouts, which "
                "raise checkout failures, which lower activation."
            ),
            evidence_ids=["ev-arch"],
            confidence=0.6,
        )
        assert "authorization timeouts" in relationship.mechanism

    def test_causal_certainty_not_automatic(self):
        with pytest.raises(ValueError, match="correlation alone"):
            KPIRelationship(
                id="rel-2",
                source_kpi_id="kpi-latency",
                target_kpi_id="kpi-activation",
                relation=KPIRelationLabel.CAUSALLY_VALIDATED.value,
                mechanism="They moved together last week.",
                evidence_ids=["ev-one-chart"],
                confidence=0.5,
            )


class TestKPIHealth:
    def _health(self, observations, tgt=None, profile=None):
        return assess_kpi_health(
            definition=definition(),
            target=tgt if tgt is not None else target(),
            observations=observations,
            profile=profile,
            as_of=AS_OF,
        )

    def test_missing_observation_unknown(self):
        trend = self._health([])
        assert trend.health == KPIHealthStatus.UNKNOWN
        assert trend.latest_value is None

    def test_stale_observation_is_stale_not_healthy(self):
        trend = self._health([observation(value=75.0, days_ago=30)])
        assert trend.health == KPIHealthStatus.STALE

    def test_fresh_breach_is_breached(self):
        trend = self._health([observation(value=61.0, days_ago=1)])
        assert trend.health == KPIHealthStatus.BREACHED
        assert trend.target_distance == pytest.approx(-9.0)

    def test_deep_breach_is_critical(self):
        trend = self._health([observation(value=40.0, days_ago=1)])
        assert trend.health == KPIHealthStatus.CRITICAL

    def test_healthy_target(self):
        trend = self._health([observation(value=82.0, days_ago=1)])
        assert trend.health == KPIHealthStatus.HEALTHY

    def test_direction_uses_target_orientation(self):
        trend = self._health([
            observation("obs-a", value=55.0, days_ago=8),
            observation("obs-b", value=61.0, days_ago=1),
        ])
        assert trend.direction == "improving"
        assert trend.observation_count == 2
