"""Decide now, or learn first (Commit 27).

A decision system that can only choose between options is incomplete.
The third move — resolving the question that makes the choice obvious —
is frequently the best one, and it is invisible to any scorer that only
ranks options against each other.

This module compares the value of the information still missing against
the cost of waiting for it, and emits Commit 13-compatible research
targets rather than inventing a second research subsystem.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from lltpm.scenarios.models import InformationValueEstimate


class DecisionTimingAction(str, Enum):
    DECIDE_NOW = "decide_now"
    RESEARCH_FIRST = "research_first"
    DEFER_FOR_EVENT = "defer_for_event"


class DecisionTimingAssessment(BaseModel):
    recommended_action: DecisionTimingAction
    current_best_option_id: str | None = None
    current_best_option_confidence: float = 0.0
    value_of_information: float = 0.0
    expected_cost_of_waiting: float = 0.0
    option_separation: float = 0.0
    recommended_research_unknown_ids: list[str] = Field(default_factory=list)
    recommended_wait_until: datetime | None = None
    explanation: str = ""


class DecisionResearchNeed(BaseModel):
    """A Commit 13-compatible research target produced by a
    RESEARCH_FIRST timing decision."""
    unknown_id: str
    question: str
    why_it_matters: str
    expected_uncertainty_reduction: float
    blocking_decision_question: str
    priority: float


# How close two options must be before the choice counts as genuinely
# uncertain and worth resolving rather than guessing.
_CLOSE_CALL_SEPARATION = 0.06


def evaluate_decision_timing(
    *,
    option_analyses: list[object],
    information_values: list[InformationValueEstimate],
    cost_of_delay: float,
    policy: object,
    decision_question: str = "",
    wait_until: datetime | None = None,
) -> DecisionTimingAssessment:
    """Weigh what we would learn against what waiting costs.

    High information value plus low cost of waiting favours learning
    first; low information value plus high cost of delay favours
    deciding now.
    """
    threshold = float(
        getattr(policy, "value_of_information_threshold", 0.20)
    )
    research_allowed = bool(
        getattr(policy, "allow_research_before_decision", True)
    )

    scored = sorted(
        (
            (
                float(getattr(a, "target_success_probability", 0.0)),
                float(getattr(a, "probability_confidence", 0.0)),
                getattr(a, "option_id", "unknown"),
            )
            for a in option_analyses
        ),
        key=lambda item: (-item[0], item[2]),
    )
    best_id = scored[0][2] if scored else None
    best_confidence = scored[0][1] if scored else 0.0
    separation = (
        abs(scored[0][0] - scored[1][0]) if len(scored) > 1 else 1.0
    )

    top_information = sorted(
        information_values, key=lambda e: (-e.priority, e.unknown_id),
    )
    value_of_information = (
        max(e.expected_probability_reduction for e in information_values)
        if information_values
        else 0.0
    )
    research_ids = [e.unknown_id for e in top_information[:3]]

    close_call = separation <= _CLOSE_CALL_SEPARATION
    worth_learning = (
        value_of_information >= threshold
        and value_of_information > cost_of_delay
    )

    if worth_learning and not research_allowed:
        action = DecisionTimingAction.DEFER_FOR_EVENT
        explanation = (
            f"Information worth {value_of_information:.2f} would sharpen "
            f"this decision, but policy disallows research first; defer "
            f"until the resolving event occurs."
        )
    elif worth_learning:
        action = DecisionTimingAction.RESEARCH_FIRST
        explanation = (
            f"Resolving {', '.join(research_ids) or 'the open question'} "
            f"is worth {value_of_information:.2f} against a delay cost of "
            f"{cost_of_delay:.2f}"
            + (
                f", and the top options are separated by only "
                f"{separation:.2f}."
                if close_call
                else "."
            )
        )
    elif close_call and information_values and cost_of_delay < value_of_information:
        action = DecisionTimingAction.RESEARCH_FIRST
        explanation = (
            f"The top options are separated by only {separation:.2f}; "
            f"resolving the open question is cheaper than guessing."
        )
    else:
        action = DecisionTimingAction.DECIDE_NOW
        explanation = (
            f"Information value {value_of_information:.2f} does not clear "
            f"the {threshold:.2f} threshold against a delay cost of "
            f"{cost_of_delay:.2f}; deciding now is preferable to waiting."
        )

    return DecisionTimingAssessment(
        recommended_action=action,
        current_best_option_id=best_id,
        current_best_option_confidence=round(best_confidence, 6),
        value_of_information=round(value_of_information, 6),
        expected_cost_of_waiting=round(cost_of_delay, 6),
        option_separation=round(separation, 6),
        recommended_research_unknown_ids=(
            research_ids
            if action != DecisionTimingAction.DECIDE_NOW
            else []
        ),
        recommended_wait_until=(
            wait_until
            if action == DecisionTimingAction.DEFER_FOR_EVENT
            else None
        ),
        explanation=explanation,
    )


def build_decision_research_needs(
    *,
    timing: DecisionTimingAssessment,
    information_values: list[InformationValueEstimate],
    decision_question: str,
) -> list[DecisionResearchNeed]:
    """Convert a RESEARCH_FIRST verdict into Commit 13 research targets.

    Returns nothing when the recommendation is to decide now — the
    system does not manufacture research it does not need.
    """
    if timing.recommended_action == DecisionTimingAction.DECIDE_NOW:
        return []

    by_id = {e.unknown_id: e for e in information_values}
    needs: list[DecisionResearchNeed] = []
    for unknown_id in timing.recommended_research_unknown_ids:
        estimate = by_id.get(unknown_id)
        if estimate is None:
            continue
        needs.append(
            DecisionResearchNeed(
                unknown_id=unknown_id,
                question=(
                    f"Resolve '{unknown_id}' before deciding: "
                    f"{decision_question}"
                ),
                why_it_matters=estimate.explanation
                or "It materially affects the modeled outcome distribution.",
                expected_uncertainty_reduction=(
                    estimate.expected_probability_reduction
                ),
                blocking_decision_question=decision_question,
                priority=estimate.priority,
            )
        )
    return needs
