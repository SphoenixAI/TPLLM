"""Forecast quality metrics.

Accuracy alone is not quality. A model that predicts 0.51 for everything
can be accurate and useless, so sharpness and calibration are measured
separately from the Brier score.

Nothing here measures a person; these metrics evaluate the forecaster.
"""

from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class ProbabilityForecastObservation(BaseModel):
    """One prediction paired with what actually happened."""
    forecast_id: str
    event_name: str
    event_family: str = "general"
    predicted_probability: float
    predicted_probability_confidence: float
    actual_occurred: bool
    prediction_as_of: datetime
    outcome_observed_at: datetime

    @model_validator(mode="after")
    def _check(self) -> "ProbabilityForecastObservation":
        for name in (
            "predicted_probability", "predicted_probability_confidence",
        ):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be within [0, 1].")
        if self.outcome_observed_at <= self.prediction_as_of:
            raise ValueError(
                "The outcome must be observed after the prediction — "
                "otherwise the forecast saw its own answer."
            )
        return self


class CalibrationBucket(BaseModel):
    lower: float
    upper: float
    predicted_mean: float
    observed_frequency: float
    sample_count: int
    gap: float


class ForecastQualityReport(BaseModel):
    """Accuracy, calibration, and sharpness reported separately."""
    sample_count: int
    brier_score: float
    log_loss: float
    expected_calibration_error: float
    sharpness: float
    base_rate: float
    coverage: float
    buckets: list[CalibrationBucket] = Field(default_factory=list)
    decomposition: object | None = None
    explanation: str = ""


def brier_score(
    observations: list[ProbabilityForecastObservation],
) -> float:
    """Mean squared error between predicted probability and outcome."""
    if not observations:
        raise ValueError("Brier score requires at least one observation.")
    return sum(
        (o.predicted_probability - (1.0 if o.actual_occurred else 0.0)) ** 2
        for o in observations
    ) / len(observations)


_LOG_EPSILON = 1e-9


def log_loss(
    observations: list[ProbabilityForecastObservation],
) -> float:
    if not observations:
        raise ValueError("Log loss requires at least one observation.")
    total = 0.0
    for observation in observations:
        p = min(1.0 - _LOG_EPSILON, max(_LOG_EPSILON,
                                        observation.predicted_probability))
        total += -(
            math.log(p) if observation.actual_occurred else math.log(1.0 - p)
        )
    return total / len(observations)


def build_calibration_buckets(
    observations: list[ProbabilityForecastObservation],
    *,
    bucket_count: int = 10,
) -> list[CalibrationBucket]:
    """Reliability by probability bucket: when the model says 0.7, does
    the event occur about 70% of the time?"""
    if bucket_count <= 0:
        raise ValueError("bucket_count must be positive.")
    width = 1.0 / bucket_count
    buckets: list[CalibrationBucket] = []

    for index in range(bucket_count):
        lower = index * width
        upper = (index + 1) * width
        if index == bucket_count - 1:
            members = [
                o for o in observations
                if lower <= o.predicted_probability <= upper
            ]
        else:
            members = [
                o for o in observations
                if lower <= o.predicted_probability < upper
            ]
        if not members:
            continue
        predicted_mean = sum(
            o.predicted_probability for o in members
        ) / len(members)
        observed = sum(
            1 for o in members if o.actual_occurred
        ) / len(members)
        buckets.append(
            CalibrationBucket(
                lower=round(lower, 4),
                upper=round(upper, 4),
                predicted_mean=round(predicted_mean, 6),
                observed_frequency=round(observed, 6),
                sample_count=len(members),
                gap=round(abs(predicted_mean - observed), 6),
            )
        )
    return buckets


def expected_calibration_error(
    observations: list[ProbabilityForecastObservation],
    *,
    bucket_count: int = 10,
) -> float:
    """Sample-weighted mean gap between predicted and observed rates."""
    buckets = build_calibration_buckets(
        observations, bucket_count=bucket_count,
    )
    if not buckets:
        return 0.0
    total = sum(b.sample_count for b in buckets)
    return sum(b.gap * b.sample_count for b in buckets) / total


class BrierDecomposition(BaseModel):
    """Murphy's three-term decomposition of the Brier score.

    ``reliability − resolution + uncertainty``. The terms answer three
    different questions that one number cannot:

    - **reliability** — when the forecaster said 0.7, did it happen 70%
      of the time? Lower is better; it is miscalibration.
    - **resolution** — did the forecaster distinguish cases at all?
      Higher is better. A forecaster that always says the base rate has
      zero resolution and perfect reliability, which is the failure a
      single score hides.
    - **uncertainty** — the base rate's own variance. It depends on the
      world, not the forecaster, and is the score a constant base-rate
      forecast would achieve.

    ``residual`` is not cosmetic. The identity is exact only for a
    forecaster emitting finitely many distinct probabilities; binning a
    continuous forecast leaves a within-bin variance term. Reporting it
    is better than quietly presenting an approximation as an identity.

    The empirical decomposition is also known to be optimistic about
    reliability and resolution on small samples, so ``sample_count`` and
    ``bucket_count`` travel with the numbers.
    """
    reliability: float
    resolution: float
    uncertainty: float
    residual: float
    sample_count: int
    bucket_count: int

    @property
    def recombined(self) -> float:
        return (
            self.reliability - self.resolution + self.uncertainty
            + self.residual
        )

    @property
    def is_better_than_the_base_rate(self) -> bool:
        """Whether the forecaster beat saying the base rate every time.

        The honest bar. A model that cannot clear it is not forecasting,
        however good its Brier score looks next to nothing.
        """
        return self.resolution > self.reliability


def brier_decomposition(
    observations: list[ProbabilityForecastObservation],
    *,
    bucket_count: int = 10,
) -> BrierDecomposition:
    """Split the Brier score into reliability, resolution and uncertainty."""
    if not observations:
        raise ValueError("A decomposition requires observations.")

    total = len(observations)
    base_rate = sum(1 for o in observations if o.actual_occurred) / total
    buckets = build_calibration_buckets(
        observations, bucket_count=bucket_count,
    )

    reliability = sum(
        b.sample_count * (b.predicted_mean - b.observed_frequency) ** 2
        for b in buckets
    ) / total
    resolution = sum(
        b.sample_count * (b.observed_frequency - base_rate) ** 2
        for b in buckets
    ) / total
    uncertainty = base_rate * (1.0 - base_rate)
    score = brier_score(observations)
    residual = score - (reliability - resolution + uncertainty)

    return BrierDecomposition(
        reliability=round(reliability, 6),
        resolution=round(resolution, 6),
        uncertainty=round(uncertainty, 6),
        residual=round(residual, 6),
        sample_count=total,
        bucket_count=bucket_count,
    )


def sharpness(
    observations: list[ProbabilityForecastObservation],
) -> float:
    """How far predictions stray from the timid 0.5.

    A model that always says 0.51 scores near zero here even if its Brier
    score looks respectable — which is exactly the failure mode accuracy
    alone hides.
    """
    if not observations:
        return 0.0
    return sum(
        abs(o.predicted_probability - 0.5) for o in observations
    ) / len(observations) * 2.0


def evaluate_forecast_quality(
    observations: list[ProbabilityForecastObservation],
    *,
    bucket_count: int = 10,
    expected_sample_count: int | None = None,
) -> ForecastQualityReport:
    if not observations:
        raise ValueError("Forecast evaluation requires observations.")

    score = brier_score(observations)
    loss = log_loss(observations)
    ece = expected_calibration_error(
        observations, bucket_count=bucket_count,
    )
    sharp = sharpness(observations)
    base_rate = sum(
        1 for o in observations if o.actual_occurred
    ) / len(observations)
    coverage = (
        len(observations) / expected_sample_count
        if expected_sample_count
        else 1.0
    )

    decomposition = brier_decomposition(
        observations, bucket_count=bucket_count,
    )

    return ForecastQualityReport(
        sample_count=len(observations),
        brier_score=round(score, 6),
        log_loss=round(loss, 6),
        expected_calibration_error=round(ece, 6),
        sharpness=round(sharp, 6),
        base_rate=round(base_rate, 6),
        coverage=round(min(1.0, coverage), 6),
        buckets=build_calibration_buckets(
            observations, bucket_count=bucket_count,
        ),
        decomposition=decomposition,
        explanation=(
            f"Brier {score:.3f} = reliability {decomposition.reliability:.3f}"
            f" − resolution {decomposition.resolution:.3f} + uncertainty "
            f"{decomposition.uncertainty:.3f}"
            + (
                f" (+{decomposition.residual:.3f} left by binning)"
                if abs(decomposition.residual) >= 5e-4 else ""
            )
            + f". ECE {ece:.3f}, sharpness {sharp:.3f} over "
            f"{len(observations)} observation(s). Sharpness and resolution "
            f"are reported separately because an always-0.5 forecaster can "
            f"score well on accuracy while being useless."
            + (
                "" if decomposition.is_better_than_the_base_rate
                else " This forecaster did not beat saying the base rate "
                     "every time."
            )
        ),
    )
