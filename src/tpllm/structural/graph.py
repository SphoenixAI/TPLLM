"""Graph projection — read the existing ontology as a living topology.

The structural intelligence layer treats the program (people, teams, work
items, dependencies, milestones, decisions, API contracts) as a directed
graph. This module defines the storage-agnostic node/edge projection and the
``project_to_graph`` function that builds a NetworkX graph FROM the existing
ontology entities.

Crucially, this is a *projection*, not a rewrite: ``project_to_graph`` reads
``WorkItem.blocks/blocked_by/dependency_ids``, ``Dependency`` relationships,
``Milestone`` dependencies, and ownership — it never mutates the ontology.

The ``OntologyNode``/``OntologyEdge`` models keep the door open to a future
Neo4j/Postgres backend; today the graph lives in memory as a
``networkx.MultiDiGraph``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

import networkx as nx
from pydantic import BaseModel, Field

from tpllm.ontology.core import SourceRef
from tpllm.ontology.entities import (
    Dependency,
    Milestone,
    Person,
    Team,
    WorkItem,
)


# ── Node & Edge Types ───────────────────────────────────────────────

class NodeType(str, Enum):
    PERSON = "person"
    TEAM = "team"
    REPOSITORY = "repository"
    SERVICE = "service"
    COMPONENT = "component"
    DOMAIN = "domain"
    WORK_ITEM = "work_item"
    PULL_REQUEST = "pull_request"
    MILESTONE = "milestone"
    DECISION = "decision"
    DEPENDENCY = "dependency"
    API_CONTRACT = "api_contract"


class EdgeType(str, Enum):
    OWNS = "owns"
    MODIFIES = "modifies"
    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"
    REVIEWS = "reviews"
    REQUIRES_APPROVAL = "requires_approval"
    BELONGS_TO = "belongs_to"
    TOUCHES = "touches"
    TARGETS = "targets"
    IMPLEMENTS = "implements"
    SUPERSEDES = "supersedes"


# ── Projection Models ───────────────────────────────────────────────

class OntologyNode(BaseModel):
    """A node in the structural projection of the ontology.

    ``canonical_object_id`` traces back to the underlying ontology entity,
    so a graph finding can always be explained in terms of real objects.
    """
    id: str = ""
    node_type: NodeType = NodeType.WORK_ITEM
    canonical_object_id: str = ""
    label: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class OntologyEdge(BaseModel):
    """A directed, typed relationship between two projected nodes."""
    id: str = ""
    from_node_id: str = ""
    to_node_id: str = ""
    edge_type: EdgeType = EdgeType.DEPENDS_ON
    weight: float = 1.0
    confidence: float = 1.0
    source_refs: list[SourceRef] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_until: datetime | None = None


# ── Graph Construction Helpers ──────────────────────────────────────

# Edge types that represent downstream propagation of risk/blocking. Used by
# blast-radius and dependency-congestion metrics.
PROPAGATING_EDGE_TYPES: frozenset[str] = frozenset(
    {EdgeType.BLOCKS.value, EdgeType.DEPENDS_ON.value}
)


def new_graph() -> nx.MultiDiGraph:
    """Return an empty directed multigraph for structural analysis."""
    return nx.MultiDiGraph()


def add_node(graph: nx.MultiDiGraph, node: OntologyNode) -> None:
    """Add (or enrich) a typed node on the graph."""
    graph.add_node(
        node.id,
        node_type=node.node_type,
        canonical_object_id=node.canonical_object_id or node.id,
        label=node.label,
        properties=node.properties,
    )


def add_edge(graph: nx.MultiDiGraph, edge: OntologyEdge) -> None:
    """Add a typed edge on the graph, keyed by the edge id."""
    graph.add_edge(
        edge.from_node_id,
        edge.to_node_id,
        key=edge.id or None,
        edge_type=edge.edge_type,
        weight=edge.weight,
        confidence=edge.confidence,
    )


def graph_from(
    nodes: list[OntologyNode],
    edges: list[OntologyEdge],
) -> nx.MultiDiGraph:
    """Build a NetworkX graph from explicit node/edge projections."""
    graph = new_graph()
    for node in nodes:
        add_node(graph, node)
    for edge in edges:
        _ensure_node(graph, edge.from_node_id)
        _ensure_node(graph, edge.to_node_id)
        add_edge(graph, edge)
    return graph


def _ensure_node(graph: nx.MultiDiGraph, node_id: str) -> None:
    """Make sure a referenced node exists so edges never dangle."""
    if node_id and node_id not in graph:
        add_node(
            graph,
            OntologyNode(id=node_id, node_type=NodeType.WORK_ITEM, canonical_object_id=node_id),
        )


def _set_type(graph: nx.MultiDiGraph, node_id: str, node_type: NodeType, label: str = "") -> None:
    """Add a node with a known type, without clobbering richer existing data."""
    if node_id in graph:
        graph.nodes[node_id]["node_type"] = node_type
        if label and not graph.nodes[node_id].get("label"):
            graph.nodes[node_id]["label"] = label
    else:
        add_node(
            graph,
            OntologyNode(
                id=node_id,
                node_type=node_type,
                canonical_object_id=node_id,
                label=label,
            ),
        )


# ── Projection from Ontology Entities ───────────────────────────────

def project_to_graph(entities: list[Any]) -> nx.MultiDiGraph:
    """Project a list of ontology entities into a NetworkX graph.

    Reads the structural relationships already present on the entities:
    ``WorkItem.blocks/blocked_by/dependency_ids`` + ownership,
    ``Dependency`` upstream/downstream/dependent links,
    ``Milestone.dependency_milestone_ids``, and ``Person.team_id``.

    The ontology is read-only here — this builds a parallel topology view.
    """
    graph = new_graph()
    _edge_seq = 0

    def _edge(frm: str, to: str, etype: EdgeType) -> None:
        nonlocal _edge_seq
        _edge_seq += 1
        _ensure_node(graph, frm)
        _ensure_node(graph, to)
        add_edge(
            graph,
            OntologyEdge(
                id=f"e-{_edge_seq}",
                from_node_id=frm,
                to_node_id=to,
                edge_type=etype,
            ),
        )

    for ent in entities:
        if isinstance(ent, WorkItem):
            _set_type(graph, ent.id, NodeType.WORK_ITEM, ent.title)
            for blocked in ent.blocks:
                _edge(ent.id, blocked, EdgeType.BLOCKS)
            for blocker in ent.blocked_by:
                _edge(blocker, ent.id, EdgeType.BLOCKS)
            for dep in ent.dependency_ids:
                _edge(ent.id, dep, EdgeType.DEPENDS_ON)
            if ent.owner_id:
                _set_type(graph, ent.owner_id, NodeType.PERSON)
                _edge(ent.owner_id, ent.id, EdgeType.OWNS)
            if ent.accountable_team_id:
                _set_type(graph, ent.accountable_team_id, NodeType.TEAM)
                _edge(ent.id, ent.accountable_team_id, EdgeType.BELONGS_TO)

        elif isinstance(ent, Dependency):
            _set_type(graph, ent.id, NodeType.DEPENDENCY, ent.title)
            for wi in ent.dependent_work_item_ids:
                _edge(ent.id, wi, EdgeType.BLOCKS)
            if ent.upstream_team_id:
                _set_type(graph, ent.upstream_team_id, NodeType.TEAM)
                _edge(ent.id, ent.upstream_team_id, EdgeType.DEPENDS_ON)
            if ent.downstream_team_id:
                _set_type(graph, ent.downstream_team_id, NodeType.TEAM)
                _edge(ent.downstream_team_id, ent.id, EdgeType.DEPENDS_ON)

        elif isinstance(ent, Milestone):
            _set_type(graph, ent.id, NodeType.MILESTONE, ent.name)
            for dm in ent.dependency_milestone_ids:
                _edge(ent.id, dm, EdgeType.DEPENDS_ON)

        elif isinstance(ent, Person):
            _set_type(graph, ent.id, NodeType.PERSON, ent.name)
            if ent.team_id:
                _set_type(graph, ent.team_id, NodeType.TEAM)
                _edge(ent.id, ent.team_id, EdgeType.BELONGS_TO)

        elif isinstance(ent, Team):
            _set_type(graph, ent.id, NodeType.TEAM, ent.name)

    return graph


def node_type_of(graph: nx.MultiDiGraph, node_id: str) -> NodeType | None:
    """Return the NodeType of a node, or None if unknown."""
    if node_id not in graph:
        return None
    return graph.nodes[node_id].get("node_type")
