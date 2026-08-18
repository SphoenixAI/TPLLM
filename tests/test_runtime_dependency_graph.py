"""Tests for Commit 30 — the intelligence dependency DAG.

A live system's recompute order is not a detail. If it were undefined, a
scenario could be built from a KPI that had not been refreshed yet, and
the resulting revision would be internally inconsistent while looking
perfectly coherent.
"""

from __future__ import annotations

import pytest

from lltpm.runtime import (
    INTELLIGENCE_DEPENDENCIES,
    DependencyCycleError,
    IntelligenceArtifactType as A,
    IntelligenceDependency,
    assert_acyclic,
    build_intelligence_dependency_graph,
    dependency_path,
    describe_dependency_edges,
    downstream_artifacts,
    find_dependency_cycles,
    topological_order,
    upstream_artifacts,
    validate_dependency_graph,
)


@pytest.fixture(scope="module")
def graph():
    return build_intelligence_dependency_graph()


class TestGraphShape:
    def test_the_declared_graph_is_acyclic(self, graph):
        assert find_dependency_cycles(graph) == []
        assert_acyclic(graph)

    def test_every_referenced_type_exists(self):
        validate_dependency_graph()

    def test_every_artifact_type_appears_as_a_node(self, graph):
        assert set(graph) == set(A)

    def test_evidence_is_the_root(self, graph):
        assert upstream_artifacts(artifact_type=A.EVIDENCE, graph=graph) == (
            set()
        )

    def test_the_spec_relationships_are_declared(self, graph):
        assert A.TEMPORAL_STATE in graph[A.EVIDENCE]
        assert A.PROJECT_UNDERSTANDING in graph[A.TEMPORAL_STATE]
        assert A.WORKSTREAM_STATE in graph[A.PROJECT_UNDERSTANDING]
        assert A.KPI_STATE in graph[A.PROJECT_UNDERSTANDING]
        assert A.LAUNCH_READINESS in graph[A.KPI_STATE]
        assert A.LAUNCH_READINESS in graph[A.WORKSTREAM_STATE]
        assert A.DECISION_STATE in graph[A.TEMPORAL_STATE]
        assert A.DECISION_CAPSULE in graph[A.DECISION_STATE]
        assert A.DECISION_RECOMMENDATION in graph[A.DECISION_CAPSULE]
        assert A.SCENARIO_LATTICE in graph[A.TEMPORAL_STATE]
        assert A.SCENARIO_LATTICE in graph[A.KPI_STATE]
        assert A.SCENARIO_LATTICE in graph[A.WORKSTREAM_STATE]
        assert A.SCENARIO_DECISION_RECOMMENDATION in graph[A.SCENARIO_LATTICE]
        assert A.SCENARIO_DECISION_RECOMMENDATION in (
            graph[A.DECISION_RECOMMENDATION]
        )
        assert A.PORTFOLIO_SCENARIO in graph[A.PORTFOLIO_BOTTLENECKS]

    def test_conditional_edges_state_their_condition(self):
        conditional = [
            d for d in INTELLIGENCE_DEPENDENCIES if d.condition is not None
        ]
        assert conditional
        for dependency in conditional:
            assert dependency.condition


class TestCycleDetection:
    def test_a_synthetic_cycle_is_found(self):
        synthetic = {
            A.KPI_STATE: {A.LAUNCH_READINESS},
            A.LAUNCH_READINESS: {A.KPI_STATE},
        }
        assert find_dependency_cycles(synthetic) == [
            ("kpi_state", "launch_readiness"),
        ]
        with pytest.raises(DependencyCycleError, match="cycle"):
            assert_acyclic(synthetic)

    def test_a_dag_passes(self):
        dag = {A.KPI_STATE: {A.LAUNCH_READINESS}, A.LAUNCH_READINESS: set()}
        assert find_dependency_cycles(dag) == []
        assert_acyclic(dag)

    def test_ordering_a_cyclic_selection_raises(self):
        cyclic = {
            A.KPI_STATE: {A.LAUNCH_READINESS},
            A.LAUNCH_READINESS: {A.KPI_STATE},
        }
        with pytest.raises(DependencyCycleError):
            topological_order(graph=cyclic)


class TestInvalidationPropagation:
    def test_a_kpi_change_reaches_the_scenario_recommendation(self, graph):
        affected = downstream_artifacts(
            artifact_types={A.KPI_STATE}, graph=graph,
        )
        assert A.LAUNCH_READINESS in affected
        assert A.SCENARIO_LATTICE in affected
        assert A.SCENARIO_DECISION_RECOMMENDATION in affected

    def test_a_kpi_change_does_not_reach_identity(self, graph):
        affected = downstream_artifacts(
            artifact_types={A.KPI_STATE}, graph=graph,
        )
        assert A.IDENTITY not in affected
        assert A.EVIDENCE not in affected

    def test_the_source_is_included_by_default(self, graph):
        assert A.KPI_STATE in downstream_artifacts(
            artifact_types={A.KPI_STATE}, graph=graph,
        )

    def test_the_source_can_be_excluded(self, graph):
        assert A.KPI_STATE not in downstream_artifacts(
            artifact_types={A.KPI_STATE}, graph=graph, include_self=False,
        )

    def test_a_leaf_invalidates_nothing_further(self, graph):
        assert downstream_artifacts(
            artifact_types={A.SCENARIO_DECISION_RECOMMENDATION},
            graph=graph, include_self=False,
        ) == set()

    def test_upstream_explains_why_readiness_refreshed(self, graph):
        assert upstream_artifacts(
            artifact_type=A.LAUNCH_READINESS, graph=graph,
        ) == {A.KPI_STATE, A.WORKSTREAM_STATE}


class TestTopologicalOrder:
    def test_dependencies_precede_dependents(self, graph):
        order = topological_order(graph=graph)
        positions = {artifact: i for i, artifact in enumerate(order)}
        for dependency in INTELLIGENCE_DEPENDENCIES:
            assert positions[dependency.source] < positions[dependency.target]

    def test_order_is_deterministic(self, graph):
        assert topological_order(graph=graph) == topological_order(graph=graph)

    def test_a_subset_orders_correctly(self, graph):
        order = topological_order(
            artifact_types={
                A.SCENARIO_LATTICE, A.KPI_STATE, A.LAUNCH_READINESS,
            },
            graph=graph,
        )
        assert order.index(A.KPI_STATE) < order.index(A.LAUNCH_READINESS)
        assert order.index(A.LAUNCH_READINESS) < order.index(
            A.SCENARIO_LATTICE
        )

    def test_a_subset_contains_only_what_was_asked_for(self, graph):
        assert set(
            topological_order(artifact_types={A.KPI_STATE}, graph=graph)
        ) == {A.KPI_STATE}

    def test_an_empty_selection_orders_to_nothing(self, graph):
        assert topological_order(artifact_types=set(), graph=graph) == []

    def test_every_artifact_type_is_orderable(self, graph):
        assert len(topological_order(graph=graph)) == len(list(A))


class TestExplanation:
    def test_a_path_is_found_from_kpi_to_scenario(self, graph):
        path = dependency_path(
            source=A.KPI_STATE,
            target=A.SCENARIO_DECISION_RECOMMENDATION,
            graph=graph,
        )
        assert path[0] == A.KPI_STATE
        assert path[-1] == A.SCENARIO_DECISION_RECOMMENDATION

    def test_no_path_upward(self, graph):
        assert dependency_path(
            source=A.SCENARIO_LATTICE, target=A.EVIDENCE, graph=graph,
        ) == []

    def test_edges_render_as_readable_propagation(self, graph):
        edges = describe_dependency_edges(
            source=A.KPI_STATE, target=A.LAUNCH_READINESS, graph=graph,
        )
        assert edges == ["kpi_state -> launch_readiness"]

    def test_an_unreachable_target_describes_no_edges(self, graph):
        assert describe_dependency_edges(
            source=A.LAUNCH_READINESS, target=A.IDENTITY, graph=graph,
        ) == []


class TestDependencyModel:
    def test_a_dependency_records_its_direction(self):
        dependency = IntelligenceDependency(
            source=A.KPI_STATE, target=A.LAUNCH_READINESS,
        )
        assert dependency.source == A.KPI_STATE
        assert dependency.target == A.LAUNCH_READINESS
        assert dependency.condition is None
