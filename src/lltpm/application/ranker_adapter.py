"""Ranker consult point — score and rank options with optional registry.

The seam between the pure ranker and the active calibration registry. The
ranker's ``tradeoff_score()`` is read, never edited: with no registry the
results are behaviorally identical to prior ranking; with a registry the
score may shift only through the bounded, scope-checked consult in
``registry.py``. Both base and adjusted scores are preserved on every
result so downstream evidence can always see what changed.
"""

from __future__ import annotations

from lltpm.application.registry import (
    ActiveCalibrationRegistry,
    CalibrationConsultContext,
    CalibrationConsultResult,
    consult_active_calibrations,
)


def score_option_with_active_calibrations(
    *,
    option: object,
    context: CalibrationConsultContext,
    registry: ActiveCalibrationRegistry | None = None,
) -> tuple[float, CalibrationConsultResult]:
    """Base score from the pure ranker, then one bounded consult."""
    if hasattr(option, "tradeoff_score") and callable(option.tradeoff_score):
        base_score = float(option.tradeoff_score())
    else:
        base_score = float(
            getattr(option, "rank_score", None)
            or getattr(option, "score", None)
            or 0.0
        )

    result = consult_active_calibrations(
        registry=registry,
        option=option,
        base_score=base_score,
        context=context,
    )
    return result.adjusted_score, result


def rank_options_with_active_calibrations(
    *,
    options: list[object],
    context: CalibrationConsultContext,
    registry: ActiveCalibrationRegistry | None = None,
) -> tuple[list[object], list[CalibrationConsultResult]]:
    """Rank by adjusted score (desc). With ``registry=None`` this is
    behaviorally equivalent to ranking by ``tradeoff_score()``."""
    scored: list[tuple[object, float, CalibrationConsultResult]] = []
    for option in options:
        adjusted_score, result = score_option_with_active_calibrations(
            option=option,
            context=context,
            registry=registry,
        )
        scored.append((option, adjusted_score, result))

    scored.sort(key=lambda item: item[1], reverse=True)
    return [item[0] for item in scored], [item[2] for item in scored]
