"""Forecast quality metrics and baselines."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tpllm.evaluation import (
    BaselineStrategy,
    ProbabilityForecastObservation,
    brier_decomposition,
    brier_score,
    build_calibration_buckets,
    build_organizational_value_metrics,
    compare_to_all_baselines,
    compare_to_baseline,
    evaluate_forecast_quality,
    expected_calibration_error,
    log_loss,
    sharpness,
)

T0 = datetime(2026, 10, 1, 9, 0, 0)


def _obs(
    probability: float,
    occurred: bool,
    *,
    index: int = 0,
    family: str = "dependency_slip",
) -> ProbabilityForecastObservation:
    return ProbabilityForecastObservation(
        forecast_id=f"fc-{family}-{index}",
        event_name="dependency_delivered_on_time",
        event_family=family,
        predicted_probability=probability,
        predicted_probability_confidence=0.6,
        actual_occurred=occurred,
        prediction_as_of=T0,
        outcome_observed_at=T0 + timedelta(days=14),
    )


class TestObservationInvariants:
    def test_outcome_must_follow_prediction(self):
        with pytest.raises(ValueError, match="saw its own answer"):
            ProbabilityForecastObservation(
                forecast_id="f1", event_name="e",
                predicted_probability=0.5,
                predicted_probability_confidence=0.5,
                actual_occurred=True,
                prediction_as_of=T0,
                outcome_observed_at=T0 - timedelta(days=1),
            )

    def test_probability_bounded(self):
        with pytest.raises(ValueError, match="predicted_probability"):
            _obs(1.5, True)

    def test_probability_and_confidence_are_distinct_fields(self):
        observation = _obs(0.9, True)
        assert observation.predicted_probability == 0.9
        assert observation.predicted_probability_confidence == 0.6


class TestBrierScore:
    def test_perfect_forecast_scores_zero(self):
        assert brier_score([_obs(1.0, True), _obs(0.0, False)]) == 0.0

    def test_worst_forecast_scores_one(self):
        assert brier_score([_obs(0.0, True), _obs(1.0, False)]) == 1.0

    def test_known_value(self):
        # 0.7 predicted, did not occur -> 0.49; 0.3 predicted, occurred
        # -> 0.49. Mean 0.49.
        score = brier_score([_obs(0.7, False), _obs(0.3, True)])
        assert score == pytest.approx(0.49)

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="at least one"):
            brier_score([])


class TestLogLoss:
    def test_confident_and_correct_is_low(self):
        assert log_loss([_obs(0.99, True)]) < 0.05

    def test_confident_and_wrong_is_high(self):
        assert log_loss([_obs(0.99, False)]) > 4.0

    def test_extremes_do_not_produce_infinity(self):
        assert log_loss([_obs(0.0, True)]) < float("inf")


class TestSharpness:
    def test_timid_forecaster_is_unsharp(self):
        timid = [_obs(0.51, i % 2 == 0, index=i) for i in range(10)]
        assert sharpness(timid) < 0.05

    def test_decisive_forecaster_is_sharp(self):
        decisive = [
            _obs(0.95 if i % 2 == 0 else 0.05, i % 2 == 0, index=i)
            for i in range(10)
        ]
        assert sharpness(decisive) > 0.8

    def test_accuracy_alone_would_hide_the_timid_model(self):
        # The timid model's Brier score is respectable, but it is useless
        # — which is exactly why sharpness is reported separately.
        timid = [_obs(0.51, i % 2 == 0, index=i) for i in range(10)]
        report = evaluate_forecast_quality(timid)
        assert report.brier_score < 0.3
        assert report.sharpness < 0.05
        assert "useless" in report.explanation


class TestCalibrationBuckets:
    def _spread(self):
        observations = []
        index = 0
        # 10 predictions at 0.85, 8 occur -> observed 0.8
        for i in range(10):
            observations.append(_obs(0.85, i < 8, index=index))
            index += 1
        # 10 predictions at 0.15, 2 occur -> observed 0.2
        for i in range(10):
            observations.append(_obs(0.15, i < 2, index=index))
            index += 1
        return observations

    def test_buckets_report_observed_frequency(self):
        buckets = build_calibration_buckets(self._spread())
        high = next(b for b in buckets if b.lower >= 0.8)
        low = next(b for b in buckets if b.upper <= 0.2)
        assert high.observed_frequency == pytest.approx(0.8)
        assert low.observed_frequency == pytest.approx(0.2)
        assert high.sample_count == 10

    def test_well_calibrated_model_has_low_ece(self):
        assert expected_calibration_error(self._spread()) < 0.06

    def test_miscalibrated_model_has_high_ece(self):
        overconfident = [_obs(0.95, i < 3, index=i) for i in range(10)]
        assert expected_calibration_error(overconfident) > 0.5

    def test_empty_buckets_skipped(self):
        buckets = build_calibration_buckets([_obs(0.85, True)])
        assert len(buckets) == 1

    def test_bucket_count_validated(self):
        with pytest.raises(ValueError, match="bucket_count"):
            build_calibration_buckets([_obs(0.5, True)], bucket_count=0)


class TestQualityReport:
    def test_report_separates_the_metrics(self):
        report = evaluate_forecast_quality(
            [_obs(0.9, True, index=i) for i in range(5)],
            expected_sample_count=10,
        )
        assert report.sample_count == 5
        assert report.coverage == 0.5
        assert report.base_rate == 1.0
        assert report.brier_score >= 0.0
        assert report.expected_calibration_error >= 0.0
        assert report.sharpness > 0.0

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="requires observations"):
            evaluate_forecast_quality([])


class TestBaselines:
    def _observations(self):
        # A genuinely skilful model: high when it happens, low when not.
        return [
            _obs(0.9 if i < 6 else 0.1, i < 6, index=i) for i in range(10)
        ]

    def test_model_beats_base_rate(self):
        comparison = compare_to_baseline(
            observations=self._observations(),
            strategy=BaselineStrategy.BASE_RATE,
        )
        assert comparison.beats_baseline is True
        assert comparison.skill_score > 0

    def test_useless_model_does_not_beat_base_rate(self):
        # Predicts 0.6 for everything, base rate is 0.6.
        flat = [_obs(0.6, i < 6, index=i) for i in range(10)]
        comparison = compare_to_baseline(
            observations=flat, strategy=BaselineStrategy.BASE_RATE,
        )
        assert comparison.beats_baseline is False
        assert comparison.skill_score == pytest.approx(0.0, abs=1e-6)

    def test_persistence_baseline(self):
        comparison = compare_to_baseline(
            observations=self._observations(),
            strategy=BaselineStrategy.PERSISTENCE,
            persistence_probability=0.5,
        )
        assert comparison.strategy == BaselineStrategy.PERSISTENCE
        assert comparison.beats_baseline is True

    def test_readiness_only_baseline_uses_supplied_probabilities(self):
        observations = self._observations()
        lookup = {o.forecast_id: 0.5 for o in observations}
        comparison = compare_to_baseline(
            observations=observations,
            strategy=BaselineStrategy.LAUNCH_READINESS_ONLY,
            readiness_probabilities=lookup,
        )
        assert comparison.baseline_brier == pytest.approx(0.25)

    def test_all_four_baselines_evaluated(self):
        comparisons = compare_to_all_baselines(
            observations=self._observations(),
        )
        assert len(comparisons) == 4
        assert {c.strategy for c in comparisons} == set(BaselineStrategy)

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="requires observations"):
            compare_to_baseline(
                observations=[], strategy=BaselineStrategy.BASE_RATE,
            )


class TestOrganizationalValue:
    def test_coordination_effects_measured(self):
        metrics = build_organizational_value_metrics(
            meeting_topics_considered=10, meeting_topics_scheduled=3,
            requests_considered=20, requests_already_answered=4,
            routing_attempts=10, routing_reached_correct_authority=9,
            capsules_built=8, capsules_with_full_context=6,
            kpi_breach_lead_hours=36.0,
            launch_slip_lead_hours=120.0,
        )
        assert metrics.unnecessary_meeting_topics_suppressed == 7
        assert metrics.already_answered_requests_caught == 4
        assert metrics.authority_routing_accuracy == pytest.approx(0.9)
        assert metrics.decision_context_completeness == pytest.approx(0.75)
        assert metrics.kpi_breach_lead_time_hours == 36.0
        assert "not individual output" in metrics.explanation

    def test_no_employee_metrics_exist(self):
        from tpllm.evaluation import OrganizationalValueMetrics

        fields = set(OrganizationalValueMetrics.model_fields)
        for forbidden in ("employee", "productivity", "responsiveness",
                          "performance", "individual"):
            assert not any(forbidden in f for f in fields)

    def test_zero_denominators_are_safe(self):
        metrics = build_organizational_value_metrics(
            meeting_topics_considered=0, meeting_topics_scheduled=0,
            requests_considered=0, requests_already_answered=0,
            routing_attempts=0, routing_reached_correct_authority=0,
            capsules_built=0, capsules_with_full_context=0,
        )
        assert metrics.authority_routing_accuracy == 0.0
        assert metrics.decision_context_completeness == 0.0


class TestBrierDecomposition:
    """Murphy's three-term decomposition.

    A single Brier score hides the failure that matters most here: a
    forecaster that always predicts the base rate is perfectly
    calibrated and tells you nothing. Reliability and resolution
    separate those two cases; the score alone cannot.
    """

    def _discriminating(self):
        """Confident and mostly right, in two separated groups."""
        return (
            [_obs(0.9, True, index=i) for i in range(9)]
            + [_obs(0.9, False, index=9)]
            + [_obs(0.1, False, index=10 + i) for i in range(9)]
            + [_obs(0.1, True, index=19)]
        )

    def _base_rate_only(self):
        return [_obs(0.5, i % 2 == 0, index=i) for i in range(20)]

    def _overconfident(self):
        """Says 0.9 and is right half the time: badly calibrated."""
        return [_obs(0.9, i % 2 == 0, index=i) for i in range(20)]

    def test_the_identity_holds_on_separated_forecasts(self):
        observations = self._discriminating()
        decomposition = brier_decomposition(observations)
        assert decomposition.recombined == pytest.approx(
            brier_score(observations), abs=1e-6
        )

    def test_uncertainty_is_the_base_rate_variance(self):
        decomposition = brier_decomposition(self._base_rate_only())
        assert decomposition.uncertainty == pytest.approx(0.25)

    def test_a_base_rate_forecaster_has_no_resolution(self):
        decomposition = brier_decomposition(self._base_rate_only())
        assert decomposition.resolution == pytest.approx(0.0)
        assert decomposition.reliability == pytest.approx(0.0), (
            "it is perfectly calibrated, which is exactly why calibration "
            "alone is not a quality measure"
        )

    def test_a_base_rate_forecaster_scores_the_uncertainty(self):
        observations = self._base_rate_only()
        assert brier_score(observations) == pytest.approx(
            brier_decomposition(observations).uncertainty
        )

    def test_a_discriminating_forecaster_has_resolution(self):
        decomposition = brier_decomposition(self._discriminating())
        assert decomposition.resolution > 0.1

    def test_the_base_rate_bar_is_reported(self):
        assert brier_decomposition(
            self._discriminating()
        ).is_better_than_the_base_rate
        assert not brier_decomposition(
            self._base_rate_only()
        ).is_better_than_the_base_rate

    def test_miscalibration_shows_up_as_reliability(self):
        decomposition = brier_decomposition(self._overconfident())
        assert decomposition.reliability > 0.1, (
            "saying 0.9 and being right half the time is miscalibration, "
            "and it belongs in the reliability term"
        )

    def test_the_residual_is_reported_rather_than_hidden(self):
        """The identity is exact only for finitely many distinct
        forecasts. Binning a continuous forecast leaves a within-bin
        term, and presenting an approximation as an identity would be the
        dishonest option."""
        observations = [
            _obs(0.01 + i * 0.004, i % 3 == 0, index=i) for i in range(20)
        ]
        decomposition = brier_decomposition(observations)
        assert decomposition.recombined == pytest.approx(
            brier_score(observations), abs=1e-6
        )
        assert decomposition.residual != 0.0

    def test_the_sample_and_bucket_counts_travel_with_the_numbers(self):
        """The empirical decomposition is known to be optimistic about
        reliability and resolution on small samples, so a reader needs to
        see what it was computed from."""
        decomposition = brier_decomposition(
            self._discriminating(), bucket_count=5,
        )
        assert decomposition.sample_count == 20
        assert decomposition.bucket_count == 5

    def test_it_requires_observations(self):
        with pytest.raises(ValueError, match="requires observations"):
            brier_decomposition([])

    def test_the_quality_report_carries_it(self):
        report = evaluate_forecast_quality(self._discriminating())
        assert report.decomposition is not None
        assert report.decomposition.resolution > 0.1

    def test_the_report_explains_the_decomposition(self):
        report = evaluate_forecast_quality(self._discriminating())
        assert "reliability" in report.explanation
        assert "resolution" in report.explanation

    def test_the_report_says_when_the_base_rate_was_not_beaten(self):
        report = evaluate_forecast_quality(self._base_rate_only())
        assert "did not beat saying the base rate" in report.explanation

    def test_nothing_here_measures_a_person(self):
        """The metrics evaluate the forecaster, not the forecasted."""
        report = evaluate_forecast_quality(self._discriminating())
        serialized = report.model_dump_json()
        for word in ("employee", "productivity", "person", "assignee"):
            assert word not in serialized
