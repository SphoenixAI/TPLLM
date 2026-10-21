"""Forecast evaluation and calibration (Commit 26).

Makes the scenario probabilities empirically defensible: replay history
without leakage, measure calibration and sharpness separately from
accuracy, correct probabilities only when the sample supports it, and
prove incremental value over trivial baselines.

Calibration adjusts probabilities. It never widens agent authority or
alters decision policy — those remain explicit human changes.
"""

from lltpm.evaluation.baselines import (
    BaselineComparison,
    BaselineStrategy,
    OrganizationalValueMetrics,
    build_organizational_value_metrics,
    compare_to_all_baselines,
    compare_to_baseline,
)
from lltpm.evaluation.forecast_metrics import (
    CalibrationBucket,
    ForecastQualityReport,
    ProbabilityForecastObservation,
    BrierDecomposition,
    brier_decomposition,
    brier_score,
    build_calibration_buckets,
    evaluate_forecast_quality,
    expected_calibration_error,
    log_loss,
    sharpness,
)
from lltpm.evaluation.replay import (
    HistoricalScenarioReplayCase,
    ReplayLeakageError,
    assert_replay_is_leak_free,
    build_replay_case,
    build_replay_series,
    collect_observed_events,
    reconstruct_for_replay,
)
from lltpm.evaluation.scenario_calibration import (
    MINIMUM_BUCKET_SAMPLES,
    MINIMUM_FAMILY_SAMPLES_FOR_PROMOTION,
    CalibrationAuthorityGuard,
    ScenarioProbabilityCalibration,
    apply_calibration,
    build_calibration_table,
    calibrate_with_authority_guard,
    can_promote_method,
    resolve_method_label,
)

__all__ = [
    "MINIMUM_BUCKET_SAMPLES",
    "MINIMUM_FAMILY_SAMPLES_FOR_PROMOTION",
    "BaselineComparison",
    "BaselineStrategy",
    "CalibrationAuthorityGuard",
    "CalibrationBucket",
    "ForecastQualityReport",
    "HistoricalScenarioReplayCase",
    "OrganizationalValueMetrics",
    "ProbabilityForecastObservation",
    "ReplayLeakageError",
    "ScenarioProbabilityCalibration",
    "apply_calibration",
    "assert_replay_is_leak_free",
    "BrierDecomposition",
    "brier_decomposition",
    "brier_score",
    "build_calibration_buckets",
    "build_calibration_table",
    "build_organizational_value_metrics",
    "build_replay_case",
    "build_replay_series",
    "calibrate_with_authority_guard",
    "can_promote_method",
    "collect_observed_events",
    "compare_to_all_baselines",
    "compare_to_baseline",
    "evaluate_forecast_quality",
    "expected_calibration_error",
    "log_loss",
    "reconstruct_for_replay",
    "resolve_method_label",
    "sharpness",
]
