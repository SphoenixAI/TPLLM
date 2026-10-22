"""Identity graph read model and point-in-time
resolution."""

from __future__ import annotations

from datetime import timedelta

from lltpm.identity import (
    CanonicalEntityType,
    EntityResolutionStatus,
    IdentityGraphEdgeType,
    IdentityGraphSnapshot,
    build_identity_graph_snapshot,
    build_represents_edge,
    resolve_external_identity,
    resolve_identity_as_of,
)
from tests._identity_fixtures import (
    AS_OF,
    POLICY,
    entity,
    identity,
    priya_registry,
)


def _resolved_result():
    registry = priya_registry()
    slack = identity(display_value="Priya Rao",
                     email="priya.rao@company.com")
    return slack, resolve_external_identity(
        identity=slack,
        canonical_entities=list(registry.entities.values()),
        existing_identities=list(registry.external_identities.values()),
        policy=POLICY,
    )


class TestIdentityGraph:
    def test_resolved_identity_gets_identifies_edge(self):
        slack, result = _resolved_result()
        registry = priya_registry()
        snapshot = build_identity_graph_snapshot(
            canonical_entities=list(registry.entities.values()),
            external_identities=[slack],
            resolution_results=[result],
            as_of=AS_OF,
        )
        assert len(snapshot.edges) == 1
        edge = snapshot.edges[0]
        assert edge.edge_type == IdentityGraphEdgeType.IDENTIFIES
        assert edge.source_id == slack.id
        assert edge.target_id == "person_priya_rao"
        assert edge.evidence_ids  # traceable

    def test_ambiguous_identity_has_no_edge_but_remains_visible(self):
        ambiguous = identity(external_id="U777", display_value="Alex Chen")
        result = resolve_external_identity(
            identity=ambiguous,
            canonical_entities=[
                entity("a", name="Alex Chen"), entity("b", name="Alex Chen"),
            ],
            existing_identities=[],
            policy=POLICY,
        )
        assert result.status == EntityResolutionStatus.AMBIGUOUS
        snapshot = build_identity_graph_snapshot(
            canonical_entities=[
                entity("a", name="Alex Chen"), entity("b", name="Alex Chen"),
            ],
            external_identities=[ambiguous],
            resolution_results=[result],
            as_of=AS_OF,
        )
        assert snapshot.edges == []  # visibly unresolved, never guessed
        assert any(i.id == ambiguous.id for i in snapshot.external_identities)

    def test_repository_represents_service(self):
        repo = entity("repo_account_graph", CanonicalEntityType.REPOSITORY,
                      "account-graph")
        service = entity("service_account_graph", CanonicalEntityType.SERVICE,
                         "Account Graph Production Service")
        edge = build_represents_edge(
            source_entity_id=repo.id,
            target_entity_id=service.id,
            confidence=0.9,
            evidence_ids=["ev-deploy-config"],
        )
        snapshot = build_identity_graph_snapshot(
            canonical_entities=[repo, service],
            external_identities=[],
            resolution_results=[],
            as_of=AS_OF,
            extra_edges=[edge],
        )
        # Distinct entities of distinct types, related — never one.
        assert repo.entity_type != service.entity_type
        assert snapshot.edges[0].edge_type == IdentityGraphEdgeType.REPRESENTS

    def test_snapshot_deterministic(self):
        slack, result = _resolved_result()
        registry = priya_registry()
        kwargs = dict(
            canonical_entities=list(registry.entities.values()),
            external_identities=[slack],
            resolution_results=[result],
            as_of=AS_OF,
        )
        assert build_identity_graph_snapshot(**kwargs).model_dump() == (
            build_identity_graph_snapshot(**kwargs).model_dump()
        )


class TestPointInTimeResolution:
    def test_resolution_respects_snapshot_history(self):
        registry = priya_registry()
        empty_snapshot = IdentityGraphSnapshot(
            id="snap-early",
            canonical_entities=[],
            external_identities=[],
            as_of=AS_OF - timedelta(days=30),
        )
        full_snapshot = IdentityGraphSnapshot(
            id="snap-late",
            canonical_entities=list(registry.entities.values()),
            external_identities=list(
                registry.external_identities.values()
            ),
            as_of=AS_OF - timedelta(days=1),
        )
        slack = identity(display_value="Priya Rao",
                         email="priya.rao@company.com")
        history = [empty_snapshot, full_snapshot]

        early = resolve_identity_as_of(
            identity=slack, registry_history=history,
            as_of=AS_OF - timedelta(days=10),
        )
        assert early.status == EntityResolutionStatus.UNRESOLVED

        late = resolve_identity_as_of(
            identity=slack, registry_history=history, as_of=AS_OF,
        )
        assert late.status == EntityResolutionStatus.RESOLVED
        assert late.canonical_entity_id == "person_priya_rao"
