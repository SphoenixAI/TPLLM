"""Shared fixtures for coordination-layer tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from tpllm.coordination import (
    ActionRequest,
    ActionRequestStatus,
    ActionRequestType,
    ActorRelationType,
    OrganizationalActorRef,
    OrganizationalDecisionRecord,
    OrganizationalDecisionStatus,
    WorkstreamObjective,
    WorkstreamType,
)
from tpllm.intelligence import (
    ProjectArchitectureMap,
    ProjectPurposeModel,
    ProjectSignificanceModel,
    ProjectUnderstandingSnapshot,
    TechnicalMechanismModel,
    UnderstandingCoverage,
)

AS_OF = datetime(2026, 8, 10, 12, 0, 0)
PROJECT = "atlas"
WS = "ws-account-graph"


def actor(
    actor_id: str = "priya",
    role: str | None = "Platform Architecture Lead",
    team: str | None = "platform-architecture",
    relations: list[ActorRelationType] | None = None,
) -> OrganizationalActorRef:
    return OrganizationalActorRef(
        actor_id=actor_id,
        display_name=actor_id.title(),
        role=role,
        team_id=team,
        team_name=team,
        relation_types=relations or [],
        evidence_ids=[f"ev-actor-{actor_id}"],
    )


def objective(**overrides) -> WorkstreamObjective:
    defaults = dict(
        id="obj-graph",
        project_id=PROJECT,
        workstream_id=WS,
        objective="Move authorization onto the shared account graph",
        company_goal_ids=["goal-platform-consolidation"],
        product_value="Single authorization layer for all clients",
        completion_definition=["All clients resolve permissions via graph"],
        evidence_ids=["ev-objective"],
        confidence=0.7,
    )
    defaults.update(overrides)
    return WorkstreamObjective(**defaults)


def decision(
    did: str = "dec-1",
    status: OrganizationalDecisionStatus = OrganizationalDecisionStatus.CONCLUSIVE,
    decided_days_ago: int = 2,
    workstream_id: str = WS,
    **overrides,
) -> OrganizationalDecisionRecord:
    defaults = dict(
        id=did,
        project_id=PROJECT,
        workstream_id=workstream_id,
        topic="Billing contract version",
        question="Which contract version do downstream teams integrate against?",
        status=status,
        conclusion=(
            "Contract v3 preferred, pending prepaid validation"
            if status == OrganizationalDecisionStatus.CONCLUSIVE
            else None
        ),
        decision_authority=(
            actor() if status == OrganizationalDecisionStatus.CONCLUSIVE else None
        ),
        decided_at=(
            AS_OF - timedelta(days=decided_days_ago)
            if status == OrganizationalDecisionStatus.CONCLUSIVE
            else None
        ),
        evidence_ids=(
            [f"ev-{did}"]
            if status == OrganizationalDecisionStatus.CONCLUSIVE
            else []
        ),
    )
    defaults.update(overrides)
    return OrganizationalDecisionRecord(**defaults)


def request(
    rid: str = "req-1",
    status: ActionRequestStatus = ActionRequestStatus.ROUTED,
    request_type: ActionRequestType = ActionRequestType.APPROVAL,
    workstream_id: str = WS,
    **overrides,
) -> ActionRequest:
    defaults = dict(
        id=rid,
        project_id=PROJECT,
        workstream_id=workstream_id,
        request_type=request_type,
        requested_action="Approve contract v3 for enterprise accounts",
        why_needed="Approval blocks three downstream integrations",
        objective_ids=["obj-graph"],
        requested_by=actor(
            "dana", role="Payments Integration Lead", team="payments",
        ),
        required_authority_relation=ActorRelationType.MAY_APPROVE,
        status=status,
        evidence_ids=[f"ev-{rid}"],
        created_at=AS_OF - timedelta(days=3),
    )
    defaults.update(overrides)
    return ActionRequest(**defaults)


def understanding(
    contradictions=(), evidence_freshness: float = 0.8,
) -> ProjectUnderstandingSnapshot:
    return ProjectUnderstandingSnapshot(
        id="pus-coord",
        project_id=PROJECT,
        as_of=AS_OF,
        purpose=ProjectPurposeModel(
            project_id=PROJECT,
            one_sentence_purpose="Replace entitlement lookup with the graph",
        ),
        significance=ProjectSignificanceModel(project_id=PROJECT),
        technical_mechanism=TechnicalMechanismModel(project_id=PROJECT),
        architecture=ProjectArchitectureMap(project_id=PROJECT),
        contradictions=list(contradictions),
        coverage=UnderstandingCoverage(
            overall_coverage=0.5, evidence_freshness=evidence_freshness,
        ),
        evidence_ids=["pev-base"],
        generated_at=AS_OF,
    )


def signal(kind: str, description: str | None = None, **extra) -> SimpleNamespace:
    return SimpleNamespace(
        signal_kind=kind,
        description=description or kind.replace("_", " "),
        evidence_ids=[f"ev-sig-{kind}"],
        **extra,
    )
