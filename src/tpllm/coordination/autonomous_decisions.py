"""Bounded autonomous organizational decisions.

When — and only when — an explicit policy authorizes it, the agent may
turn its own recommendation into a conclusive organizational decision.
Every such decision captures a pre-decision snapshot, records the agent
as a policy-delegated authority (never impersonating a human), and is
reopenable and supersedable exactly like a human decision.

The agent can never modify its own policy.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from tpllm.core.actors import ActorReference
from tpllm.coordination.authority_policy import (
    DecisionAuthorityAssessment,
    OrganizationalAutonomyBudget,
    OrganizationalDecisionAuthorityPolicy,
)
from tpllm.coordination.decision_capsules import DecisionCapsule
from tpllm.coordination.models import (
    OrganizationalActorRef,
    OrganizationalDecisionRecord,
    OrganizationalDecisionStatus,
)
from tpllm.coordination.recommendations import AgentDecisionRecommendation

DECISION_ORIGIN_AUTONOMOUS = "agent_autonomous_under_policy"

AGENT_AUTHORITY_ACTOR_ID = "tpllm-agent"


class AgentAuthorityRef(BaseModel):
    """The agent acting under delegated organizational policy. Rendered
    distinctly from any human authority."""
    agent_id: str = AGENT_AUTHORITY_ACTOR_ID
    display_name: str = "TPLLM"
    authority_policy_id: str
    authority_scope: str

    def as_actor_reference(self) -> OrganizationalActorRef:
        """A clearly non-human actor reference for the decision record."""
        return OrganizationalActorRef(
            actor_id=self.agent_id,
            display_name=self.display_name,
            role=(
                f"Agent under delegated policy "
                f"{self.authority_policy_id} ({self.authority_scope})"
            ),
            team_id=None,
            team_name=None,
        )


class OrganizationalDecisionSnapshot(BaseModel):
    """The before-state, captured prior to autonomous finalization."""
    id: str
    recommendation: AgentDecisionRecommendation
    capsule: DecisionCapsule
    authority_assessment: DecisionAuthorityAssessment
    relevant_evidence_ids: list[str] = Field(default_factory=list)
    captured_at: datetime


class AutonomousDecisionExecution(BaseModel):
    id: str
    recommendation_id: str
    selected_option_id: str
    authority_policy_id: str
    snapshot_id: str
    created_decision_id: str | None = None
    executed_at: datetime
    review_due_at: datetime | None = None
    explanation: str


class AutonomousDecisionReview(BaseModel):
    decision_id: str
    review_due_at: datetime
    reviewed_by: ActorReference | None = None
    review_result: str | None = None
    review_notes: str | None = None


def capture_decision_snapshot(
    *,
    recommendation: AgentDecisionRecommendation,
    capsule: DecisionCapsule,
    authority_assessment: DecisionAuthorityAssessment,
    now: datetime,
) -> OrganizationalDecisionSnapshot:
    evidence_ids = list(recommendation.evidence_ids)
    for eid in capsule.evidence_ids:
        if eid not in evidence_ids:
            evidence_ids.append(eid)
    return OrganizationalDecisionSnapshot(
        id=f"snapshot-{recommendation.id}",
        recommendation=recommendation,
        capsule=capsule,
        authority_assessment=authority_assessment,
        relevant_evidence_ids=evidence_ids,
        captured_at=now,
    )


def finalize_agent_decision(
    *,
    recommendation: AgentDecisionRecommendation,
    capsule: DecisionCapsule,
    authority_assessment: DecisionAuthorityAssessment,
    policy: OrganizationalDecisionAuthorityPolicy,
    now: datetime,
) -> tuple[
    OrganizationalDecisionRecord,
    OrganizationalDecisionSnapshot,
    AutonomousDecisionExecution,
]:
    """Create a conclusive decision under delegated authority.

    Raises unless the authority assessment says the decision is
    autonomously eligible — eligibility is decided in
    ``authority_policy``, never re-derived permissively here.
    """
    if not authority_assessment.autonomous_eligible:
        raise ValueError(
            "Autonomous finalization requires an eligible authority "
            "assessment; blockers: "
            + "; ".join(authority_assessment.blockers)
        )
    if recommendation.recommended_option_id is None:
        raise ValueError("Nothing to finalize: no recommended option.")

    snapshot = capture_decision_snapshot(
        recommendation=recommendation,
        capsule=capsule,
        authority_assessment=authority_assessment,
        now=now,
    )

    agent_authority = AgentAuthorityRef(
        authority_policy_id=policy.id,
        authority_scope=(
            ", ".join(policy.autonomous_allowed_decision_types) or "unscoped"
        ),
    )

    review_due_at = (
        now + timedelta(hours=policy.auto_expiry_hours)
        if policy.human_review_required_after_execution
        else None
    )

    record = OrganizationalDecisionRecord(
        id=f"decision-auto-{recommendation.id}",
        project_id=recommendation.project_id,
        workstream_id=recommendation.workstream_id,
        topic=capsule.decision_question[:120],
        question=capsule.decision_question,
        status=OrganizationalDecisionStatus.CONCLUSIVE,
        conclusion=recommendation.recommended_option_id,
        decision_authority=agent_authority.as_actor_reference(),
        positions=list(capsule.actor_positions),
        decided_at=now,
        effective_at=now,
        consequence_summary=(
            f"Origin: {DECISION_ORIGIN_AUTONOMOUS}. Decided by the agent "
            f"under policy {policy.id} at "
            f"{authority_assessment.decision_risk.value} risk with "
            f"confidence {recommendation.confidence:.2f}. "
            f"{authority_assessment.authority_reason}"
        ),
        affected_workstream_ids=(
            [recommendation.workstream_id]
            if recommendation.workstream_id
            else []
        ),
        affected_kpi_ids=list(capsule.affected_kpi_ids),
        evidence_ids=snapshot.relevant_evidence_ids,
        confidence=recommendation.confidence,
    )

    execution = AutonomousDecisionExecution(
        id=f"exec-{recommendation.id}",
        recommendation_id=recommendation.id,
        selected_option_id=recommendation.recommended_option_id,
        authority_policy_id=policy.id,
        snapshot_id=snapshot.id,
        created_decision_id=record.id,
        executed_at=now,
        review_due_at=review_due_at,
        explanation=authority_assessment.authority_reason,
    )
    return record, snapshot, execution


def consume_autonomy_budget(
    *,
    budget: OrganizationalAutonomyBudget,
    on_day: datetime,
) -> OrganizationalAutonomyBudget:
    """Copy-on-write budget consumption, resetting on a new day."""
    day = on_day.date()
    if day != budget.current_day:
        return budget.model_copy(
            update={"current_day": day, "decisions_used": 1}
        )
    return budget.model_copy(
        update={"decisions_used": budget.decisions_used + 1}
    )


def is_agent_decided(record: OrganizationalDecisionRecord) -> bool:
    """Agent decisions are always distinguishable from human ones."""
    return (
        record.decision_authority is not None
        and record.decision_authority.actor_id == AGENT_AUTHORITY_ACTOR_ID
    )


def schedule_autonomous_review(
    *,
    execution: AutonomousDecisionExecution,
) -> AutonomousDecisionReview | None:
    if execution.review_due_at is None or execution.created_decision_id is None:
        return None
    return AutonomousDecisionReview(
        decision_id=execution.created_decision_id,
        review_due_at=execution.review_due_at,
    )
