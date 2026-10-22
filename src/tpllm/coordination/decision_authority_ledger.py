"""Append-only organizational decision ledger.

Every step from recommendation to outcome is recorded so the four
symmetric evaluation cases can be compared honestly later:

    agent recommended A / human validated A / outcome positive
    agent recommended A / human validated A / outcome negative
    agent recommended A / human chose B    / outcome positive
    agent recommended A / human chose B    / outcome negative

No one-sided metric proving the agent is always right.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from tpllm.coordination.autonomous_decisions import (
    AutonomousDecisionExecution,
)
from tpllm.coordination.recommendations import (
    AgentDecisionRecommendation,
    DecisionEvaluationAction,
    HumanDecisionEvaluation,
)


class DecisionLedgerEventType(str, Enum):
    RECOMMENDED = "recommended"
    HUMAN_VALIDATED = "human_validated"
    HUMAN_MODIFIED = "human_modified"
    HUMAN_REJECTED = "human_rejected"
    HUMAN_DEFERRED = "human_deferred"
    AUTONOMOUSLY_FINALIZED = "autonomously_finalized"
    REOPENED = "reopened"
    SUPERSEDED = "superseded"
    OUTCOME_OBSERVED = "outcome_observed"


class DecisionOrigin(str, Enum):
    AGENT_RECOMMENDATION = "agent_recommendation"
    HUMAN_VALIDATED_AGENT = "human_validated_agent_recommendation"
    HUMAN_MODIFIED_AGENT = "human_modified_agent_recommendation"
    HUMAN_ONLY = "human_decision"
    AGENT_AUTONOMOUS = "agent_autonomous_under_policy"


class OrganizationalDecisionLedgerEntry(BaseModel):
    id: str
    decision_id: str
    recommendation_id: str | None = None
    decision_origin: str
    selected_option_id: str | None = None
    authority_policy_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    occurred_at: datetime
    event_type: str


class OrganizationalDecisionLedger(BaseModel):
    entries: list[OrganizationalDecisionLedgerEntry] = Field(
        default_factory=list
    )


class OrganizationalDecisionOutcome(BaseModel):
    decision_id: str
    observed_outcome: str
    positive: bool | None = None
    affected_kpi_ids: list[str] = Field(default_factory=list)
    affected_milestone_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    observed_at: datetime


def _append(
    ledger: OrganizationalDecisionLedger,
    entry: OrganizationalDecisionLedgerEntry,
) -> OrganizationalDecisionLedger:
    """Append-only by copy; idempotent on entry id."""
    if any(e.id == entry.id for e in ledger.entries):
        return ledger
    return ledger.model_copy(update={"entries": [*ledger.entries, entry]})


def append_recommendation(
    *,
    ledger: OrganizationalDecisionLedger,
    recommendation: AgentDecisionRecommendation,
) -> OrganizationalDecisionLedger:
    return _append(
        ledger,
        OrganizationalDecisionLedgerEntry(
            id=f"led-rec-{recommendation.id}",
            decision_id=recommendation.decision_capsule_id,
            recommendation_id=recommendation.id,
            decision_origin=DecisionOrigin.AGENT_RECOMMENDATION.value,
            selected_option_id=recommendation.recommended_option_id,
            evidence_ids=list(recommendation.evidence_ids),
            occurred_at=recommendation.generated_at,
            event_type=DecisionLedgerEventType.RECOMMENDED.value,
        ),
    )


_ACTION_EVENTS = {
    DecisionEvaluationAction.VALIDATE: (
        DecisionLedgerEventType.HUMAN_VALIDATED,
        DecisionOrigin.HUMAN_VALIDATED_AGENT,
    ),
    DecisionEvaluationAction.MODIFY: (
        DecisionLedgerEventType.HUMAN_MODIFIED,
        DecisionOrigin.HUMAN_MODIFIED_AGENT,
    ),
    DecisionEvaluationAction.REJECT: (
        DecisionLedgerEventType.HUMAN_REJECTED,
        DecisionOrigin.HUMAN_ONLY,
    ),
    DecisionEvaluationAction.DEFER: (
        DecisionLedgerEventType.HUMAN_DEFERRED,
        DecisionOrigin.HUMAN_ONLY,
    ),
}


def append_human_evaluation(
    *,
    ledger: OrganizationalDecisionLedger,
    evaluation: HumanDecisionEvaluation,
    decision_id: str,
) -> OrganizationalDecisionLedger:
    event_type, origin = _ACTION_EVENTS[evaluation.action]
    return _append(
        ledger,
        OrganizationalDecisionLedgerEntry(
            id=f"led-eval-{evaluation.id}",
            decision_id=decision_id,
            recommendation_id=evaluation.recommendation_id,
            decision_origin=origin.value,
            selected_option_id=evaluation.selected_option_id,
            evidence_ids=list(evaluation.evidence_ids),
            occurred_at=evaluation.evaluated_at,
            event_type=event_type.value,
        ),
    )


def append_autonomous_execution(
    *,
    ledger: OrganizationalDecisionLedger,
    execution: AutonomousDecisionExecution,
    evidence_ids: list[str] | None = None,
) -> OrganizationalDecisionLedger:
    return _append(
        ledger,
        OrganizationalDecisionLedgerEntry(
            id=f"led-exec-{execution.id}",
            decision_id=execution.created_decision_id or execution.id,
            recommendation_id=execution.recommendation_id,
            decision_origin=DecisionOrigin.AGENT_AUTONOMOUS.value,
            selected_option_id=execution.selected_option_id,
            authority_policy_id=execution.authority_policy_id,
            evidence_ids=list(evidence_ids or []),
            occurred_at=execution.executed_at,
            event_type=(
                DecisionLedgerEventType.AUTONOMOUSLY_FINALIZED.value
            ),
        ),
    )


def append_lifecycle_event(
    *,
    ledger: OrganizationalDecisionLedger,
    decision_id: str,
    event_type: DecisionLedgerEventType,
    occurred_at: datetime,
    evidence_ids: list[str] | None = None,
    origin: str = DecisionOrigin.HUMAN_ONLY.value,
) -> OrganizationalDecisionLedger:
    return _append(
        ledger,
        OrganizationalDecisionLedgerEntry(
            id=f"led-{event_type.value}-{decision_id}-"
               f"{occurred_at.isoformat()}",
            decision_id=decision_id,
            decision_origin=origin,
            evidence_ids=list(evidence_ids or []),
            occurred_at=occurred_at,
            event_type=event_type.value,
        ),
    )


def append_outcome(
    *,
    ledger: OrganizationalDecisionLedger,
    outcome: OrganizationalDecisionOutcome,
) -> OrganizationalDecisionLedger:
    return _append(
        ledger,
        OrganizationalDecisionLedgerEntry(
            id=f"led-outcome-{outcome.decision_id}-"
               f"{outcome.observed_at.isoformat()}",
            decision_id=outcome.decision_id,
            decision_origin=DecisionOrigin.HUMAN_ONLY.value,
            evidence_ids=list(outcome.evidence_ids),
            occurred_at=outcome.observed_at,
            event_type=DecisionLedgerEventType.OUTCOME_OBSERVED.value,
        ),
    )


class RecommendationEvaluationCase(BaseModel):
    """One row of the symmetric evaluation matrix."""
    decision_id: str
    recommendation_id: str | None
    agent_recommended_option_id: str | None
    final_option_id: str | None
    human_followed_agent: bool | None
    decision_origin: str
    outcome_positive: bool | None


def build_evaluation_matrix(
    *,
    ledger: OrganizationalDecisionLedger,
    outcomes: list[OrganizationalDecisionOutcome],
) -> list[RecommendationEvaluationCase]:
    """Assemble agent-recommended vs finally-decided vs outcome, without
    asserting that either party is an oracle."""
    outcomes_by_decision = {o.decision_id: o for o in outcomes}
    recommended: dict[str, tuple[str | None, str | None]] = {}
    finals: dict[str, tuple[str | None, str]] = {}

    for entry in ledger.entries:
        if entry.event_type == DecisionLedgerEventType.RECOMMENDED.value:
            recommended[entry.recommendation_id or entry.decision_id] = (
                entry.selected_option_id, entry.decision_id,
            )
        elif entry.event_type in (
            DecisionLedgerEventType.HUMAN_VALIDATED.value,
            DecisionLedgerEventType.HUMAN_MODIFIED.value,
            DecisionLedgerEventType.AUTONOMOUSLY_FINALIZED.value,
        ):
            finals[entry.decision_id] = (
                entry.selected_option_id, entry.decision_origin,
            )

    cases: list[RecommendationEvaluationCase] = []
    for entry in ledger.entries:
        if entry.event_type not in (
            DecisionLedgerEventType.HUMAN_VALIDATED.value,
            DecisionLedgerEventType.HUMAN_MODIFIED.value,
            DecisionLedgerEventType.AUTONOMOUSLY_FINALIZED.value,
        ):
            continue
        rec_key = entry.recommendation_id
        agent_option = (
            recommended.get(rec_key, (None, None))[0] if rec_key else None
        )
        final_option, origin = finals[entry.decision_id]
        outcome = outcomes_by_decision.get(entry.decision_id)
        cases.append(
            RecommendationEvaluationCase(
                decision_id=entry.decision_id,
                recommendation_id=rec_key,
                agent_recommended_option_id=agent_option,
                final_option_id=final_option,
                human_followed_agent=(
                    None if agent_option is None
                    else agent_option == final_option
                ),
                decision_origin=origin,
                outcome_positive=(
                    outcome.positive if outcome is not None else None
                ),
            )
        )
    return cases
