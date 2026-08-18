"""Shared scenario factors across projects (Commit 29).

If A, B, and C all depend on Platform approval capacity, that is *one*
uncertainty, not three. Sampling it three times would manufacture
diversification the organization does not actually have and would make
the portfolio look far safer than it is.

This module keeps shared uncertainty shared, and keeps genuinely
independent per-project uncertainty independent.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class SharedScenarioFactor(BaseModel):
    """An uncertainty that several projects experience together.

    ``independent_group`` marks factors that are really the same
    underlying situation, so the sampler counts them once.
    """
    id: str
    factor_type: str
    description: str = ""
    affected_project_ids: list[str] = Field(default_factory=list)
    affected_resource_ids: list[str] = Field(default_factory=list)
    probability: float
    probability_confidence: float
    evidence_ids: list[str] = Field(default_factory=list)
    independent_group: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "SharedScenarioFactor":
        for name in ("probability", "probability_confidence"):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be within [0, 1].")
        return self

    @property
    def is_shared(self) -> bool:
        return len(set(self.affected_project_ids)) > 1


def deduplicate_shared_factors(
    factors: list[SharedScenarioFactor],
) -> list[SharedScenarioFactor]:
    """Collapse each independent_group to its single strongest member.

    Three reports that Platform is unavailable Thursday describe one
    situation; treating them as three independent risks would triple a
    risk that exists once.
    """
    grouped: dict[str, SharedScenarioFactor] = {}
    ungrouped: list[SharedScenarioFactor] = []
    for factor in factors:
        if factor.independent_group is None:
            ungrouped.append(factor)
            continue
        current = grouped.get(factor.independent_group)
        weight = factor.probability * factor.probability_confidence
        if current is None or weight > (
            current.probability * current.probability_confidence
        ):
            grouped[factor.independent_group] = factor
    return sorted(
        ungrouped + [grouped[k] for k in sorted(grouped)],
        key=lambda f: f.id,
    )


def sample_shared_factors(
    *,
    factors: list[SharedScenarioFactor],
    rng,
) -> dict[str, bool]:
    """Draw each distinct shared uncertainty exactly once.

    One ``rng`` call per *deduplicated* factor. Members collapsed into an
    ``independent_group`` alias onto their representative's draw, so the
    returned map can be read by factor id without the caller having to
    know which member survived deduplication — and without the shared
    uncertainty being drawn twice.

    Every project affected by a factor reads the *same* value, which is
    the whole point.
    """
    outcomes: dict[str, bool] = {}
    by_group: dict[str, bool] = {}
    for factor in deduplicate_shared_factors(factors):
        drawn = rng.random() < factor.probability
        outcomes[factor.id] = drawn
        if factor.independent_group is not None:
            by_group[factor.independent_group] = drawn
    for factor in factors:
        if factor.id in outcomes:
            continue
        if factor.independent_group in by_group:
            outcomes[factor.id] = by_group[factor.independent_group]
    return outcomes


def factors_affecting_project(
    *,
    factors: list[SharedScenarioFactor],
    project_id: str,
) -> list[SharedScenarioFactor]:
    return sorted(
        (f for f in factors if project_id in f.affected_project_ids),
        key=lambda f: f.id,
    )


def projects_sharing_factor(factor: SharedScenarioFactor) -> list[str]:
    return sorted(set(factor.affected_project_ids))


def shared_factor_coverage(
    factors: list[SharedScenarioFactor],
) -> dict[str, list[str]]:
    """Factor id -> the projects that move together with it."""
    return {
        f.id: projects_sharing_factor(f)
        for f in sorted(factors, key=lambda f: f.id)
        if f.is_shared
    }
