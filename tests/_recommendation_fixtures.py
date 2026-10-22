"""Shared fixtures for decision-recommendation tests."""

from __future__ import annotations

from datetime import datetime, timedelta

from tpllm.coordination import (
    ActorRelationType,
    DecisionCriterion,
    DecisionCriterionType,
    DecisionOptionAssessment,
    DecisionPosition,
    DecisionRecommendationPolicy,
    PositionType,
    WorkstreamKeyDate,
    WorkstreamType,
    build_decision_capsule,
    build_workstream_state,
)
from tests._coordination_fixtures import (
    AS_OF,
    PROJECT,
    WS,
    actor,
    decision,
    objective,
    request,
    understanding,
)

POLICY = DecisionRecommendationPolicy()


def position(pid, actor_ref, position_type, statement):
    return DecisionPosition(
        id=pid, decision_id="dec-1", actor=actor_ref,
        position_type=position_type, statement=statement,
        observed_at=AS_OF - timedelta(days=1), evidence_ids=[f"ev-{pid}"],
    )


def standard_positions():
    return [
        position("p-pay", actor("dana", role="Payments Integration Lead",
                                team="payments"),
                 PositionType.SUPPORT, "Ready for v3."),
        position("p-plat", actor("priya",
                                 relations=[ActorRelationType.MAY_APPROVE,
                                            ActorRelationType.OWNS_SYSTEM]),
                 PositionType.CONDITIONAL,
                 "v3 preferred if prepaid compatibility passes."),
        position("p-mob", actor("kim", role="Mobile Lead", team="mobile"),
                 PositionType.CONCERN,
                 "Mobile expects v2 during phase one."),
    ]


def workstream(**overrides):
    defaults = dict(
        workstream_id=WS, project_id=PROJECT, vertical="Payments",
        feature_or_scope="Account Graph Migration",
        workstream_type=WorkstreamType.MIGRATION,
        objective=objective(),
        key_dates=[
            WorkstreamKeyDate(
                id="kd-freeze", workstream_id=WS, name="integration freeze",
                date=AS_OF + timedelta(days=14), date_type="freeze",
                hard_commitment=True, evidence_ids=["ev-freeze"],
            )
        ],
        understanding=understanding(), readiness=None,
        decisions=[decision("dec-1", decided_days_ago=5)],
        action_requests=[request("req-1")], commitments=[],
        structural_signals=[], kpi_ids=["kpi-contract-validation"],
        wins=[], primary_contact=actor(), previous_state=None, as_of=AS_OF,
    )
    defaults.update(overrides)
    return build_workstream_state(**defaults)


def capsule(positions=None, **overrides):
    authority = actor(
        relations=[ActorRelationType.MAY_APPROVE,
                   ActorRelationType.OWNS_SYSTEM],
    )
    defaults = dict(
        request=request("req-1", preferred_recipient=authority),
        workstream=workstream(),
        decisions=[decision("dec-1", decided_days_ago=5)],
        positions=positions if positions is not None else standard_positions(),
        understanding=understanding(), readiness=None, evidence=[],
        as_of=AS_OF,
    )
    defaults.update(overrides)
    return build_decision_capsule(**defaults)


def criteria(n: int = 3) -> list[DecisionCriterion]:
    specs = [
        (DecisionCriterionType.OBJECTIVE_ALIGNMENT, 0.85, False),
        (DecisionCriterionType.DEPENDENCY_IMPACT, 0.75, False),
        (DecisionCriterionType.TECHNICAL_FEASIBILITY, 0.80, True),
    ]
    return [
        DecisionCriterion(
            id=f"crit-{i}", criterion_type=t, description=f"criterion {i}",
            weight=w, hard_requirement=hard, evidence_ids=[f"ev-crit-{i}"],
        )
        for i, (t, w, hard) in enumerate(specs[:n])
    ]


def assessment(
    option_id: str,
    scores: dict[str, float] | None = None,
    *,
    evidence_confidence: float = 0.95,
    violated: list[str] | None = None,
    n_criteria: int = 3,
    **overrides,
) -> DecisionOptionAssessment:
    if scores is None:
        scores = {f"crit-{i}": 0.9 for i in range(n_criteria)}
    defaults = dict(
        option_id=option_id,
        criterion_scores=scores,
        supporting_evidence_ids=[f"ev-for-{option_id}"],
        contradicting_evidence_ids=[f"ev-against-{option_id}"],
        satisfied_hard_constraints=[] if violated else ["technical-compat"],
        violated_hard_constraints=list(violated or []),
        assumptions=[f"assumption for {option_id}"],
        predicted_positive_impacts=[f"{option_id} unblocks integrations"],
        predicted_negative_impacts=[f"{option_id} adds coexistence risk"],
        reversible=True,
        evidence_confidence=evidence_confidence,
    )
    defaults.update(overrides)
    return DecisionOptionAssessment(**defaults)
