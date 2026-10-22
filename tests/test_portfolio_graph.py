"""The cross-project topology graph."""

from __future__ import annotations

import pytest

from lltpm.portfolio import (
    PortfolioEdgeType,
    PortfolioNodeType,
    build_portfolio_graph,
    projects_sharing_node,
    shared_nodes,
)
from tests._portfolio_fixtures import (
    ACCOUNT_GRAPH,
    PLATFORM,
    T0,
    four_project_graph_inputs,
    platform_alias_registry,
    project_state,
)


def _graph(**overrides):
    inputs = four_project_graph_inputs()
    inputs.update(overrides)
    return build_portfolio_graph(**inputs)


class TestGraphConstruction:
    def test_projects_combine_into_one_graph(self):
        graph = _graph()
        assert graph.project_ids == [
            "project-a", "project-b", "project-c", "project-d",
        ]
        project_nodes = [
            n for n in graph.nodes
            if n.node_type == PortfolioNodeType.PROJECT
        ]
        assert len(project_nodes) == 4

    def test_workstreams_attach_to_their_projects(self):
        graph = _graph()
        workstream_nodes = [
            n for n in graph.nodes
            if n.node_type == PortfolioNodeType.WORKSTREAM
        ]
        assert len(workstream_nodes) == 4
        assert any(
            e.edge_type == PortfolioEdgeType.SUPPORTS
            and e.source_id == "project:project-a"
            for e in graph.edges
        )

    def test_kpi_edges_recorded(self):
        graph = _graph()
        kpi_edges = [
            e for e in graph.edges
            if e.edge_type == PortfolioEdgeType.AFFECTS_KPI
        ]
        assert kpi_edges

    def test_snapshot_is_a_projection_not_a_mutation(self):
        # The graph is built purely from the inputs; nothing is written
        # back into them.
        inputs = four_project_graph_inputs()
        before = [s.project_id for s in inputs["project_states"]]
        build_portfolio_graph(**inputs)
        after = [s.project_id for s in inputs["project_states"]]
        assert before == after

    def test_deterministic(self):
        assert _graph().model_dump() == _graph().model_dump()

    def test_fingerprint_changes_with_content(self):
        smaller = _graph(
            project_states=[project_state("project-a")],
            workstreams=[], approval_requirements={}, service_usage={},
        )
        assert smaller.state_fingerprint != _graph().state_fingerprint


class TestSharedResourceDetection:
    def test_shared_authority_detected(self):
        graph = _graph()
        authority = graph.node(f"authority:{PLATFORM}")
        assert authority is not None
        assert sorted(set(authority.project_ids)) == [
            "project-a", "project-b", "project-c",
        ]

    def test_shared_service_detected(self):
        graph = _graph()
        service = graph.node(f"service:{ACCOUNT_GRAPH}")
        assert service is not None
        assert sorted(set(service.project_ids)) == [
            "project-a", "project-c",
        ]

    def test_unshared_resource_is_not_a_collision(self):
        graph = _graph()
        partner = graph.node("service:Partner Gateway")
        assert partner is not None
        assert len(set(partner.project_ids)) == 1

    def test_shared_nodes_helper_filters_by_type(self):
        graph = _graph()
        authorities = shared_nodes(
            graph=graph, node_type=PortfolioNodeType.DECISION_AUTHORITY,
        )
        services = shared_nodes(
            graph=graph, node_type=PortfolioNodeType.SERVICE,
        )
        assert [n.label for n in authorities] == [PLATFORM]
        assert [n.label for n in services] == [ACCOUNT_GRAPH]

    def test_competition_edges_created_for_shared_resources(self):
        graph = _graph()
        competing = [
            e for e in graph.edges
            if e.edge_type == PortfolioEdgeType.COMPETES_FOR_CAPACITY
        ]
        competing_projects = {
            e.source_id for e in competing
            if e.target_id == f"authority:{PLATFORM}"
        }
        assert competing_projects == {
            "project:project-a", "project:project-b", "project:project-c",
        }

    def test_projects_sharing_node_helper(self):
        graph = _graph()
        assert projects_sharing_node(
            graph=graph, node_id=f"authority:{PLATFORM}",
        ) == ["project-a", "project-b", "project-c"]
        assert projects_sharing_node(graph=graph, node_id="nope") == []


class TestCanonicalIdentityResolution:
    def test_aliases_resolve_to_one_node(self):
        # Without identity resolution these read as three unrelated
        # authorities and the shared bottleneck stays invisible.
        graph = build_portfolio_graph(
            project_states=[
                project_state("project-a"), project_state("project-b"),
                project_state("project-c"),
            ],
            approval_requirements={
                "project-a": [PLATFORM],
                "project-b": ["platform-arch"],
                "project-c": ["PlatArch"],
            },
            identity_registry=platform_alias_registry(),
            as_of=T0,
        )
        authorities = [
            n for n in graph.nodes
            if n.node_type == PortfolioNodeType.DECISION_AUTHORITY
        ]
        assert len(authorities) == 1, (
            "aliases of one authority must collapse onto a single node"
        )
        assert sorted(set(authorities[0].project_ids)) == [
            "project-a", "project-b", "project-c",
        ]
        assert authorities[0].canonical_entity_id == (
            "authority_platform_architecture"
        )

    def test_without_a_registry_aliases_stay_separate(self):
        graph = build_portfolio_graph(
            project_states=[
                project_state("project-a"), project_state("project-b"),
            ],
            approval_requirements={
                "project-a": [PLATFORM], "project-b": ["platform-arch"],
            },
            as_of=T0,
        )
        authorities = [
            n for n in graph.nodes
            if n.node_type == PortfolioNodeType.DECISION_AUTHORITY
        ]
        # Honest behavior: unresolved references are not silently merged.
        assert len(authorities) == 2

    def test_inactive_canonical_entity_is_ignored(self):
        from types import SimpleNamespace

        from tests._portfolio_fixtures import FakeIdentityRegistry

        registry = FakeIdentityRegistry({
            "retired": SimpleNamespace(
                active=False, canonical_name=PLATFORM, aliases=[],
            ),
        })
        graph = build_portfolio_graph(
            project_states=[project_state("project-a")],
            approval_requirements={"project-a": [PLATFORM]},
            identity_registry=registry, as_of=T0,
        )
        authority = graph.node(f"authority:{PLATFORM}")
        assert authority is not None
        assert authority.canonical_entity_id is None


class TestActorIdentityIsNotAuthority:
    def test_identity_resolution_does_not_confer_authority(self):
        # Resolving who someone is says nothing about what they may
        # decide — the node type carries that, and it comes from the
        # approval requirement, not from the registry.
        graph = _graph(identity_registry=platform_alias_registry())
        authority = graph.node("authority_platform_architecture")
        assert authority is not None
        assert authority.node_type == PortfolioNodeType.DECISION_AUTHORITY
        # A canonical id is recorded, but it is not a grant of anything.
        assert authority.canonical_entity_id == (
            "authority_platform_architecture"
        )
        assert "may_approve" not in str(authority.metadata)
