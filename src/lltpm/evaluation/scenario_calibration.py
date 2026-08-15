"""Probability calibration from historical outcomes (Commit 26).

When TPLLM says 70%, does the event occur about 70% of the time?

Two rules keep this honest:
- Tiny samples never earn a calibration correction, and never promote
  the method label from HEURISTIC_V1.
- Event families calibrate separately. Dependency slips, decision
  delays, KPI recovery, and cross-team coordination do not share one
  global curve.

Calibration adjusts probabilities only. It can never widen agent
authority, alter a decision policy, or change a risk ceiling — those
require explicit human policy change (Commits 7-10, 22).
"""

from __future__ import annotations

import math
from enum import Enum

from pydantic import BaseModel, Field

from lltpm.evaluation.forecast_metrics import (
    ProbabilityForecastObservation,
    build_calibration_buckets,
)
from lltpm.scenarios.models import ScenarioProbabilityMethod

# A bucket needs this many samples before its observed frequency is
# trusted enough to correct a probability.
MINIMUM_BUCKET_SAMPLES = 20

# An event family needs this many total samples before the method label
# may be promoted beyond HEURISTIC_V1.
MINIMUM_FAMILY_SAMPLES_FOR_PROMOTION = 100


class ScenarioProbabilityCalibration(BaseModel):
    event_family: str
    raw_probability_bucket: tuple[float, float]
    observed_frequency: float
    sample_count: int
    confidence_interval_low: float
    confidence_interval_high: float
    trusted: bool = False
    explanation: str = ""


def _wilson_interval(
    successes: int, total: int, z: float = 1.96,
) -> tuple[float, float]:
    """Wilson score interval — well behaved at small n, unlike the
    normal approximation."""
    if total == 0:
        return 0.0, 1.0
    p = successes / total
    denominator = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denominator
    margin = (
        z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2))
    ) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def build_calibration_table(
    *,
    observations: list[ProbabilityForecastObservation],
    bucket_count: int = 10,
    minimum_bucket_samples: int = MINIMUM_BUCKET_SAMPLES,
) -> list[ScenarioProbabilityCalibration]:
    """Per-family calibration. Families never share a curve."""
    by_family: dict[str, list[ProbabilityForecastObservation]] = {}
    for observation in observations:
        by_family.setdefault(observation.event_family, []).append(observation)

    table: list[ScenarioProbabilityCalibration] = []
    for family in sorted(by_family):
        members = by_family[family]
        for bucket in build_calibration_buckets(
            members, bucket_count=bucket_count,
        ):
            in_bucket = [
                o for o in members
                if bucket.lower <= o.predicted_probability <= bucket.upper
            ]
            successes = sum(1 for o in in_bucket if o.actual_occurred)
            low, high = _wilson_interval(successes, len(in_bucket))
            trusted = bucket.sample_count >= minimum_bucket_samples
            table.append(
                ScenarioProbabilityCalibration(
                    event_family=family,
                    raw_probability_bucket=(bucket.lower, bucket.upper),
                    observed_frequency=bucket.observed_frequency,
                    sample_count=bucket.sample_count,
                    confidence_interval_low=round(low, 6),
                    confidence_interval_high=round(high, 6),
                    trusted=trusted,
                    explanation=(
                        f"{family}: predictions in "
                        f"[{bucket.lower:.1f}, {bucket.upper:.1f}] occurred "
                        f"{bucket.observed_frequency:.0%} of the time over "
                        f"{bucket.sample_count} sample(s)."
                        + (
                            ""
                            if trusted
                            else f" Below the {minimum_bucket_samples}-sample "
                                 f"threshold, so it does not adjust "
                                 f"probabilities."
                        )
                    ),
                )
            )
    return table


def apply_calibration(
    *,
    raw_probability: float,
    event_family: str,
    table: list[ScenarioProbabilityCalibration],
) -> tuple[float, bool]:
    """Return (calibrated probability, whether a correction applied).

    An untrusted bucket leaves the probability untouched — a handful of
    samples must not rewrite a forecast.
    """
    for entry in table:
        if entry.event_family != event_family:
            continue
        low, high = entry.raw_probability_bucket
        if low <= raw_probability <= high and entry.trusted:
            return entry.observed_frequency, True
    return raw_probability, False


def can_promote_method(
    *,
    observations: list[ProbabilityForecastObservation],
    event_family: str,
    minimum_samples: int = MINIMUM_FAMILY_SAMPLES_FOR_PROMOTION,
) -> bool:
    """Only real history earns the HISTORICALLY_CALIBRATED label."""
    return sum(
        1 for o in observations if o.event_family == event_family
    ) >= minimum_samples


def resolve_method_label(
    *,
    observations: list[ProbabilityForecastObservation],
    event_family: str,
    minimum_samples: int = MINIMUM_FAMILY_SAMPLES_FOR_PROMOTION,
) -> ScenarioProbabilityMethod:
    """Heuristic weights are never labeled as learned or calibrated."""
    if can_promote_method(
        observations=observations,
        event_family=event_family,
        minimum_samples=minimum_samples,
    ):
        return ScenarioProbabilityMethod.HISTORICALLY_CALIBRATED
    return ScenarioProbabilityMethod.HEURISTIC_V1


class CalibrationAuthorityGuard(BaseModel):
    """Explicit record that calibration touched probabilities only.

    Historical success must never silently widen what the agent may
    decide; that requires a human policy change.
    """
    event_family: str
    adjusted_probability: bool
    adjusted_agent_authority: bool = False
    adjusted_decision_policy: bool = False
    explanation: str = (
        "Calibration adjusts probability estimates only. Authority scope, "
        "risk ceilings, and decision policy are unchanged and can only be "
        "changed by explicit human policy revision."
    )


def calibrate_with_authority_guard(
    *,
    raw_probability: float,
    event_family: str,
    table: list[ScenarioProbabilityCalibration],
) -> tuple[float, CalibrationAuthorityGuard]:
    calibrated, adjusted = apply_calibration(
        raw_probability=raw_probability,
        event_family=event_family,
        table=table,
    )
    return calibrated, CalibrationAuthorityGuard(
        event_family=event_family,
        adjusted_probability=adjusted,
    )
