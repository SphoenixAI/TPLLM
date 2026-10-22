"""Human evaluation of agent recommendations, and the decision surface
.

Humans validate, modify, reject, or defer. A validation feeds the
normal ``OrganizationalDecisionRecord`` — no competing
decision type is created. A rejection is recorded, never treated as
proof the agent was wrong (deliberately), and it never mutates
agent authority or policy. A deferral names the missing evidence and
can generate a self-research gap.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from tpllm.core.actors import ActorReference
from tpllm.coordination.ask_router import ActionRoutingPlan
from tpllm.coordination.decision_capsules import DecisionCapsule
from tpllm.coordination.models import (
    DecisionPosition,
    OrganizationalActorRef,
    OrganizationalDecisionRecord,
    OrganizationalDecisionStatus,
)
from tpllm.coordination.recommendations import (
    AgentDecisionRecommendation,
    AgentRecommendationStatus,
    DecisionEvaluationAction,
    HumanDecisionEvaluation,
)

# How a decision came to be authoritative. Kept distinct so analytics
# can compare agent recommendation against final decision honestly.
DECISION_ORIGIN_HUMAN_VALIDATED_AGENT = "human_validated_agent_recommendation"
DECISION_ORIGIN_HUMAN_MODIFIED_AGENT = "human_modified_agent_recommendation"
DECISION_ORIGIN_HUMAN_ONLY = "human_decision"


def _as_org_actor(actor: ActorReference) -> OrganizationalActorRef:
    if isinstance(actor, OrganizationalActorRef):
        return actor
    return OrganizationalActorRef(**actor.model_dump())


def finalize_human_validated_decision(
    *,
    recommendation: AgentDecisionRecommendation,
    evaluation: HumanDecisionEvaluation,
    capsule: DecisionCapsule,
    now: datetime | None = None,
) -> OrganizationalDecisionRecord:
    """Turn a human VALIDATE or MODIFY into a conclusive organizational
    decision record. This is the default path."""
    if evaluation.action not in (
        DecisionEvaluationAction.VALIDATE,
        DecisionEvaluationAction.MODIFY,
    ):
        raise ValueError(
            f"Only validate/modify finalize a decision; got "
            f"{evaluation.action.value}."
        )
    if evaluation.recommendation_id != recommendation.id:
        raise ValueError("Evaluation does not belong to this recommendation.")

    stamp = now or evaluation.evaluated_at
    modified = (
        evaluation.action == DecisionEvaluationAction.MODIFY
        or evaluation.selected_option_id != recommendation.recommended_option_id
    )
    origin = (
        DECISION_ORIGIN_HUMAN_MODIFIED_AGENT
        if modified
        else DECISION_ORIGIN_HUMAN_VALIDATED_AGENT
    )

    conclusion = evaluation.selected_option_id or ""
    if evaluation.modification_summary:
        conclusion = (
            f"{evaluation.selected_option_id}: "
            f"{evaluation.modification_summary}"
        )

    evidence_ids = list(recommendation.evidence_ids)
    for eid in evaluation.evidence_ids:
        if eid not in evidence_ids:
            evidence_ids.append(eid)
    if not evidence_ids:
        evidence_ids = [recommendation.id]

    consequence = (
        f"Origin: {origin}. Agent recommended "
        f"{recommendation.recommended_option_id or 'no option'} "
        f"(confidence {recommendation.confidence:.2f}); human selected "
        f"{evaluation.selected_option_id}."
    )

    return OrganizationalDecisionRecord(
        id=f"decision-{recommendation.id}",
        project_id=recommendation.project_id,
        workstream_id=recommendation.workstream_id,
        topic=capsule.decision_question[:120],
        question=capsule.decision_question,
        status=OrganizationalDecisionStatus.CONCLUSIVE,
        conclusion=conclusion,
        decision_authority=_as_org_actor(evaluation.evaluator),
        positions=list(capsule.actor_positions),
        decided_at=stamp,
        effective_at=stamp,
        consequence_summary=consequence,
        affected_workstream_ids=(
            [recommendation.workstream_id]
            if recommendation.workstream_id
            else []
        ),
        affected_kpi_ids=list(capsule.affected_kpi_ids),
        evidence_ids=evidence_ids,
        confidence=recommendation.confidence,
    )


class DeferredDecisionResearchNeed(BaseModel):
    """A deferral names what evidence would unblock the decision, so
    self-research can pursue it."""
    recommendation_id: str
    project_id: str
    question: str
    why_it_matters: str
    missing_evidence: list[str] = Field(default_factory=list)
    requested_by: ActorReference | None = None
    created_at: datetime


def build_deferral_research_need(
    *,
    recommendation: AgentDecisionRecommendation,
    evaluation: HumanDecisionEvaluation,
) -> DeferredDecisionResearchNeed:
    if evaluation.action != DecisionEvaluationAction.DEFER:
        raise ValueError("Only a deferral produces a research need.")
    missing = list(recommendation.rationale.unresolved_unknowns)
    missing.extend(recommendation.rationale.assumptions)
    if evaluation.defer_reason:
        missing.insert(0, evaluation.defer_reason)
    return DeferredDecisionResearchNeed(
        recommendation_id=recommendation.id,
        project_id=recommendation.project_id,
        question=recommendation.decision_question,
        why_it_matters=(
            "The decision is deferred pending evidence; until it resolves "
            "the dependent work stays blocked."
        ),
        missing_evidence=missing,
        requested_by=evaluation.evaluator,
        created_at=evaluation.evaluated_at,
    )


# ── Decision surface ─────────────────────────────────────────────────

class DecisionIntelligencePanel(BaseModel):
    """The decision surface: facts, positions, contacts and authority,
    the agent's recommendation and rationale, evidence for and against,
    then the human or policy decision."""
    capsule: DecisionCapsule
    recommendation: AgentDecisionRecommendation | None = None
    decision_authority: ActorReference | None = None
    relevant_contacts: list[ActorReference] = Field(default_factory=list)
    expertise_contacts: list[ActorReference] = Field(default_factory=list)
    current_positions: list[DecisionPosition] = Field(default_factory=list)
    latest_conclusive_decision: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


def build_decision_intelligence_panel(
    *,
    capsule: DecisionCapsule,
    recommendation: AgentDecisionRecommendation | None,
    routing_plan: ActionRoutingPlan | None = None,
    resolved_actors: object | None = None,
) -> DecisionIntelligencePanel:
    """Assemble the surface. Purely compositional — no factual field of
    the capsule or recommendation is altered.

    A recommendation is never hidden merely because human validation is
    required; it is shown beside the evidence.
    """
    authority: ActorReference | None = capsule.decision_authority
    contacts: list[ActorReference] = []
    expertise: list[ActorReference] = []

    if routing_plan is not None:
        if routing_plan.primary_recipient is not None:
            authority = routing_plan.primary_recipient
        for candidate in (
            routing_plan.primary_recipient,
            routing_plan.backup_recipient,
        ):
            if candidate is not None and candidate not in contacts:
                contacts.append(candidate)

    if capsule.requested_by not in contacts:
        contacts.append(capsule.requested_by)
    for position in capsule.actor_positions:
        if position.actor not in contacts:
            contacts.append(position.actor)

    for contact in contacts:
        relations = {
            getattr(r, "value", r)
            for r in getattr(contact, "relation_types", [])
        }
        if "has_expertise" in relations and contact not in expertise:
            expertise.append(contact)

    # Canonical resolution may annotate authority, but never replaces
    # the recorded organizational facts.
    if resolved_actors is not None:
        resolved_authority = getattr(resolved_actors, "decision_authority", None)
        canonical_id = getattr(resolved_authority, "canonical_entity_id", None)
        if authority is not None and canonical_id:
            authority = authority.model_copy(
                update={"canonical_entity_id": canonical_id}
            )

    evidence_refs = list(capsule.evidence_ids)
    if recommendation is not None:
        for eid in recommendation.evidence_ids:
            if eid not in evidence_refs:
                evidence_refs.append(eid)

    return DecisionIntelligencePanel(
        capsule=capsule,
        recommendation=recommendation,
        decision_authority=authority,
        relevant_contacts=contacts,
        expertise_contacts=expertise,
        current_positions=list(capsule.actor_positions),
        latest_conclusive_decision=capsule.latest_conclusive_decision,
        evidence_refs=evidence_refs,
    )


def render_panel_sections(panel: DecisionIntelligencePanel) -> list[dict]:
    """Vendor-neutral sections for a tool surface."""
    sections: list[dict] = [
        {"title": "Decision", "body": panel.capsule.decision_question},
        {"title": "Current state", "body": panel.capsule.current_state},
    ]
    if panel.latest_conclusive_decision:
        sections.append({
            "title": "Latest conclusive decision",
            "body": panel.latest_conclusive_decision,
        })
    if panel.capsule.what_changed_since_last_discussion:
        sections.append({
            "title": "What changed",
            "items": panel.capsule.what_changed_since_last_discussion,
        })

    recommendation = panel.recommendation
    if recommendation is None:
        sections.append({
            "title": "Agent recommendation",
            "body": "Not evaluated.",
        })
    elif recommendation.status == (
        AgentRecommendationStatus.RECOMMENDATION_AVAILABLE
    ):
        sections.append({
            "title": "Recommended action",
            "body": (
                f"{recommendation.recommended_option_id} "
                f"(confidence {recommendation.confidence:.0%}, "
                f"{recommendation.strength.value if recommendation.strength else 'n/a'})"
            ),
        })
        sections.append({
            "title": "Why this option wins",
            "items": recommendation.rationale.decisive_factors,
        })
        sections.append({
            "title": "Evidence against",
            "items": recommendation.rationale.evidence_against_ids,
        })
        sections.append({
            "title": "Unresolved",
            "items": recommendation.rationale.unresolved_unknowns,
        })
    else:
        sections.append({
            "title": "Agent recommendation",
            "body": (
                f"{recommendation.status.value}: "
                f"{recommendation.rationale.summary}"
            ),
        })

    if recommendation is not None and (
        recommendation.rationale.dissenting_positions
    ):
        sections.append({
            "title": "Dissent",
            "items": recommendation.rationale.dissenting_positions,
        })
    if panel.decision_authority is not None:
        sections.append({
            "title": "Decision authority",
            "body": (
                f"{panel.decision_authority.display_name or panel.decision_authority.actor_id}"
                f" — {panel.decision_authority.role or 'role unknown'}. "
                f"{panel.capsule.why_this_person_or_role}"
            ),
        })
    sections.append({
        "title": "Impact if delayed", "body": panel.capsule.impact_if_delayed,
    })
    return sections
