"""Portfolio topology models (Commit 28).

Project-level intelligence can report three separate problems when there
is really one. Four projects each waiting on the same approval authority
will each conclude, independently and correctly, that they are blocked —
and each will be wrong about why.

This layer models the organization as a shared dependency topology so
the common cause becomes visible.

Capacity here is a structural resource estimate — how much approval or
review bandwidth exists in a window — never a statement about what any
individual can produce.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class PortfolioNodeType(str, Enum):
    PROJECT = "project"
    WORKSTREAM = "workstream"
    TEAM = "team"
    SERVICE = "service"
    DECISION_AUTHORITY = "decision_authority"
    KPI = "kpi"
    MILESTONE = "milestone"
    COMMITMENT = "commitment"
    DEPENDENCY = "dependency"
    SYSTEM = "system"


class PortfolioEdgeType(str, Enum):
    DEPENDS_ON = "depends_on"
    REQUIRES_APPROVAL_FROM = "requires_approval_from"
    SHARES_SERVICE = "shares_service"
    SHARES_TEAM = "shares_team"
    SHARES_DEPENDENCY = "shares_dependency"
    SHARES_MILESTONE_WINDOW = "shares_milestone_window"
    AFFECTS_KPI = "affects_kpi"
    COMPETES_FOR_CAPACITY = "competes_for_capacity"
    BLOCKS = "blocks"
    SUPPORTS = "supports"


class CapacityResourceType(str, Enum):
    APPROVAL = "approval"
    REVIEW = "review"
    INTEGRATION_SUPPORT = "integration_support"
    INCIDENT_SUPPORT = "incident_support"
    DOMAIN_EXPERTISE = "domain_expertise"
    SERVICE_CHANGE = "service_change"


class PortfolioCollisionType(str, Enum):
    SHARED_APPROVAL = "shared_approval"
    SHARED_SERVICE = "shared_service"
    SHARED_TEAM = "shared_team"
    LAUNCH_WINDOW = "launch_window"
    MIGRATION_WINDOW = "migration_window"
    CUSTOMER_COMMITMENT = "customer_commitment"
    KPI_DEPENDENCY = "kpi_dependency"


# ── Graph ────────────────────────────────────────────────────────────

class PortfolioNode(BaseModel):
    id: str
    node_type: PortfolioNodeType
    canonical_entity_id: str | None = None
    label: str
    project_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PortfolioEdge(BaseModel):
    id: str
    source_id: str
    target_id: str
    edge_type: PortfolioEdgeType
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0

    @model_validator(mode="after")
    def _check(self) -> "PortfolioEdge":
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("Edge confidence must be within [0, 1].")
        return self


class PortfolioGraphSnapshot(BaseModel):
    """A projection across projects. The Commit 2 structural graph is
    never mutated to build this."""
    id: str
    as_of: datetime
    nodes: list[PortfolioNode] = Field(default_factory=list)
    edges: list[PortfolioEdge] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    state_fingerprint: str = ""

    def node(self, node_id: str) -> PortfolioNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def edges_from(self, node_id: str) -> list[PortfolioEdge]:
        return [e for e in self.edges if e.source_id == node_id]

    def edges_to(self, node_id: str) -> list[PortfolioEdge]:
        return [e for e in self.edges if e.target_id == node_id]


def build_portfolio_fingerprint(
    *, project_ids: list[str], node_ids: list[str], edge_ids: list[str],
) -> str:
    payload = "|".join([
        f"projects={','.join(sorted(project_ids))}",
        f"nodes={','.join(sorted(node_ids))}",
        f"edges={','.join(sorted(edge_ids))}",
    ])
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


# ── Capacity ─────────────────────────────────────────────────────────

class OrganizationalCapacityWindow(BaseModel):
    """A structural estimate of available organizational bandwidth.

    This describes a *resource* — an approval queue, a review capacity —
    not a person's output. Estimated capacity and confidence are kept
    separate because capacity is frequently uncertain, and claiming
    "Platform can make exactly two decisions" would overstate it.
    """
    resource_id: str
    resource_type: CapacityResourceType
    team_id: str | None = None
    authority_entity_id: str | None = None
    window_start: datetime
    window_end: datetime
    estimated_capacity_units: float
    committed_capacity_units: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5

    @model_validator(mode="after")
    def _check(self) -> "OrganizationalCapacityWindow":
        if self.window_end <= self.window_start:
            raise ValueError("Capacity window must end after it starts.")
        if self.estimated_capacity_units < 0:
            raise ValueError("Estimated capacity cannot be negative.")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("Capacity confidence must be within [0, 1].")
        return self

    @property
    def available_capacity_units(self) -> float:
        return max(
            0.0, self.estimated_capacity_units - self.committed_capacity_units
        )


class PortfolioCapacityDemand(BaseModel):
    id: str
    project_id: str
    workstream_id: str | None = None
    resource_type: CapacityResourceType
    required_resource_id: str
    requested_units: float = 1.0
    earliest_needed_at: datetime
    latest_needed_at: datetime
    criticality: float = 0.5
    alternative_resource_ids: list[str] = Field(default_factory=list)
    has_workaround: bool = False
    workaround_description: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "PortfolioCapacityDemand":
        if not (0.0 <= self.criticality <= 1.0):
            raise ValueError("Criticality must be within [0, 1].")
        if self.requested_units <= 0:
            raise ValueError("Requested units must be positive.")
        return self


class PortfolioBottleneck(BaseModel):
    id: str
    resource_id: str
    resource_type: CapacityResourceType
    affected_project_ids: list[str] = Field(default_factory=list)
    affected_workstream_ids: list[str] = Field(default_factory=list)
    demand_units: float
    available_units: float
    overload_ratio: float
    severity: str
    capacity_confidence: float = 0.5
    earliest_impact_at: datetime | None = None
    downstream_milestone_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str = ""


class PortfolioCollision(BaseModel):
    id: str
    collision_type: PortfolioCollisionType
    project_ids: list[str] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
    window_start: datetime
    window_end: datetime
    severity: str
    explanation: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class SharedFailureDomain(BaseModel):
    """Projects that believed themselves independent but are not."""
    id: str
    resource_id: str
    resource_type: str
    project_ids: list[str] = Field(default_factory=list)
    single_point_of_failure: bool = False
    projects_without_alternative: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str = ""


class PortfolioInterventionLeverage(BaseModel):
    """One action that resolves several projects' problems at once."""
    intervention_id: str
    description: str = ""
    affected_project_ids: list[str] = Field(default_factory=list)
    estimated_total_risk_reduction: float = 0.0
    affected_bottleneck_ids: list[str] = Field(default_factory=list)
    coordination_cost: float = 0.0
    leverage_score: float = 0.0
    explanation: str = ""


class PortfolioOpportunityCost(BaseModel):
    """What the displaced project pays so the selected one can proceed."""
    selected_demand_id: str
    displaced_demand_id: str
    selected_project_benefit: float
    displaced_project_cost: float
    net_portfolio_effect: float
    displaced_has_alternative: bool = False
    explanation: str = ""


class PortfolioPriorityRecommendation(BaseModel):
    id: str
    resource_id: str
    ordered_demand_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    expected_portfolio_outcome_change: dict[str, float] = Field(
        default_factory=dict
    )
    displaced_demand_impacts: dict[str, float] = Field(default_factory=dict)
    alternative_routes: dict[str, list[str]] = Field(default_factory=dict)
    opportunity_costs: list[PortfolioOpportunityCost] = Field(
        default_factory=list
    )
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
