"""KPI claim reconciliation.

Source priority: direct metric source → authoritative analytics record
→ formal project target → conclusive decision → meeting statement →
chat statement. A meeting claim never automatically overrides a fresher
authoritative metric, conflicting values stay contested, and cause
claims remain hypotheses.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from lltpm.intelligence.kpi import (
    KPIClaim,
    KPIClaimType,
    KPIDefinition,
    KPIObservation,
    KPITarget,
    active_target,
    resolve_kpi_by_name,
)


class KPIReconciliationStatus(str, Enum):
    CONFIRMED = "confirmed"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    CONTESTED = "contested"
    UNRESOLVED = "unresolved"


class KPIReconciliationResult(BaseModel):
    kpi_id: str | None
    status: KPIReconciliationStatus
    resolved_definition_id: str | None
    resolved_observation_id: str | None
    resolved_target_id: str | None
    supporting_claim_ids: list[str] = Field(default_factory=list)
    conflicting_claim_ids: list[str] = Field(default_factory=list)
    explanation: str


_VALUE_TOLERANCE = 0.51  # claims are usually rounded in conversation
_OBSERVATION_AUTHORITY_WINDOW_SECONDS = 14 * 24 * 3600


def _latest_observation(
    observations: list[KPIObservation], kpi_id: str, as_of: datetime
) -> KPIObservation | None:
    relevant = sorted(
        (o for o in observations
         if o.kpi_id == kpi_id and o.observed_at <= as_of),
        key=lambda o: (o.observed_at, o.id),
    )
    return relevant[-1] if relevant else None


def reconcile_kpi_claims(
    *,
    claims: list[KPIClaim],
    known_kpis: list[KPIDefinition],
    observations: list[KPIObservation],
    targets: list[KPITarget],
    as_of: datetime,
) -> list[KPIReconciliationResult]:
    results: list[KPIReconciliationResult] = []

    grouped: dict[tuple[str | None, KPIClaimType], list[KPIClaim]] = {}
    order: list[tuple[str | None, KPIClaimType]] = []
    for claim in claims:
        definition = resolve_kpi_by_name(
            name=claim.candidate_kpi_name, known_kpis=known_kpis
        )
        key = (definition.id if definition else None, claim.claim_type)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(claim)

    for kpi_id, claim_type in order:
        group = grouped[(kpi_id, claim_type)]
        claim_ids = [c.id for c in group]

        if kpi_id is None:
            results.append(
                KPIReconciliationResult(
                    kpi_id=None,
                    status=KPIReconciliationStatus.UNRESOLVED,
                    resolved_definition_id=None,
                    resolved_observation_id=None,
                    resolved_target_id=None,
                    supporting_claim_ids=claim_ids,
                    explanation=(
                        f"'{group[0].candidate_kpi_name}' does not resolve "
                        f"to any known KPI or alias."
                    ),
                )
            )
            continue

        if claim_type == KPIClaimType.CURRENT_VALUE:
            observation = _latest_observation(observations, kpi_id, as_of)
            if observation is not None and (
                (as_of - observation.observed_at).total_seconds()
                <= _OBSERVATION_AUTHORITY_WINDOW_SECONDS
            ):
                supporting = [
                    c.id for c in group
                    if c.parsed_numeric_value is not None
                    and abs(c.parsed_numeric_value - observation.value)
                    <= _VALUE_TOLERANCE
                ]
                conflicting = [c for c in claim_ids if c not in supporting]
                results.append(
                    KPIReconciliationResult(
                        kpi_id=kpi_id,
                        status=KPIReconciliationStatus.CONFIRMED,
                        resolved_definition_id=kpi_id,
                        resolved_observation_id=observation.id,
                        resolved_target_id=None,
                        supporting_claim_ids=supporting,
                        conflicting_claim_ids=conflicting,
                        explanation=(
                            f"Direct metric source "
                            f"({observation.source_system}) is authoritative "
                            f"at {observation.value}{observation.unit}; "
                            f"meeting/chat statements do not override it."
                        ),
                    )
                )
                continue

            values = {
                round(c.parsed_numeric_value, 2)
                for c in group
                if c.parsed_numeric_value is not None
            }
            if len(values) == 1:
                results.append(
                    KPIReconciliationResult(
                        kpi_id=kpi_id,
                        status=KPIReconciliationStatus.CONFIRMED,
                        resolved_definition_id=kpi_id,
                        resolved_observation_id=None,
                        resolved_target_id=None,
                        supporting_claim_ids=claim_ids,
                        explanation=(
                            "All communicated values agree; no metric "
                            "source available to verify."
                        ),
                    )
                )
            elif len(values) > 1:
                results.append(
                    KPIReconciliationResult(
                        kpi_id=kpi_id,
                        status=KPIReconciliationStatus.CONTESTED,
                        resolved_definition_id=kpi_id,
                        resolved_observation_id=None,
                        resolved_target_id=None,
                        supporting_claim_ids=[],
                        conflicting_claim_ids=claim_ids,
                        explanation=(
                            f"Conflicting communicated values {sorted(values)} "
                            f"with no authoritative metric source — remains "
                            f"contested; no value is silently chosen."
                        ),
                    )
                )
            else:
                results.append(
                    KPIReconciliationResult(
                        kpi_id=kpi_id,
                        status=KPIReconciliationStatus.UNRESOLVED,
                        resolved_definition_id=kpi_id,
                        resolved_observation_id=None,
                        resolved_target_id=None,
                        supporting_claim_ids=claim_ids,
                        explanation="No numeric value could be parsed.",
                    )
                )
            continue

        if claim_type == KPIClaimType.TARGET:
            current = active_target(targets=targets, kpi_id=kpi_id, as_of=as_of)
            matching = [
                c.id for c in group
                if current is not None
                and c.target_candidate is not None
                and current.target_value is not None
                and math.isclose(
                    c.target_candidate, current.target_value, abs_tol=1e-6
                )
            ]
            if current is not None and matching:
                results.append(
                    KPIReconciliationResult(
                        kpi_id=kpi_id,
                        status=KPIReconciliationStatus.CONFIRMED,
                        resolved_definition_id=kpi_id,
                        resolved_observation_id=None,
                        resolved_target_id=current.id,
                        supporting_claim_ids=matching,
                        conflicting_claim_ids=[
                            c for c in claim_ids if c not in matching
                        ],
                        explanation="Matches the formal versioned target.",
                    )
                )
            else:
                results.append(
                    KPIReconciliationResult(
                        kpi_id=kpi_id,
                        status=KPIReconciliationStatus.PARTIALLY_CONFIRMED,
                        resolved_definition_id=kpi_id,
                        resolved_observation_id=None,
                        resolved_target_id=current.id if current else None,
                        supporting_claim_ids=claim_ids,
                        explanation=(
                            "Communicated target has no matching formal "
                            "target version — candidate for target review, "
                            "not an automatic overwrite."
                        ),
                    )
                )
            continue

        if claim_type == KPIClaimType.CAUSE:
            results.append(
                KPIReconciliationResult(
                    kpi_id=kpi_id,
                    status=KPIReconciliationStatus.UNRESOLVED,
                    resolved_definition_id=kpi_id,
                    resolved_observation_id=None,
                    resolved_target_id=None,
                    supporting_claim_ids=claim_ids,
                    explanation=(
                        "Cause statements from meetings remain hypotheses "
                        "until validated — never upgraded to fact because "
                        "they were said aloud."
                    ),
                )
            )
            continue

        results.append(
            KPIReconciliationResult(
                kpi_id=kpi_id,
                status=KPIReconciliationStatus.PARTIALLY_CONFIRMED,
                resolved_definition_id=kpi_id,
                resolved_observation_id=None,
                resolved_target_id=None,
                supporting_claim_ids=claim_ids,
                explanation=(
                    f"{claim_type.value} claim recorded against the "
                    f"canonical KPI."
                ),
            )
        )

    return results
