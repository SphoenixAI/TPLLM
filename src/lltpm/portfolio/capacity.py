"""Organizational capacity and bottleneck detection.

Four projects each waiting on the same approval will each report being
blocked. This module adds up the demand against the capacity and names
the single cause.

Capacity estimates carry their confidence separately and are always
described as approximate — "sufficient for approximately two" rather
than "Platform can make exactly two decisions".
"""

from __future__ import annotations

from datetime import datetime

from lltpm.portfolio.models import (
    OrganizationalCapacityWindow,
    PortfolioBottleneck,
    PortfolioCapacityDemand,
    SharedFailureDomain,
)

_SEVERITY_THRESHOLDS = ((3.0, "critical"), (2.0, "high"), (1.25, "moderate"))


def _severity(overload_ratio: float) -> str:
    for threshold, label in _SEVERITY_THRESHOLDS:
        if overload_ratio >= threshold:
            return label
    return "low"


def _windows_overlap(
    demand: PortfolioCapacityDemand,
    window: OrganizationalCapacityWindow,
) -> bool:
    return (
        demand.earliest_needed_at < window.window_end
        and demand.latest_needed_at > window.window_start
    )


def aggregate_demand(
    *,
    demands: list[PortfolioCapacityDemand],
    resource_id: str,
    window: OrganizationalCapacityWindow | None = None,
) -> list[PortfolioCapacityDemand]:
    """Demands competing for one resource inside the capacity window."""
    return sorted(
        (
            d for d in demands
            if d.required_resource_id == resource_id
            and (window is None or _windows_overlap(d, window))
        ),
        key=lambda d: d.id,
    )


def detect_bottlenecks(
    *,
    demands: list[PortfolioCapacityDemand],
    capacity_windows: list[OrganizationalCapacityWindow],
    milestone_links: dict[str, list[str]] | None = None,
) -> list[PortfolioBottleneck]:
    """Where demand for a shared resource exceeds its estimated capacity.

    Four independent approval requests against a two-decision window
    become one bottleneck, not four unrelated pending approvals.
    """
    milestone_links = milestone_links or {}
    bottlenecks: list[PortfolioBottleneck] = []

    for window in sorted(capacity_windows, key=lambda w: w.resource_id):
        competing = aggregate_demand(
            demands=demands, resource_id=window.resource_id, window=window,
        )
        if not competing:
            continue
        demand_units = sum(d.requested_units for d in competing)
        available = window.available_capacity_units
        if demand_units <= available:
            continue

        overload = demand_units / available if available > 0 else float(
            demand_units
        )
        project_ids = sorted({d.project_id for d in competing})
        workstream_ids = sorted({
            d.workstream_id for d in competing if d.workstream_id
        })
        milestones: list[str] = []
        for project_id in project_ids:
            for milestone in milestone_links.get(project_id, []):
                if milestone not in milestones:
                    milestones.append(milestone)

        evidence: list[str] = list(window.evidence_ids)
        for demand in competing:
            for eid in demand.evidence_ids:
                if eid not in evidence:
                    evidence.append(eid)

        bottlenecks.append(
            PortfolioBottleneck(
                id=f"bottleneck-{window.resource_id}",
                resource_id=window.resource_id,
                resource_type=window.resource_type,
                affected_project_ids=project_ids,
                affected_workstream_ids=workstream_ids,
                demand_units=demand_units,
                available_units=available,
                overload_ratio=round(overload, 4),
                severity=_severity(overload),
                capacity_confidence=window.confidence,
                earliest_impact_at=min(
                    (d.latest_needed_at for d in competing), default=None,
                ),
                downstream_milestone_ids=milestones,
                evidence_ids=evidence,
                explanation=explain_bottleneck(
                    resource_id=window.resource_id,
                    project_ids=project_ids,
                    demand_units=demand_units,
                    available=available,
                    window=window,
                ),
            )
        )
    return bottlenecks


def explain_bottleneck(
    *,
    resource_id: str,
    project_ids: list[str],
    demand_units: float,
    available: float,
    window: OrganizationalCapacityWindow,
) -> str:
    """Structural explanation, never a judgment about a team.

    Reports what the resource is carrying and that the requesting
    projects share one cause — not that anyone is slow.
    """
    days = max(1, (window.window_end - window.window_start).days)
    hedge = (
        "approximately" if window.confidence < 0.85 else "an estimated"
    )
    return (
        f"{resource_id} is the required {window.resource_type.value} "
        f"resource for {len(project_ids)} project(s) "
        f"({', '.join(project_ids)}) in the same {days}-day window. "
        f"Estimated capacity is sufficient for {hedge} "
        f"{available:.0f} of {demand_units:.0f} requested unit(s) "
        f"(confidence {window.confidence:.2f}). Each requesting project "
        f"may read its delay as local, but they share one constraint."
    )


def detect_shared_failure_domains(
    *,
    demands: list[PortfolioCapacityDemand],
    capacity_windows: list[OrganizationalCapacityWindow] | None = None,
    minimum_projects: int = 2,
) -> list[SharedFailureDomain]:
    """Resources several projects depend on, with the projects that have
    no alternative route called out."""
    by_resource: dict[str, list[PortfolioCapacityDemand]] = {}
    for demand in demands:
        by_resource.setdefault(demand.required_resource_id, []).append(demand)

    windows = {w.resource_id: w for w in (capacity_windows or [])}
    domains: list[SharedFailureDomain] = []

    for resource_id in sorted(by_resource):
        competing = by_resource[resource_id]
        project_ids = sorted({d.project_id for d in competing})
        if len(project_ids) < minimum_projects:
            continue

        stranded = sorted({
            d.project_id for d in competing
            if not d.alternative_resource_ids and not d.has_workaround
        })
        window = windows.get(resource_id)
        evidence: list[str] = []
        for demand in competing:
            for eid in demand.evidence_ids:
                if eid not in evidence:
                    evidence.append(eid)

        domains.append(
            SharedFailureDomain(
                id=f"failure-domain-{resource_id}",
                resource_id=resource_id,
                resource_type=(
                    window.resource_type.value if window
                    else competing[0].resource_type.value
                ),
                project_ids=project_ids,
                single_point_of_failure=bool(stranded),
                projects_without_alternative=stranded,
                evidence_ids=evidence,
                explanation=(
                    f"{len(project_ids)} project(s) depend on "
                    f"{resource_id}"
                    + (
                        f"; {', '.join(stranded)} have no alternative route "
                        f"and no workaround, so a failure here is not "
                        f"locally survivable for them."
                        if stranded
                        else "; every dependent project has an alternative "
                             "route or a workaround."
                    )
                ),
            )
        )
    return domains
