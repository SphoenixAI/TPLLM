"""Ask routing — the right ask to the right authority with the right
context, and no pointless pings.

Routing order: required decision authority → correct system owner →
relevant expertise → historical context. Capacity constraints may change
timing or escalation recommendations; they never replace authority, and
no numerical person score exists anywhere in this module.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from tpllm.coordination.models import (
    ActionRequest,
    ActionRequestType,
    ActorRelationType,
    OrganizationalActorRef,
    OrganizationalDecisionRecord,
    OrganizationalDecisionStatus,
    TeamCapacityConstraint,
)
from tpllm.intelligence.project_understanding import (
    ProjectEvidence,
    ProjectUnderstandingSnapshot,
    UnderstandingClaimStatus,
)


class ExpertiseRequirement(BaseModel):
    topic: str
    why_needed: str
    required_depth: str
    related_system_ids: list[str] = Field(default_factory=list)


class RoutingCandidate(BaseModel):
    actor: OrganizationalActorRef
    authority_match: bool
    ownership_match: bool
    expertise_match: bool
    historical_context_match: bool
    relevant_capacity_constraint_ids: list[str]
    routing_reason: str
    eligible: bool


class ActionRoutingPlan(BaseModel):
    request_id: str
    primary_recipient: OrganizationalActorRef | None
    backup_recipient: OrganizationalActorRef | None
    context_packet_id: str | None
    decision_capsule_id: str | None
    routing_reason: str
    missing_authority: bool = False
    expertise_gap: bool = False
    capacity_warning: str | None = None
    recommended_delivery_mode: str


# ── Candidate identification ─────────────────────────────────────────

def _relations_of(item: object) -> tuple[OrganizationalActorRef, set, set]:
    actor = getattr(item, "actor", item)
    relations = set(
        getattr(item, "relation_types", None) or actor.relation_types
    )
    topics = {
        t.lower() for t in (getattr(item, "expertise_topics", None) or [])
    }
    return actor, relations, topics


def identify_routing_candidates(
    *,
    request: ActionRequest,
    actor_relations: list[object],
    expertise_requirements: list[ExpertiseRequirement],
    capacity_constraints: list[TeamCapacityConstraint],
) -> list[RoutingCandidate]:
    """Deterministic candidates ordered authority → ownership →
    expertise → historical context. Capacity constraints attach as
    context; they never disqualify an authority."""
    required_topics = {
        t.lower() for t in request.required_expertise_topics
    } | {r.topic.lower() for r in expertise_requirements}

    candidates: list[RoutingCandidate] = []
    for item in actor_relations:
        actor, relations, topics = _relations_of(item)

        authority_match = (
            request.required_authority_relation is not None
            and request.required_authority_relation in relations
        )
        ownership_match = ActorRelationType.OWNS_SYSTEM in relations
        expertise_match = ActorRelationType.HAS_EXPERTISE in relations and (
            not required_topics or bool(topics & required_topics)
        )
        historical_match = ActorRelationType.HAS_HISTORICAL_CONTEXT in relations

        constraint_ids = [
            c.id
            for c in capacity_constraints
            if actor.team_id is not None and c.team_id == actor.team_id
        ]

        reasons: list[str] = []
        if authority_match:
            reasons.append(
                f"holds the required "
                f"{request.required_authority_relation.value} authority"
            )
        if ownership_match:
            reasons.append("owns the affected system")
        if expertise_match:
            reasons.append("has the required expertise")
        if historical_match:
            reasons.append("carries historical context")

        eligible = (
            authority_match or ownership_match or expertise_match
            or historical_match
        )
        candidates.append(
            RoutingCandidate(
                actor=actor,
                authority_match=authority_match,
                ownership_match=ownership_match,
                expertise_match=expertise_match,
                historical_context_match=historical_match,
                relevant_capacity_constraint_ids=constraint_ids,
                routing_reason=(
                    "; ".join(reasons)
                    if reasons
                    else "no relevant relation to this request"
                ),
                eligible=eligible,
            )
        )

    candidates.sort(
        key=lambda c: (
            not c.authority_match,
            not c.ownership_match,
            not c.expertise_match,
            not c.historical_context_match,
            c.actor.actor_id,
        )
    )
    return candidates


# ── Routing plan ─────────────────────────────────────────────────────

_AUTHORITY_TYPES = {ActionRequestType.DECISION, ActionRequestType.APPROVAL}


def build_action_routing_plan(
    *,
    request: ActionRequest,
    candidates: list[RoutingCandidate],
    context_packet_id: str | None = None,
    decision_capsule_id: str | None = None,
) -> ActionRoutingPlan:
    eligible = [c for c in candidates if c.eligible]
    authority = [c for c in eligible if c.authority_match]

    requires_authority = (
        request.required_authority_relation is not None
        or request.request_type in _AUTHORITY_TYPES
    )
    missing_authority = requires_authority and not authority

    if authority:
        primary = authority[0]
        backup = authority[1] if len(authority) > 1 else (
            next((c for c in eligible if not c.authority_match), None)
        )
    elif eligible and not requires_authority:
        primary = eligible[0]
        backup = eligible[1] if len(eligible) > 1 else None
    else:
        primary = None
        backup = eligible[0] if eligible else None

    expertise_gap = bool(request.required_expertise_topics) and not any(
        c.expertise_match for c in eligible
    )

    capacity_warning = None
    warn_source = primary or backup
    if warn_source and warn_source.relevant_capacity_constraint_ids:
        capacity_warning = (
            f"Recipient team is under capacity constraints "
            f"({', '.join(warn_source.relevant_capacity_constraint_ids)}); "
            f"expect slower turnaround or escalate timing — the authority "
            f"itself is unchanged."
        )

    if missing_authority:
        mode = (
            "MEETING_DECISION"
            if request.request_type in _AUTHORITY_TYPES
            else "MEETING_DISCUSSION"
        )
        reason = (
            "No candidate holds the required authority — explicit gap; "
            "resolve the authority before routing."
        )
    elif request.request_type in _AUTHORITY_TYPES:
        mode = "DECISION_CAPSULE"
        reason = primary.routing_reason if primary else "authority located"
    elif request.request_type == ActionRequestType.EXPERTISE:
        mode = "EXPERTISE_REQUEST"
        reason = primary.routing_reason if primary else "expertise requested"
    elif request.request_type == ActionRequestType.ALIGNMENT:
        mode = "MEETING_DISCUSSION"
        reason = "multi-party alignment needs discussion"
    else:
        mode = "ASYNC_CONTEXT_PACKET"
        reason = primary.routing_reason if primary else "async context"

    return ActionRoutingPlan(
        request_id=request.id,
        primary_recipient=primary.actor if primary else None,
        backup_recipient=backup.actor if backup else None,
        context_packet_id=context_packet_id,
        decision_capsule_id=decision_capsule_id,
        routing_reason=reason,
        missing_authority=missing_authority,
        expertise_gap=expertise_gap,
        capacity_warning=capacity_warning,
        recommended_delivery_mode=mode,
    )


# ── No-pointless-ping guardrail ──────────────────────────────────────

_STOPWORDS = {
    "a", "an", "and", "are", "be", "by", "do", "does", "for", "from", "has",
    "have", "how", "in", "is", "it", "of", "on", "or", "the", "this", "to",
    "we", "what", "which", "who", "will", "with", "should", "must",
}


def _tokens(text: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower())
        if w not in _STOPWORDS
    }


_ANSWER_TOKEN_OVERLAP = 3


def answer_already_exists(
    *,
    request: ActionRequest,
    decisions: list[OrganizationalDecisionRecord],
    understanding: ProjectUnderstandingSnapshot,
    evidence: list[ProjectEvidence],
) -> bool:
    """True when a conclusive decision or supported claim already answers
    the ask — in which case the answer plus evidence is returned instead
    of a new ping."""
    request_tokens = _tokens(
        f"{request.requested_action} {request.why_needed}"
    )

    for decision in decisions:
        if decision.status != OrganizationalDecisionStatus.CONCLUSIVE:
            continue
        if decision.superseded_by_decision_id is not None:
            continue
        if decision.id in request.related_decision_ids:
            return True
        decision_tokens = _tokens(
            f"{decision.topic} {decision.question} {decision.conclusion or ''}"
        )
        if len(request_tokens & decision_tokens) >= _ANSWER_TOKEN_OVERLAP:
            return True

    for claim in understanding.claims:
        if claim.status != UnderstandingClaimStatus.SUPPORTED:
            continue
        if len(request_tokens & _tokens(claim.statement)) >= (
            _ANSWER_TOKEN_OVERLAP
        ):
            return True

    return False
