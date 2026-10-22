"""Portfolio allocation comparison and regret.

Choosing who gets the contended resource is a portfolio decision, not a
project decision. This module compares candidate allocations by their
simulated portfolio outcomes and reports the regret of each — how much
better the portfolio would have done under the best alternative.

The invariant that governs everything here: a portfolio recommendation
must state the displaced impact. Optimizing one project at another's
expense is sometimes the right call; doing it invisibly never is, so a
recommendation that cannot name what the displaced projects pay is
rejected at construction rather than merely discouraged.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from tpllm.portfolio.scenarios import (
    PortfolioAllocationDecision,
    PortfolioSimulationState,
)
from tpllm.portfolio.simulation import (
    PortfolioSimulationResult,
    simulate_portfolio_allocation,
)
from tpllm.scenarios.models import ScenarioHorizon


class PortfolioRegretAssessment(BaseModel):
    """What choosing this allocation costs relative to the best one."""
    allocation_id: str
    expected_value: float
    worst_case_value: float
    best_alternative_id: str | None = None
    best_alternative_expected_value: float = 0.0
    regret: float = 0.0
    worst_case_regret: float = 0.0
    displaced_project_ids: list[str] = Field(default_factory=list)
    displaced_project_impacts: dict[str, float] = Field(default_factory=dict)
    explanation: str = ""

    @model_validator(mode="after")
    def _check(self) -> "PortfolioRegretAssessment":
        if self.regret < 0:
            raise ValueError("Regret cannot be negative.")
        missing = set(self.displaced_project_ids) - set(
            self.displaced_project_impacts
        )
        if missing:
            raise ValueError(
                f"Displaced projects must have their impact stated: "
                f"{sorted(missing)}."
            )
        return self


class PortfolioAllocationRecommendation(BaseModel):
    """A portfolio choice, with the bill for it attached.

    ``displaced_project_impacts`` is validated against
    ``displaced_project_ids``: this object cannot exist in a state where
    a project was quietly deprioritized.
    """
    id: str
    resource_id: str
    recommended_allocation_id: str
    considered_allocation_ids: list[str] = Field(default_factory=list)
    expected_portfolio_value: float = 0.0
    worst_case_portfolio_value: float = 0.0
    per_project_expected_impact: dict[str, float] = Field(default_factory=dict)
    displaced_project_ids: list[str] = Field(default_factory=list)
    displaced_project_impacts: dict[str, float] = Field(default_factory=dict)
    alternative_routes: dict[str, list[str]] = Field(default_factory=dict)
    projects_without_alternative: list[str] = Field(default_factory=list)
    regret_assessments: list[PortfolioRegretAssessment] = Field(
        default_factory=list
    )
    unmodeled_probability_mass: float = 0.0
    confidence: float = 0.0
    rationale: str = ""

    @model_validator(mode="after")
    def _check(self) -> "PortfolioAllocationRecommendation":
        missing = set(self.displaced_project_ids) - set(
            self.displaced_project_impacts
        )
        if missing:
            raise ValueError(
                f"A portfolio recommendation must state displaced impact; "
                f"{sorted(missing)} were displaced without one."
            )
        uncovered = set(self.displaced_project_ids) - set(
            self.per_project_expected_impact
        )
        if uncovered:
            raise ValueError(
                f"Displaced projects must appear in the portfolio outcome "
                f"summary: {sorted(uncovered)}."
            )
        return self


def compare_allocations(
    *,
    portfolio_state: PortfolioSimulationState,
    allocations: list[PortfolioAllocationDecision],
    horizon: ScenarioHorizon,
    runs: int = 500,
    seed: int = 12345,
) -> list[PortfolioSimulationResult]:
    """Simulate every candidate allocation on the same seed.

    Sharing the seed is deliberate: the allocations then face the same
    drawn futures, so a difference between them is a difference in the
    decision rather than in the luck of the draw.
    """
    if not allocations:
        return []
    return [
        simulate_portfolio_allocation(
            portfolio_state=portfolio_state,
            allocation=allocation,
            horizon=horizon,
            runs=runs,
            seed=seed,
        )
        for allocation in allocations
    ]


def assess_allocation_regret(
    results: list[PortfolioSimulationResult],
) -> list[PortfolioRegretAssessment]:
    """Regret of each allocation against the best expected value."""
    if not results:
        return []
    best = max(
        results, key=lambda r: (r.portfolio_expected_value, r.allocation_id),
    )
    best_worst_case = max(r.worst_case_portfolio_value for r in results)

    assessments: list[PortfolioRegretAssessment] = []
    for result in results:
        regret = round(
            max(
                0.0,
                best.portfolio_expected_value
                - result.portfolio_expected_value,
            ),
            6,
        )
        worst_case_regret = round(
            max(0.0, best_worst_case - result.worst_case_portfolio_value), 6,
        )
        assessments.append(
            PortfolioRegretAssessment(
                allocation_id=result.allocation_id,
                expected_value=result.portfolio_expected_value,
                worst_case_value=result.worst_case_portfolio_value,
                best_alternative_id=(
                    None if result.allocation_id == best.allocation_id
                    else best.allocation_id
                ),
                best_alternative_expected_value=(
                    best.portfolio_expected_value
                ),
                regret=regret,
                worst_case_regret=worst_case_regret,
                displaced_project_ids=list(result.displaced_project_ids),
                displaced_project_impacts=dict(
                    result.displaced_project_impacts
                ),
                explanation=(
                    f"Allocation '{result.allocation_id}' has an expected "
                    f"portfolio value of "
                    f"{result.portfolio_expected_value:.3f} and a worst "
                    f"modelled case of "
                    f"{result.worst_case_portfolio_value:.3f}."
                    + (
                        " This is the highest-value allocation considered."
                        if regret == 0.0
                        else f" Choosing it instead of "
                             f"'{best.allocation_id}' costs {regret:.3f} in "
                             f"expected portfolio value."
                    )
                ),
            )
        )
    return sorted(assessments, key=lambda a: (a.regret, a.allocation_id))


def recommend_portfolio_allocation(
    *,
    portfolio_state: PortfolioSimulationState,
    allocations: list[PortfolioAllocationDecision],
    horizon: ScenarioHorizon,
    runs: int = 500,
    seed: int = 12345,
    risk_aversion: float = 0.0,
) -> PortfolioAllocationRecommendation:
    """Pick an allocation and state what every project gets from it.

    ``risk_aversion`` blends the worst modelled portfolio value into the
    ranking. At 0 the choice is by expected value; raising it prefers
    allocations whose bad futures are less bad, which is often the right
    posture when one project has no alternative route.
    """
    if not allocations:
        raise ValueError("At least one candidate allocation is required.")
    if not (0.0 <= risk_aversion <= 1.0):
        raise ValueError("risk_aversion must be within [0, 1].")

    results = compare_allocations(
        portfolio_state=portfolio_state,
        allocations=allocations,
        horizon=horizon,
        runs=runs,
        seed=seed,
    )

    def score(result: PortfolioSimulationResult) -> float:
        return round(
            (1.0 - risk_aversion) * result.portfolio_expected_value
            + risk_aversion * result.worst_case_portfolio_value,
            9,
        )

    chosen = max(results, key=lambda r: (score(r), r.allocation_id))
    assessments = assess_allocation_regret(results)

    per_project = {
        o.project_id: o.expected_impact for o in chosen.project_outcomes
    }
    stranded = sorted(
        o.project_id for o in chosen.project_outcomes
        if o.was_displaced and not o.alternative_routes
    )
    confidences = [
        o.probability_confidence for o in chosen.project_outcomes
    ]

    return PortfolioAllocationRecommendation(
        id=f"palloc-rec-{chosen.resource_id}",
        resource_id=chosen.resource_id,
        recommended_allocation_id=chosen.allocation_id,
        considered_allocation_ids=sorted(r.allocation_id for r in results),
        expected_portfolio_value=chosen.portfolio_expected_value,
        worst_case_portfolio_value=chosen.worst_case_portfolio_value,
        per_project_expected_impact=per_project,
        displaced_project_ids=list(chosen.displaced_project_ids),
        displaced_project_impacts=dict(chosen.displaced_project_impacts),
        alternative_routes=dict(chosen.alternative_routes),
        projects_without_alternative=stranded,
        regret_assessments=assessments,
        unmodeled_probability_mass=chosen.unmodeled_probability_mass,
        confidence=round(min(confidences), 6) if confidences else 0.0,
        rationale=explain_portfolio_allocation(
            result=chosen,
            considered=len(results),
            risk_aversion=risk_aversion,
            stranded=stranded,
        ),
    )


def explain_portfolio_allocation(
    *,
    result: PortfolioSimulationResult,
    considered: int,
    risk_aversion: float,
    stranded: list[str],
) -> str:
    """State the choice, the cost, and who carries it."""
    served_projects = sorted(
        o.project_id for o in result.project_outcomes if not o.was_displaced
    )
    parts = [
        f"Of {considered} allocation(s) considered for "
        f"{result.resource_id}, '{result.allocation_id}' has the best "
        f"portfolio outcome"
        + (
            f" at a risk aversion of {risk_aversion:.2f}."
            if risk_aversion > 0 else " by expected value."
        ),
    ]
    if served_projects:
        parts.append(f"Prioritize {', '.join(served_projects)}.")
    for project_id in result.displaced_project_ids:
        routes = result.alternative_routes.get(project_id) or []
        cost = result.displaced_project_impacts.get(project_id, 0.0)
        parts.append(
            f"{project_id} waits at a cost of {cost:.2f}"
            + (
                f", routed via {routes[0]}."
                if routes
                else " with no alternative route, so it absorbs the delay "
                     "directly."
            )
        )
    if stranded:
        parts.append(
            f"{', '.join(stranded)} should be told before the fact, not "
            f"after."
        )
    if result.unmodeled_probability_mass > 0:
        parts.append(
            f"{result.unmodeled_probability_mass:.3f} of probability mass "
            f"lies outside the modelled futures; these are the material "
            f"scenarios, not all possible ones."
        )
    return " ".join(parts)


def portfolio_value_by_allocation(
    results: list[PortfolioSimulationResult],
) -> dict[str, float]:
    return {
        r.allocation_id: r.portfolio_expected_value
        for r in sorted(results, key=lambda r: r.allocation_id)
    }
