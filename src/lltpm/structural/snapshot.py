"""Graph snapshots — point-in-time captures of the structural projection.

Snapshots support both teammate proposals: scheduled (daily/weekly) cadence
AND event-triggered capture (PR opened, review requested, dependency created,
decision changed). They persist through the existing ArtifactStore so the
structural layer needs zero new infrastructure.

Event-trigger hook points are defined here as enum values; the webhook
pipeline wires them in. Scheduled capture works today.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

import networkx as nx
from pydantic import BaseModel, Field


class SnapshotTrigger(str, Enum):
    SCHEDULED_DAILY = "scheduled_daily"
    SCHEDULED_WEEKLY = "scheduled_weekly"
    EVENT_PR_OPENED = "event_pr_opened"
    EVENT_REVIEW_REQUESTED = "event_review_requested"
    EVENT_DEPENDENCY_CREATED = "event_dependency_created"
    EVENT_DECISION_CHANGED = "event_decision_changed"
    MANUAL = "manual"


class GraphSnapshot(BaseModel):
    """A captured set of node/edge ids for a scope at a moment in time.

    Stores ids (not the full graph) so it stays light; the graph can be
    re-projected from the ontology when a snapshot is replayed.
    """
    id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    scope_type: str = "program"
    scope_object_id: str = ""
    node_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)
    trigger: SnapshotTrigger = SnapshotTrigger.SCHEDULED_DAILY
    node_count: int = 0
    edge_count: int = 0


def snapshot_graph(
    graph: nx.MultiDiGraph,
    scope_object_id: str,
    scope_type: str = "program",
    trigger: SnapshotTrigger = SnapshotTrigger.SCHEDULED_DAILY,
    snapshot_id: str = "",
) -> GraphSnapshot:
    """Capture a GraphSnapshot from a live NetworkX graph."""
    node_ids = [str(n) for n in graph.nodes()]
    edge_ids = [
        str(k) for _, _, k in graph.edges(keys=True) if k is not None
    ]
    return GraphSnapshot(
        id=snapshot_id or f"snap-{scope_object_id}-{int(datetime.utcnow().timestamp())}",
        scope_type=scope_type,
        scope_object_id=scope_object_id,
        node_ids=node_ids,
        edge_ids=edge_ids,
        trigger=trigger,
        node_count=graph.number_of_nodes(),
        edge_count=graph.number_of_edges(),
    )
