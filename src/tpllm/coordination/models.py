"""Organizational memory models.

Durable, evidence-backed organizational state: workstreams, objectives,
key dates, commitments, decisions, positions, asks, authority, and team
capacity constraints.

Architectural invariants:
- Read models are projections of ontology-backed evidence — never a
  second manually maintained truth system. Manual annotations remain
  evidence with provenance; they never replace computed state.
- Every important statement preserves provenance: source, timestamps,
  actor, role, confidence, supporting and contradicting evidence.
- The latest conclusive decision beats the latest message: conversation,
  position, proposal, tentative conclusion, conclusive decision,
  superseded and reopened decisions are explicitly distinct states.
- Expertise, ownership, authority, and implementation are different
  relations and are never flattened into one.
- Capacity is modeled as team-level structural conditions with explicit
  evidence. This layer contains no individual scoring of people.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from tpllm.core.actors import ActorReference


# ── Enums ──────────────────────────────────────────────────────────────

class WorkstreamType(str, Enum):
    PRODUCT_FEATURE = "product_feature"
    PRODUCT_COMMITMENT = "product_commitment"
    PARTNERSHIP = "partnership"
    SUPPORT = "support"
    INFRASTRUCTURE = "infrastructure"
    MIGRATION = "migration"
    RELIABILITY = "reliability"
    RESEARCH = "research"
    OPERATIONAL = "operational"
    OTHER = "other"


class WorkstreamHealth(str, Enum):
    ON_TRACK = "on_track"
    WATCH = "watch"
    AT_RISK = "at_risk"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class OrganizationalDecisionStatus(str, Enum):
    PROPOSED = "proposed"
    UNDER_DISCUSSION = "under_discussion"
    TENTATIVE = "tentative"
    CONCLUSIVE = "conclusive"
    SUPERSEDED = "superseded"
    REOPENED = "reopened"


class PositionType(str, Enum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    PREFER = "prefer"
    CONCERN = "concern"
    NEUTRAL = "neutral"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


class ActionRequestType(str, Enum):
    DECISION = "decision"
    APPROVAL = "approval"
    REVIEW = "review"
    EXPERTISE = "expertise"
    SUPPORT = "support"
    DATA = "data"
    DEPENDENCY = "dependency"
    ALIGNMENT = "alignment"
    IMPLEMENTATION = "implementation"


class ActionRequestStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    ROUTED = "routed"
    ACKNOWLEDGED = "acknowledged"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class ActorRelationType(str, Enum):
    OWNS_SYSTEM = "owns_system"
    HAS_EXPERTISE = "has_expertise"
    IMPLEMENTS = "implements"
    MAY_APPROVE = "may_approve"
    MAY_DECIDE = "may_decide"
    HAS_HISTORICAL_CONTEXT = "has_historical_context"
    REQUESTED_ACTION = "requested_action"
    IS_BLOCKED_BY = "is_blocked_by"


class CommitmentType(str, Enum):
    INTERNAL_PRODUCT = "internal_product"
    CUSTOMER = "customer"
    PARTNER = "partner"
    EXECUTIVE = "executive"
    SUPPORT = "support"
    REGULATORY = "regulatory"


class CapacityConstraintType(str, Enum):
    ACTIVE_INCIDENT = "active_incident"
    LAUNCH_LOAD = "launch_load"
    REORGANIZATION = "reorganization"
    STAFFING_REDUCTION = "staffing_reduction"
    APPROVAL_QUEUE = "approval_queue"
    CROSS_TEAM_DEPENDENCY_LOAD = "cross_team_dependency_load"
    OTHER = "other"


# ── Actor model ──────────────────────────────────────────────────────

class OrganizationalActorRef(ActorReference):
    """Organizational context around decisions and requests.

    Extends the core ``ActorReference`` (who they are) with the
    capacities coordination owns (in what capacity). This is not an HR
    profile and never carries a quality score.
    """
    relation_types: list[ActorRelationType] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


# ── Objective chain ──────────────────────────────────────────────────

class WorkstreamObjective(BaseModel):
    """Traceable objective chain: company goal → project objective →
    workstream objective → feature → action. Actions always trace back
    to the objective they serve — no telephone game."""
    id: str
    project_id: str
    workstream_id: str
    objective: str
    parent_objective_id: str | None = None
    company_goal_ids: list[str] = Field(default_factory=list)
    product_value: str | None = None
    completion_definition: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class WorkstreamKeyDate(BaseModel):
    id: str
    workstream_id: str
    name: str
    date: datetime
    date_type: str
    hard_commitment: bool = False
    source_system: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class WorkstreamCommitment(BaseModel):
    id: str
    workstream_id: str
    commitment_type: CommitmentType
    description: str
    committed_to_actor_or_org: str | None = None
    committed_by_actor_id: str | None = None
    target_date: datetime | None = None
    status: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


# ── Decisions ────────────────────────────────────────────────────────

class DecisionPosition(BaseModel):
    """A recorded organizational position with provenance — who said it,
    in what role, when, and on what evidence. Not a sentiment score."""
    id: str
    decision_id: str
    actor: OrganizationalActorRef
    position_type: PositionType
    statement: str
    conditions: list[str] = Field(default_factory=list)
    observed_at: datetime
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class OrganizationalDecisionRecord(BaseModel):
    """An organizational decision with explicit lifecycle. Distinct from
    the flight-recorder HumanDecisionRecord — this tracks the decision as
    organizational state, with positions, authority, and supersession."""
    id: str
    project_id: str
    workstream_id: str | None = None
    topic: str
    question: str
    status: OrganizationalDecisionStatus
    conclusion: str | None = None
    decision_authority: OrganizationalActorRef | None = None
    positions: list[DecisionPosition] = Field(default_factory=list)
    decided_at: datetime | None = None
    effective_at: datetime | None = None
    supersedes_decision_id: str | None = None
    superseded_by_decision_id: str | None = None
    consequence_summary: str | None = None
    affected_objective_ids: list[str] = Field(default_factory=list)
    affected_workstream_ids: list[str] = Field(default_factory=list)
    affected_kpi_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0

    @model_validator(mode="after")
    def _check_conclusive(self) -> "OrganizationalDecisionRecord":
        if self.status == OrganizationalDecisionStatus.CONCLUSIVE:
            if not self.conclusion:
                raise ValueError("A conclusive decision requires a conclusion.")
            if self.decision_authority is None:
                raise ValueError(
                    "A conclusive decision requires a decision authority."
                )
            if self.decided_at is None:
                raise ValueError("A conclusive decision requires decided_at.")
            if not self.evidence_ids:
                raise ValueError(
                    "A conclusive decision requires at least one evidence "
                    "reference."
                )
        return self


# ── Action requests ──────────────────────────────────────────────────

class ActionRequest(BaseModel):
    """A routed ask with the requester's organizational context, the
    authority relation it needs, and its objective linkage preserved."""
    id: str
    project_id: str
    workstream_id: str | None = None
    request_type: ActionRequestType
    requested_action: str
    why_needed: str
    objective_ids: list[str] = Field(default_factory=list)
    requested_by: OrganizationalActorRef
    preferred_recipient: OrganizationalActorRef | None = None
    required_authority_relation: ActorRelationType | None = None
    required_expertise_topics: list[str] = Field(default_factory=list)
    due_at: datetime | None = None
    status: ActionRequestStatus
    blocking_reason: str | None = None
    related_decision_ids: list[str] = Field(default_factory=list)
    related_kpi_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime


# ── Capacity ─────────────────────────────────────────────────────────

class TeamCapacityConstraint(BaseModel):
    """A team-level structural condition (incident load, launch load,
    approval queues, reorganizations) with explicit evidence. Never an
    inference about any individual person."""
    id: str
    team_id: str
    constraint_type: CapacityConstraintType
    description: str
    severity: str
    effective_from: datetime
    effective_until: datetime | None = None
    affected_workstream_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


# ── Workstream state ─────────────────────────────────────────────────

class WorkstreamState(BaseModel):
    """The evidence-backed read model of one workstream — the projection
    a board renders. Health is computed from evidence, never manually
    painted."""
    id: str
    project_id: str
    workstream_id: str
    vertical: str
    feature_or_scope: str
    workstream_type: WorkstreamType
    objective: WorkstreamObjective
    key_dates: list[WorkstreamKeyDate] = Field(default_factory=list)
    current_health: WorkstreamHealth
    previous_health: WorkstreamHealth | None = None
    health_confidence: float
    key_status_signal: str
    primary_contact: OrganizationalActorRef | None = None
    latest_conclusive_decision_id: str | None = None
    open_action_request_ids: list[str] = Field(default_factory=list)
    blocker_ids: list[str] = Field(default_factory=list)
    kpi_ids: list[str] = Field(default_factory=list)
    commitment_ids: list[str] = Field(default_factory=list)
    wins: list[str] = Field(default_factory=list)
    product_value: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    as_of: datetime
