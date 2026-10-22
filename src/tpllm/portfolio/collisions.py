"""Collision windows and shared failure domains.

Two launches in the same week that depend on the same migration are one
risk, not two. This module finds the windows where projects collide.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tpllm.portfolio.models import (
    PortfolioCollision,
    PortfolioCollisionType,
    PortfolioGraphSnapshot,
    PortfolioNodeType,
)

_NODE_COLLISION_TYPES = {
    PortfolioNodeType.DECISION_AUTHORITY: (
        PortfolioCollisionType.SHARED_APPROVAL
    ),
    PortfolioNodeType.SERVICE: PortfolioCollisionType.SHARED_SERVICE,
    PortfolioNodeType.TEAM: PortfolioCollisionType.SHARED_TEAM,
    PortfolioNodeType.MILESTONE: PortfolioCollisionType.LAUNCH_WINDOW,
    PortfolioNodeType.KPI: PortfolioCollisionType.KPI_DEPENDENCY,
}


def _severity(project_count: int) -> str:
    if project_count >= 4:
        return "critical"
    if project_count == 3:
        return "high"
    return "moderate"


def detect_resource_collisions(
    *,
    graph: PortfolioGraphSnapshot,
    window_start: datetime,
    window_end: datetime,
    minimum_projects: int = 2,
) -> list[PortfolioCollision]:
    """Shared nodes become collisions when several projects need them in
    the same window."""
    collisions: list[PortfolioCollision] = []
    for node in sorted(graph.nodes, key=lambda n: n.id):
        collision_type = _NODE_COLLISION_TYPES.get(node.node_type)
        if collision_type is None:
            continue
        project_ids = sorted(set(node.project_ids))
        if len(project_ids) < minimum_projects:
            continue
        collisions.append(
            PortfolioCollision(
                id=f"collision-{node.id}",
                collision_type=collision_type,
                project_ids=project_ids,
                resource_ids=[node.id],
                window_start=window_start,
                window_end=window_end,
                severity=_severity(len(project_ids)),
                explanation=(
                    f"{len(project_ids)} project(s) "
                    f"({', '.join(project_ids)}) contend for "
                    f"{node.label} between "
                    f"{window_start.date().isoformat()} and "
                    f"{window_end.date().isoformat()}."
                ),
            )
        )
    return collisions


def detect_launch_window_collisions(
    *,
    launch_dates: dict[str, datetime],
    window_days: int = 7,
) -> list[PortfolioCollision]:
    """Projects launching close enough together to contend for the same
    release, on-call, and validation attention."""
    collisions: list[PortfolioCollision] = []
    ordered = sorted(launch_dates.items(), key=lambda item: (item[1], item[0]))
    grouped: list[list[tuple[str, datetime]]] = []

    for project_id, launch_date in ordered:
        placed = False
        for group in grouped:
            if abs((launch_date - group[0][1]).days) <= window_days:
                group.append((project_id, launch_date))
                placed = True
                break
        if not placed:
            grouped.append([(project_id, launch_date)])

    for index, group in enumerate(grouped):
        if len(group) < 2:
            continue
        project_ids = sorted(p for p, _ in group)
        starts = min(d for _, d in group)
        ends = max(d for _, d in group)
        collisions.append(
            PortfolioCollision(
                id=f"collision-launch-window-{index}",
                collision_type=PortfolioCollisionType.LAUNCH_WINDOW,
                project_ids=project_ids,
                resource_ids=[],
                window_start=starts,
                window_end=ends + timedelta(days=1),
                severity=_severity(len(project_ids)),
                explanation=(
                    f"{len(project_ids)} project(s) "
                    f"({', '.join(project_ids)}) launch within "
                    f"{window_days} days of one another."
                ),
            )
        )
    return collisions
