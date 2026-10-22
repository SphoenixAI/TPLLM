"""KPI stewardship.

Models the state of the KPI *response* — acknowledged, investigating,
remediating, waiting on a dependency, validating recovery — with
distinct measurement, remediation, and decision owners. It reports
structural conditions ("remediation is blocked on X's decision"), never
judgments about people or teams failing.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from lltpm.core.actors import ActorReference
from lltpm.intelligence.kpi import (
    KPIDefinition,
    KPIHealthStatus,
    KPIMilestoneLink,
    KPIResponseState,
    KPITrend,
)


class KPIResponsePlan(BaseModel):
    id: str
    kpi_id: str
    state: KPIResponseState
    measurement_owner: ActorReference | None
    remediation_owner: ActorReference | None
    decision_owner: ActorReference | None
    investigation_summary: str | None
    remediation_summary: str | None
    blocker_ids: list[str] = Field(default_factory=list)
    decision_request_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    last_updated_at: datetime


class KPIStewardshipAssessment(BaseModel):
    kpi_id: str
    health: KPIHealthStatus
    response_state: KPIResponseState
    acknowledged: bool
    active_remediation: bool
    blocked_by_dependency: bool
    blocker_ids: list[str] = Field(default_factory=list)
    next_required_action: str | None
    next_decision_owner: ActorReference | None
    evidence_freshness: float
    explanation: str


class KPIResolutionPacket(BaseModel):
    """Complete context + next action + correct authority — replacing
    'start a Slack chat to close the KPI flag'."""
    kpi_id: str
    current_health: KPIHealthStatus
    current_value: float | None
    target_summary: str | None
    why_it_matters: str
    related_workstream_ids: list[str] = Field(default_factory=list)
    related_milestone_ids: list[str] = Field(default_factory=list)
    probable_contributing_signals: list[str] = Field(default_factory=list)
    response_state: KPIResponseState
    next_required_action: str | None
    decision_capsule_id: str | None
    evidence_ids: list[str] = Field(default_factory=list)


_ACKNOWLEDGED_STATES = {
    KPIResponseState.ACKNOWLEDGED,
    KPIResponseState.INVESTIGATING,
    KPIResponseState.REMEDIATING,
    KPIResponseState.WAITING_ON_DEPENDENCY,
    KPIResponseState.VALIDATING_RECOVERY,
    KPIResponseState.RESOLVED,
}

_ACTIVE_STATES = {
    KPIResponseState.INVESTIGATING,
    KPIResponseState.REMEDIATING,
    KPIResponseState.VALIDATING_RECOVERY,
}


def _freshness_factor(trend: KPITrend) -> float:
    if trend.freshness_seconds is None:
        return 0.0
    days = trend.freshness_seconds / 86400
    return max(0.0, min(1.0, 1.0 - days / 30.0))


def assess_kpi_stewardship(
    *,
    definition: KPIDefinition,
    trend: KPITrend,
    plan: KPIResponsePlan | None,
    as_of: datetime,
) -> KPIStewardshipAssessment:
    """Structural stewardship narrative: what state the response is in,
    what blocks it, and what the next required action is — with the
    owner of that action, never a verdict about a team."""
    if plan is None:
        state = KPIResponseState.UNACKNOWLEDGED
        acknowledged = False
        active = False
        blocked = False
        blocker_ids: list[str] = []
        next_action = (
            f"Acknowledge the {trend.health.value} state of "
            f"'{definition.canonical_name}' and assign a remediation owner."
            if trend.health
            in (KPIHealthStatus.BREACHED, KPIHealthStatus.CRITICAL)
            else None
        )
        next_owner = None
    else:
        state = plan.state
        acknowledged = plan.state in _ACKNOWLEDGED_STATES
        active = plan.state in _ACTIVE_STATES
        blocked = plan.state == KPIResponseState.WAITING_ON_DEPENDENCY
        blocker_ids = list(plan.blocker_ids)
        if blocked:
            next_action = (
                plan.remediation_summary
                or "Resolve the blocking dependency decision."
            )
            next_owner = plan.decision_owner
        elif plan.state == KPIResponseState.RESOLVED:
            next_action = None
            next_owner = None
        else:
            next_action = plan.remediation_summary or plan.investigation_summary
            next_owner = plan.remediation_owner or plan.decision_owner

    parts: list[str] = []
    parts.append(
        f"'{definition.canonical_name}' is {trend.health.value}"
        + (
            f" at {trend.latest_value}{definition.unit}"
            if trend.latest_value is not None
            else ""
        )
        + "."
    )
    if acknowledged:
        parts.append("The state has been acknowledged.")
        if plan is not None and plan.remediation_owner is not None:
            owner = plan.remediation_owner
            parts.append(
                f"{owner.team_name or owner.display_name or owner.actor_id} "
                f"is {'remediating it' if active else 'responsible for remediation'}."
            )
        if blocked:
            parts.append(
                "Progress is blocked on a dependency"
                + (
                    f" ({', '.join(blocker_ids)})"
                    if blocker_ids
                    else ""
                )
                + "."
            )
    else:
        parts.append("The state has not yet been acknowledged.")
    if next_action:
        owner_note = ""
        if plan is not None and blocked and plan.decision_owner is not None:
            decision_owner = plan.decision_owner
            owner_note = (
                f", owned by "
                f"{decision_owner.team_name or decision_owner.display_name or decision_owner.actor_id}"
            )
        parts.append(f"The next required action is: {next_action}{owner_note}.")

    return KPIStewardshipAssessment(
        kpi_id=definition.id,
        health=trend.health,
        response_state=state,
        acknowledged=acknowledged,
        active_remediation=active,
        blocked_by_dependency=blocked,
        blocker_ids=blocker_ids,
        next_required_action=next_action,
        next_decision_owner=(
            plan.decision_owner if plan is not None and blocked else next_owner
            if plan is not None
            else None
        ),
        evidence_freshness=_freshness_factor(trend),
        explanation=" ".join(parts),
    )


def build_kpi_resolution_packet(
    *,
    definition: KPIDefinition,
    trend: KPITrend,
    assessment: KPIStewardshipAssessment,
    target_summary: str | None,
    related_workstream_ids: list[str],
    milestone_links: list[KPIMilestoneLink],
    probable_contributing_signals: list[str],
    decision_capsule_id: str | None = None,
) -> KPIResolutionPacket:
    evidence_ids: list[str] = list(definition.evidence_ids)
    for eid in trend.evidence_ids:
        if eid not in evidence_ids:
            evidence_ids.append(eid)

    return KPIResolutionPacket(
        kpi_id=definition.id,
        current_health=trend.health,
        current_value=trend.latest_value,
        target_summary=target_summary,
        why_it_matters=definition.why_it_matters,
        related_workstream_ids=list(related_workstream_ids),
        related_milestone_ids=[
            link.milestone_id
            for link in milestone_links
            if link.kpi_id == definition.id
        ],
        probable_contributing_signals=list(probable_contributing_signals),
        response_state=assessment.response_state,
        next_required_action=assessment.next_required_action,
        decision_capsule_id=decision_capsule_id,
        evidence_ids=evidence_ids,
    )
