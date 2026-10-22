"""Decision utility over scenario outcomes.

Utility is deliberately not purely financial. An outcome's utility may
express objective advancement, launch success, technical risk, customer
impact, commitment preservation, or reversibility — and every utility
states its rationale so the trade-off stays inspectable.

Expected value is computed here but is never the sole decision criterion;
``scenario_decisions`` combines it with tail risk, regret, optionality,
and recoverability under an explicit posture.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from lltpm.scenarios.models import ScenarioLattice
from lltpm.scenarios.probability import probability_of_outcome


class ScenarioOutcomeUtility(BaseModel):
    """What one modeled outcome is worth, and why.

    Utility is bounded [-1, 1]: negative for outcomes the organization
    would rather avoid, positive for those it wants.
    """
    outcome_id: str
    utility: float
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "ScenarioOutcomeUtility":
        if not (-1.0 <= self.utility <= 1.0):
            raise ValueError("Outcome utility must be within [-1, 1].")
        if not self.rationale.strip():
            raise ValueError(
                "An outcome utility must state its rationale — hidden "
                "weighting is not inspectable."
            )
        return self


def default_outcome_utilities() -> list[ScenarioOutcomeUtility]:
    """A transparent starting set. Callers are expected to override with
    utilities grounded in their own commitments."""
    return [
        ScenarioOutcomeUtility(
            outcome_id="launch_on_time",
            utility=1.0,
            rationale="The launch commitment is met.",
        ),
        ScenarioOutcomeUtility(
            outcome_id="launch_slips",
            utility=-0.6,
            rationale=(
                "The launch commitment is missed; downstream integrations "
                "and customer expectations are affected."
            ),
        ),
    ]


def outcome_distribution(
    *,
    lattice: ScenarioLattice,
    outcome_ids: list[str],
) -> dict[str, float]:
    """Probability of each named outcome across the lattice.

    Uses the lattice's path aggregation, so convergent paths are summed
    without double-counting.
    """
    return {
        outcome_id: probability_of_outcome(
            lattice=lattice, outcome_label=outcome_id,
        )[0]
        for outcome_id in sorted(outcome_ids)
    }


def calculate_expected_utility_from_lattice(
    *,
    lattice: ScenarioLattice,
    outcome_utilities: list[ScenarioOutcomeUtility],
) -> tuple[float, float]:
    """Return (expected utility, covered probability mass).

    The covered mass is reported because paths ending without a labeled
    outcome contribute no utility — pretending otherwise would overstate
    confidence in the expectation.
    """
    if not outcome_utilities:
        return 0.0, 0.0
    utilities = {u.outcome_id: u.utility for u in outcome_utilities}
    distribution = outcome_distribution(
        lattice=lattice, outcome_ids=list(utilities),
    )
    covered = sum(distribution.values())
    if covered <= 0.0:
        return 0.0, 0.0
    expected = sum(
        probability * utilities[outcome_id]
        for outcome_id, probability in distribution.items()
    )
    return expected, min(1.0, covered)


def normalize_utility_to_unit(utility: float) -> float:
    """Map utility from [-1, 1] onto [0, 1] for score composition."""
    return max(0.0, min(1.0, (utility + 1.0) / 2.0))


def calculate_expected_option_utility(
    *,
    analysis: object,
    outcome_utilities: list[ScenarioOutcomeUtility],
) -> float:
    """Expected utility for one option's analysed future distribution.

    Accepts the analysis duck-typed so this module stays usable before
    ``OptionScenarioAnalysis`` is constructed.
    """
    success = float(getattr(analysis, "target_success_probability", 0.0))
    severe = float(getattr(analysis, "severe_failure_probability", 0.0))

    utilities = {u.outcome_id: u.utility for u in outcome_utilities}
    success_utility = utilities.get("launch_on_time", 1.0)
    failure_utility = utilities.get("launch_slips", -0.6)

    remainder = max(0.0, 1.0 - success - severe)
    # Unlabeled remainder is treated as neutral rather than favourable.
    return success * success_utility + severe * failure_utility + remainder * 0.0
