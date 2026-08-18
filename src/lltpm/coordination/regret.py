"""Decision regret (Commit 27).

Regret asks a different question than expected value:

    If the world turns out the way it turns out, how much worse off are
    we for having chosen this option instead of the best one?

Expected regret aggregates that gap across modeled futures; worst-case
regret asks it under the least favourable realized world. An option can
have a respectable expected value and still carry the highest regret,
which is exactly why both are reported.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lltpm.coordination.decision_utility import (
    ScenarioOutcomeUtility,
    calculate_expected_option_utility,
)


class DecisionRegretAssessment(BaseModel):
    option_id: str
    expected_regret: float
    worst_case_regret: float
    regret_confidence: float
    dominant_regret_path_ids: list[str] = Field(default_factory=list)
    opportunity_cost_summary: str = ""


def _worst_case_utility(
    analysis: object,
    outcome_utilities: list[ScenarioOutcomeUtility],
) -> float:
    """Utility under the option's least favourable credible world."""
    utilities = {u.outcome_id: u.utility for u in outcome_utilities}
    failure_utility = utilities.get("launch_slips", -0.6)
    severe = float(getattr(analysis, "severe_failure_probability", 0.0))
    recoverability = float(getattr(analysis, "recoverability", 0.0))
    # Recoverability softens the worst case: a failure the organization
    # can climb out of is not as bad as one it cannot.
    return failure_utility * (1.0 - recoverability) if severe > 0 else 0.0


def calculate_decision_regret(
    *,
    option_analyses: list[object],
    outcome_utilities: list[ScenarioOutcomeUtility],
) -> list[DecisionRegretAssessment]:
    """Regret of each option relative to the best available option.

    The best option always has zero expected regret by construction —
    regret is a comparison, not an absolute property.
    """
    if not option_analyses:
        return []

    expected = {
        getattr(a, "option_id", f"option-{index}"):
            calculate_expected_option_utility(
                analysis=a, outcome_utilities=outcome_utilities,
            )
        for index, a in enumerate(option_analyses)
    }
    worst = {
        getattr(a, "option_id", f"option-{index}"):
            _worst_case_utility(a, outcome_utilities)
        for index, a in enumerate(option_analyses)
    }

    best_expected = max(expected.values())
    best_worst = max(worst.values())
    best_option_id = sorted(
        expected, key=lambda k: (-expected[k], k),
    )[0]

    assessments: list[DecisionRegretAssessment] = []
    for analysis in option_analyses:
        option_id = getattr(analysis, "option_id", "unknown")
        expected_regret = max(0.0, best_expected - expected[option_id])
        worst_case_regret = max(0.0, best_worst - worst[option_id])
        confidence = float(
            getattr(analysis, "probability_confidence", 0.0)
        )
        failure_paths = list(
            getattr(analysis, "dominant_failure_paths", []) or []
        )

        if option_id == best_option_id:
            summary = (
                "Highest expected utility among modeled options; no "
                "opportunity cost relative to the alternatives."
            )
        else:
            summary = (
                f"Choosing {option_id} forgoes {expected_regret:.3f} of "
                f"expected utility relative to {best_option_id}, and "
                f"{worst_case_regret:.3f} under the least favourable "
                f"modeled world."
            )

        assessments.append(
            DecisionRegretAssessment(
                option_id=option_id,
                expected_regret=round(expected_regret, 6),
                worst_case_regret=round(worst_case_regret, 6),
                regret_confidence=round(confidence, 6),
                dominant_regret_path_ids=failure_paths[:5],
                opportunity_cost_summary=summary,
            )
        )

    return sorted(assessments, key=lambda a: a.option_id)


def lowest_regret_option(
    assessments: list[DecisionRegretAssessment],
) -> str | None:
    if not assessments:
        return None
    return sorted(
        assessments,
        key=lambda a: (a.expected_regret, a.worst_case_regret, a.option_id),
    )[0].option_id
