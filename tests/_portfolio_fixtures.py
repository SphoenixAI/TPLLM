"""Fixtures for portfolio tests.

Models the spec's canonical situation: four projects, one Platform
Architecture approval queue with room for about two decisions before
Friday, and differing alternative routes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from lltpm.portfolio import (
    CapacityResourceType,
    OrganizationalCapacityWindow,
    PortfolioCapacityDemand,
)

T0 = datetime(2026, 11, 16, 9, 0, 0)
FRIDAY = T0 + timedelta(days=5)

PLATFORM = "Platform Architecture"
ACCOUNT_GRAPH = "Account Graph"


def project_state(project_id: str):
    """A minimal duck-typed project state — the portfolio layer only
    reads project_id from it."""
    return SimpleNamespace(project_id=project_id)


def workstream(project_id: str, workstream_id: str, kpi_ids=None):
    return SimpleNamespace(
        project_id=project_id,
        workstream_id=workstream_id,
        kpi_ids=list(kpi_ids or []),
    )


def approval_window(
    *,
    resource_id: str = PLATFORM,
    capacity: float = 2.0,
    committed: float = 0.0,
    confidence: float = 0.6,
    start: datetime | None = None,
    end: datetime | None = None,
) -> OrganizationalCapacityWindow:
    return OrganizationalCapacityWindow(
        resource_id=resource_id,
        resource_type=CapacityResourceType.APPROVAL,
        team_id="platform-architecture",
        authority_entity_id="person_priya_rao",
        window_start=start or T0,
        window_end=end or FRIDAY,
        estimated_capacity_units=capacity,
        committed_capacity_units=committed,
        evidence_ids=["ev-approval-queue"],
        confidence=confidence,
    )


def demand(
    *,
    demand_id: str,
    project_id: str,
    criticality: float,
    has_workaround: bool = False,
    workaround: str | None = None,
    alternatives: list[str] | None = None,
    resource_id: str = PLATFORM,
    units: float = 1.0,
) -> PortfolioCapacityDemand:
    return PortfolioCapacityDemand(
        id=demand_id,
        project_id=project_id,
        workstream_id=f"ws-{project_id}",
        resource_type=CapacityResourceType.APPROVAL,
        required_resource_id=resource_id,
        requested_units=units,
        earliest_needed_at=T0,
        latest_needed_at=FRIDAY,
        criticality=criticality,
        alternative_resource_ids=list(alternatives or []),
        has_workaround=has_workaround,
        workaround_description=workaround,
        evidence_ids=[f"ev-{demand_id}"],
    )


def four_approval_demands() -> list[PortfolioCapacityDemand]:
    """The spec's canonical case.

    A: high urgency, moderate workaround
    B: medium urgency, strong workaround
    C: high urgency, no workaround
    D: lower urgency
    """
    return [
        demand(
            demand_id="dem-a", project_id="project-a", criticality=0.95,
            has_workaround=True,
            workaround="Partial enterprise-only rollout",
        ),
        demand(
            demand_id="dem-b", project_id="project-b", criticality=0.70,
            has_workaround=True, workaround="Continue on contract v2",
            alternatives=["Platform Deputy"],
        ),
        demand(
            demand_id="dem-c", project_id="project-c", criticality=0.90,
        ),
        demand(
            demand_id="dem-d", project_id="project-d", criticality=0.55,
            has_workaround=True, workaround="Defer to next review window",
        ),
    ]


def four_project_graph_inputs():
    """Four projects: A/B/C need Platform approval; A and C share the
    Account Graph service; D is independent."""
    return dict(
        project_states=[
            project_state("project-a"), project_state("project-b"),
            project_state("project-c"), project_state("project-d"),
        ],
        workstreams=[
            workstream("project-a", "ws-project-a", ["kpi-contract"]),
            workstream("project-b", "ws-project-b"),
            workstream("project-c", "ws-project-c", ["kpi-contract"]),
            workstream("project-d", "ws-project-d"),
        ],
        approval_requirements={
            "project-a": [PLATFORM],
            "project-b": [PLATFORM],
            "project-c": [PLATFORM],
            "project-d": ["Partner Ops"],
        },
        service_usage={
            "project-a": [ACCOUNT_GRAPH],
            "project-c": [ACCOUNT_GRAPH],
            "project-d": ["Partner Gateway"],
        },
        as_of=T0,
    )


class FakeIdentityRegistry:
    """Duck-typed stand-in for the identity registry, so cross-project
    aliases resolve onto one canonical node."""

    def __init__(self, entities: dict):
        self.entities = entities


def platform_alias_registry() -> FakeIdentityRegistry:
    return FakeIdentityRegistry({
        "authority_platform_architecture": SimpleNamespace(
            active=True,
            canonical_name=PLATFORM,
            aliases=["platform-arch", "Platform Arch", "PlatArch"],
        ),
    })
