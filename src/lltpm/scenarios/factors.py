"""Scenario probability factors derived from real state (Commit 24).

Factors come from the reconstructed temporal state — decision age,
dependency state, KPI signals, capacity constraints, commitment
pressure, technical uncertainty, communication risk — never from a fixed
table applied blindly.

Factors carry an ``independent_group`` whenever they derive from the same
underlying situation, so the probability engine cannot double-count them.
"""

from __future__ import annotations

from datetime import datetime

from lltpm.scenarios.models import (
    ScenarioFactorType,
    ScenarioProbabilityFactor,
)
from lltpm.temporal.models import ProjectTemporalState


def build_factor(
    *,
    factor_id: str,
    factor_type: ScenarioFactorType,
    description: str,
    direction: str,
    magnitude: float,
    confidence: float,
    source_ids: list[str] | None = None,
    independent_group: str | None = None,
) -> ScenarioProbabilityFactor:
    return ScenarioProbabilityFactor(
        id=factor_id,
        factor_type=factor_type,
        description=description,
        direction=direction,
        magnitude=max(0.0, min(1.0, magnitude)),
        confidence=max(0.0, min(1.0, confidence)),
        source_ids=list(source_ids or []),
        independent_group=independent_group,
    )


def derive_factors_from_state(
    *,
    state: ProjectTemporalState,
    as_of: datetime,
) -> list[ScenarioProbabilityFactor]:
    """Turn the reconstructed state into delay/failure-pressure factors.

    Every factor's evidence stays traceable to the state that produced it.
    """
    factors: list[ScenarioProbabilityFactor] = []

    for blocker_id in state.active_blocker_ids:
        factors.append(
            build_factor(
                factor_id=f"fac-blocker-{blocker_id}",
                factor_type=ScenarioFactorType.DEPENDENCY_STATE,
                description=f"Blocker {blocker_id} is open.",
                direction="increases",
                magnitude=0.55,
                confidence=0.75,
                source_ids=[blocker_id],
                # All blockers on one dependency share a situation.
                independent_group=f"blocker:{blocker_id}",
            )
        )

    for claim_id in state.contested_claim_ids:
        factors.append(
            build_factor(
                factor_id=f"fac-contested-{claim_id}",
                factor_type=ScenarioFactorType.COMMUNICATION_RISK,
                description=(
                    f"Claim {claim_id} is contested; teams may be acting on "
                    f"different assumptions."
                ),
                direction="increases",
                magnitude=0.40,
                confidence=0.60,
                source_ids=[claim_id],
                independent_group=f"contested:{claim_id}",
            )
        )

    for unknown_id in state.unresolved_unknown_ids:
        factors.append(
            build_factor(
                factor_id=f"fac-unknown-{unknown_id}",
                factor_type=ScenarioFactorType.TECHNICAL_UNCERTAINTY,
                description=f"Unresolved unknown {unknown_id}.",
                direction="increases",
                magnitude=0.30,
                confidence=0.45,
                source_ids=[unknown_id],
                independent_group=f"unknown:{unknown_id}",
            )
        )

    for request_id in state.open_action_request_ids:
        factors.append(
            build_factor(
                factor_id=f"fac-openask-{request_id}",
                factor_type=ScenarioFactorType.DECISION_AGE,
                description=f"Action request {request_id} is still open.",
                direction="increases",
                magnitude=0.35,
                confidence=0.65,
                source_ids=[request_id],
                independent_group=f"request:{request_id}",
            )
        )

    if state.active_decision_ids:
        factors.append(
            build_factor(
                factor_id="fac-authoritative-decisions",
                factor_type=ScenarioFactorType.CURRENT_STRUCTURAL_SIGNAL,
                description=(
                    f"{len(state.active_decision_ids)} authoritative "
                    f"decision(s) provide a stable basis."
                ),
                direction="decreases",
                magnitude=0.25,
                confidence=0.70,
                source_ids=list(state.active_decision_ids),
            )
        )

    return sorted(factors, key=lambda f: f.id)


def build_correlated_evidence_factors(
    *,
    situation_id: str,
    factor_type: ScenarioFactorType,
    descriptions: list[str],
    magnitude: float = 0.5,
    confidence: float = 0.6,
) -> list[ScenarioProbabilityFactor]:
    """Several reports of the *same* situation.

    They share an independent_group, so the engine counts the situation
    once no matter how many times it was mentioned.
    """
    return [
        build_factor(
            factor_id=f"fac-{situation_id}-{index}",
            factor_type=factor_type,
            description=description,
            direction="increases",
            magnitude=magnitude,
            confidence=confidence,
            source_ids=[f"{situation_id}-src-{index}"],
            independent_group=situation_id,
        )
        for index, description in enumerate(descriptions)
    ]
