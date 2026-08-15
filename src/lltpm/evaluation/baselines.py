"""Baseline comparison and organizational value metrics (Commit 26).

A scenario model must demonstrate incremental value over trivially
simple predictors, otherwise its complexity is unjustified.

The organizational metrics measure the system's effect on coordination —
suppressed meeting topics, caught already-answered requests, warning
lead time. They never measure employee output.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from lltpm.evaluation.forecast_metrics import (
    ProbabilityForecastObservation,
    brier_score,
)


class BaselineStrategy(str, Enum):
    BASE_RATE = "base_rate"
    PERSISTENCE = "persistence"
    LAUNCH_READINESS_ONLY = "launch_readiness_only"
    DEPENDENCY_STATUS_ONLY = "dependency_status_only"


class BaselineComparison(BaseModel):
    strategy: BaselineStrategy
    baseline_brier: float
    model_brier: float
    skill_score: float
    beats_baseline: bool
    sample_count: int
    explanation: str = ""


def _rebase(
    observations: list[ProbabilityForecastObservation],
    probability_for,
) -> list[ProbabilityForecastObservation]:
    return [
        o.model_copy(
            update={"predicted_probability": probability_for(o)}
        )
        for o in observations
    ]


def compare_to_baseline(
    *,
    observations: list[ProbabilityForecastObservation],
    strategy: BaselineStrategy,
    persistence_probability: float | None = None,
    readiness_probabilities: dict[str, float] | None = None,
    dependency_probabilities: dict[str, float] | None = None,
) -> BaselineComparison:
    """Skill score > 0 means the model beat the baseline.

    Skill = 1 - (model Brier / baseline Brier), the standard forecasting
    convention.
    """
    if not observations:
        raise ValueError("Baseline comparison requires observations.")

    base_rate = sum(
        1 for o in observations if o.actual_occurred
    ) / len(observations)

    if strategy == BaselineStrategy.BASE_RATE:
        baseline = _rebase(observations, lambda _o: base_rate)
    elif strategy == BaselineStrategy.PERSISTENCE:
        fixed = (
            persistence_probability
            if persistence_probability is not None
            else 0.5
        )
        baseline = _rebase(observations, lambda _o: fixed)
    elif strategy == BaselineStrategy.LAUNCH_READINESS_ONLY:
        lookup = readiness_probabilities or {}
        baseline = _rebase(
            observations, lambda o: lookup.get(o.forecast_id, base_rate),
        )
    else:
        lookup = dependency_probabilities or {}
        baseline = _rebase(
            observations, lambda o: lookup.get(o.forecast_id, base_rate),
        )

    baseline_brier = brier_score(baseline)
    model_brier = brier_score(observations)
    skill = (
        1.0 - (model_brier / baseline_brier)
        if baseline_brier > 0
        else 0.0
    )

    return BaselineComparison(
        strategy=strategy,
        baseline_brier=round(baseline_brier, 6),
        model_brier=round(model_brier, 6),
        skill_score=round(skill, 6),
        beats_baseline=model_brier < baseline_brier,
        sample_count=len(observations),
        explanation=(
            f"Model Brier {model_brier:.4f} versus {strategy.value} "
            f"baseline {baseline_brier:.4f} (skill {skill:+.3f}) over "
            f"{len(observations)} observation(s)."
        ),
    )


def compare_to_all_baselines(
    *,
    observations: list[ProbabilityForecastObservation],
    **kwargs,
) -> list[BaselineComparison]:
    return [
        compare_to_baseline(
            observations=observations, strategy=strategy, **kwargs,
        )
        for strategy in BaselineStrategy
    ]


class OrganizationalValueMetrics(BaseModel):
    """The system's effect on coordination — never on people.

    Lead times are the headline: warning about a KPI breach or a launch
    slip early is the whole point.
    """
    unnecessary_meeting_topics_suppressed: int = 0
    already_answered_requests_caught: int = 0
    authority_routing_accuracy: float = 0.0
    decision_context_completeness: float = 0.0
    contradiction_detection_lead_time_hours: float | None = None
    kpi_breach_lead_time_hours: float | None = None
    launch_slip_warning_lead_time_hours: float | None = None
    explanation: str = ""


def build_organizational_value_metrics(
    *,
    meeting_topics_considered: int,
    meeting_topics_scheduled: int,
    requests_considered: int,
    requests_already_answered: int,
    routing_attempts: int,
    routing_reached_correct_authority: int,
    capsules_built: int,
    capsules_with_full_context: int,
    contradiction_lead_hours: float | None = None,
    kpi_breach_lead_hours: float | None = None,
    launch_slip_lead_hours: float | None = None,
) -> OrganizationalValueMetrics:
    suppressed = max(0, meeting_topics_considered - meeting_topics_scheduled)
    routing_accuracy = (
        routing_reached_correct_authority / routing_attempts
        if routing_attempts
        else 0.0
    )
    completeness = (
        capsules_with_full_context / capsules_built if capsules_built else 0.0
    )

    return OrganizationalValueMetrics(
        unnecessary_meeting_topics_suppressed=suppressed,
        already_answered_requests_caught=requests_already_answered,
        authority_routing_accuracy=round(routing_accuracy, 6),
        decision_context_completeness=round(completeness, 6),
        contradiction_detection_lead_time_hours=contradiction_lead_hours,
        kpi_breach_lead_time_hours=kpi_breach_lead_hours,
        launch_slip_warning_lead_time_hours=launch_slip_lead_hours,
        explanation=(
            f"{suppressed} meeting topic(s) suppressed, "
            f"{requests_already_answered} already-answered request(s) "
            f"caught, routing accuracy {routing_accuracy:.0%}. These "
            f"measure coordination effects, not individual output."
        ),
    )
