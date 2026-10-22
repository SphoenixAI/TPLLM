"""Calibration, sample thresholds, per-family
segmentation, and the authority guard."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tpllm.evaluation import (
    MINIMUM_BUCKET_SAMPLES,
    MINIMUM_FAMILY_SAMPLES_FOR_PROMOTION,
    ProbabilityForecastObservation,
    apply_calibration,
    build_calibration_table,
    calibrate_with_authority_guard,
    can_promote_method,
    resolve_method_label,
)
from tpllm.scenarios import ScenarioProbabilityMethod

T0 = datetime(2026, 10, 1, 9, 0, 0)


def _obs(probability, occurred, *, index=0, family="dependency_slip"):
    return ProbabilityForecastObservation(
        forecast_id=f"fc-{family}-{index}",
        event_name="event", event_family=family,
        predicted_probability=probability,
        predicted_probability_confidence=0.6,
        actual_occurred=occurred,
        prediction_as_of=T0,
        outcome_observed_at=T0 + timedelta(days=7),
    )


def _family(name, *, count, probability, occurrence_rate):
    """`count` predictions at `probability`, occurring at the given rate."""
    occurrences = int(round(count * occurrence_rate))
    return [
        _obs(probability, i < occurrences, index=i, family=name)
        for i in range(count)
    ]


class TestCalibrationTable:
    def test_bucket_reports_observed_frequency(self):
        # 40 predictions at 0.85; only 60% actually occur.
        observations = _family(
            "dependency_slip", count=40, probability=0.85,
            occurrence_rate=0.6,
        )
        table = build_calibration_table(observations=observations)
        entry = next(e for e in table if e.raw_probability_bucket[0] >= 0.8)
        assert entry.observed_frequency == pytest.approx(0.6)
        assert entry.sample_count == 40
        assert entry.trusted is True

    def test_confidence_interval_present(self):
        table = build_calibration_table(
            observations=_family(
                "dependency_slip", count=40, probability=0.85,
                occurrence_rate=0.6,
            ),
        )
        entry = table[0]
        assert entry.confidence_interval_low < entry.observed_frequency
        assert entry.confidence_interval_high > entry.observed_frequency

    def test_small_sample_interval_is_wide(self):
        small = build_calibration_table(
            observations=_family(
                "dependency_slip", count=4, probability=0.85,
                occurrence_rate=0.5,
            ),
        )[0]
        large = build_calibration_table(
            observations=_family(
                "dependency_slip", count=200, probability=0.85,
                occurrence_rate=0.5,
            ),
        )[0]
        small_width = (
            small.confidence_interval_high - small.confidence_interval_low
        )
        large_width = (
            large.confidence_interval_high - large.confidence_interval_low
        )
        assert small_width > large_width


class TestSampleThresholds:
    def test_tiny_sample_is_not_trusted(self):
        table = build_calibration_table(
            observations=_family(
                "dependency_slip", count=3, probability=0.85,
                occurrence_rate=0.33,
            ),
        )
        assert all(not e.trusted for e in table)
        assert "does not adjust probabilities" in table[0].explanation

    def test_tiny_sample_does_not_correct_the_probability(self):
        table = build_calibration_table(
            observations=_family(
                "dependency_slip", count=3, probability=0.85,
                occurrence_rate=0.0,
            ),
        )
        calibrated, adjusted = apply_calibration(
            raw_probability=0.85, event_family="dependency_slip",
            table=table,
        )
        # Three samples must not rewrite a forecast.
        assert calibrated == 0.85
        assert adjusted is False

    def test_sufficient_sample_does_correct(self):
        table = build_calibration_table(
            observations=_family(
                "dependency_slip", count=40, probability=0.85,
                occurrence_rate=0.6,
            ),
        )
        calibrated, adjusted = apply_calibration(
            raw_probability=0.85, event_family="dependency_slip",
            table=table,
        )
        assert adjusted is True
        assert calibrated == pytest.approx(0.6)

    def test_threshold_constants_are_explicit(self):
        assert MINIMUM_BUCKET_SAMPLES == 20
        assert MINIMUM_FAMILY_SAMPLES_FOR_PROMOTION == 100


class TestFamilySegmentation:
    def _mixed(self):
        # Dependency slips are overpredicted; KPI recovery is well
        # calibrated. They must not share a curve.
        return (
            _family("dependency_slip", count=40, probability=0.85,
                    occurrence_rate=0.4)
            + _family("kpi_recovery", count=40, probability=0.85,
                      occurrence_rate=0.85)
        )

    def test_families_calibrate_separately(self):
        table = build_calibration_table(observations=self._mixed())
        families = {e.event_family for e in table}
        assert families == {"dependency_slip", "kpi_recovery"}

    def test_one_family_correction_does_not_leak_to_another(self):
        table = build_calibration_table(observations=self._mixed())
        dependency, _ = apply_calibration(
            raw_probability=0.85, event_family="dependency_slip",
            table=table,
        )
        kpi, _ = apply_calibration(
            raw_probability=0.85, event_family="kpi_recovery", table=table,
        )
        assert dependency == pytest.approx(0.4)
        assert kpi == pytest.approx(0.85)
        assert dependency != kpi

    def test_unknown_family_is_left_alone(self):
        table = build_calibration_table(observations=self._mixed())
        calibrated, adjusted = apply_calibration(
            raw_probability=0.7, event_family="never_seen", table=table,
        )
        assert calibrated == 0.7
        assert adjusted is False


class TestMethodPromotion:
    def test_tiny_history_stays_heuristic(self):
        observations = _family(
            "dependency_slip", count=10, probability=0.8,
            occurrence_rate=0.8,
        )
        assert can_promote_method(
            observations=observations, event_family="dependency_slip",
        ) is False
        assert resolve_method_label(
            observations=observations, event_family="dependency_slip",
        ) == ScenarioProbabilityMethod.HEURISTIC_V1

    def test_sufficient_history_promotes(self):
        observations = _family(
            "dependency_slip", count=150, probability=0.8,
            occurrence_rate=0.8,
        )
        assert resolve_method_label(
            observations=observations, event_family="dependency_slip",
        ) == ScenarioProbabilityMethod.HISTORICALLY_CALIBRATED

    def test_promotion_is_per_family(self):
        observations = (
            _family("dependency_slip", count=150, probability=0.8,
                    occurrence_rate=0.8)
            + _family("kpi_recovery", count=5, probability=0.8,
                      occurrence_rate=0.8)
        )
        assert resolve_method_label(
            observations=observations, event_family="dependency_slip",
        ) == ScenarioProbabilityMethod.HISTORICALLY_CALIBRATED
        # A well-calibrated family must not vouch for a sparse one.
        assert resolve_method_label(
            observations=observations, event_family="kpi_recovery",
        ) == ScenarioProbabilityMethod.HEURISTIC_V1

    def test_heuristic_is_never_called_learned(self):
        observations = _family(
            "dependency_slip", count=1000, probability=0.8,
            occurrence_rate=0.8,
        )
        # Even abundant history yields CALIBRATED, never LEARNED_MODEL —
        # no model was trained.
        assert resolve_method_label(
            observations=observations, event_family="dependency_slip",
        ) != ScenarioProbabilityMethod.LEARNED_MODEL


class TestAuthorityGuard:
    def _table(self):
        return build_calibration_table(
            observations=_family(
                "dependency_slip", count=40, probability=0.85,
                occurrence_rate=0.6,
            ),
        )

    def test_calibration_adjusts_probability_only(self):
        calibrated, guard = calibrate_with_authority_guard(
            raw_probability=0.85, event_family="dependency_slip",
            table=self._table(),
        )
        assert calibrated == pytest.approx(0.6)
        assert guard.adjusted_probability is True
        assert guard.adjusted_agent_authority is False
        assert guard.adjusted_decision_policy is False

    def test_guard_states_the_boundary(self):
        _, guard = calibrate_with_authority_guard(
            raw_probability=0.85, event_family="dependency_slip",
            table=self._table(),
        )
        assert "Authority scope" in guard.explanation
        assert "human policy revision" in guard.explanation

    def test_calibration_source_touches_no_policy(self):
        import ast
        import inspect
        import tpllm.evaluation.scenario_calibration as mod

        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if not isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef,
                 ast.AsyncFunctionDef),
            ):
                continue
            body = node.body
            if (
                body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body.pop(0)
        source = ast.unparse(tree)

        for forbidden in (
            "maximum_autonomous_risk", "minimum_autonomous_confidence",
            "autonomous_allowed_decision_types", "OrganizationalDecisionMode",
            "can_agent_finalize_decision", "tpllm.coordination",
        ):
            assert forbidden not in source, (
                f"calibration must not reference '{forbidden}' — historical "
                f"success can never widen agent authority."
            )

    def test_historical_success_does_not_alter_decision_policy(self):
        # A family with a perfect track record still adjusts nothing but
        # the probability.
        perfect = _family(
            "dependency_slip", count=200, probability=0.9,
            occurrence_rate=0.9,
        )
        table = build_calibration_table(observations=perfect)
        _, guard = calibrate_with_authority_guard(
            raw_probability=0.9, event_family="dependency_slip", table=table,
        )
        assert guard.adjusted_agent_authority is False
        assert guard.adjusted_decision_policy is False
