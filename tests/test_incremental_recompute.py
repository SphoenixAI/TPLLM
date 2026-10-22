"""Scoped, coalesced, incremental recomputation.

The failure mode under test throughout: a single event triggering a
full-organization rebuild, or one shared incident triggering fourteen
independent refresh chains. Correct, unusable, and expensive.
"""

from __future__ import annotations

import pytest

from tpllm.runtime import (
    DIRECT_INVALIDATIONS,
    ArtifactRecomputeError,
    ArtifactRecomputeRequest,
    IntelligenceArtifactType as A,
    RecomputeScope,
    RuntimeFanoutPolicy,
    apply_fanout_policy,
    build_event_scope,
    build_recompute_batch,
    coalesced_request_count,
    determine_direct_invalidations,
    execute_recompute_batch,
    merge_requests,
)
from tests._runtime_fixtures import (
    ALL_PROJECTS,
    PLATFORM,
    PROJECT_A,
    PROJECT_B,
    PROJECT_C,
    PROJECT_D,
    FakeIntelligenceServices,
    at,
    event,
    topology,
)


def _batch(events, **kwargs):
    return build_recompute_batch(
        events=events,
        revision_id=kwargs.pop("revision_id", "revision-1"),
        created_at=kwargs.pop("created_at", at(0)),
        topology=kwargs.pop("topology", topology()),
        **kwargs,
    )


def _kpi_event(**overrides):
    defaults = dict(
        envelope_id="env-kpi", event_class="kpi_observation",
        project_ids=[PROJECT_A], kpi_ids=["kpi-activation-rate"],
    )
    defaults.update(overrides)
    return event(**defaults)


def _capacity_event(**overrides):
    defaults = dict(
        envelope_id="env-capacity", event_class="capacity_changed",
        project_ids=[PROJECT_A], shared_resource_ids=[PLATFORM],
        causal_event_id="causal-platform",
    )
    defaults.update(overrides)
    return event(**defaults)


class TestDirectInvalidation:
    def test_a_kpi_observation_invalidates_the_kpi(self):
        assert determine_direct_invalidations(event=_kpi_event()) == {
            A.KPI_STATE
        }

    def test_a_conclusive_decision_invalidates_the_decision_chain(self):
        assert determine_direct_invalidations(
            event=event(
                envelope_id="e", event_class="decision_conclusive",
            )
        ) == {A.TEMPORAL_STATE, A.DECISION_STATE, A.WORKSTREAM_STATE}

    def test_architecture_evidence_invalidates_understanding(self):
        invalidated = determine_direct_invalidations(
            event=event(
                envelope_id="e", event_class="architecture_evidence",
            )
        )
        assert invalidated == {
            A.PROJECT_UNDERSTANDING, A.WORKSTREAM_STATE, A.SCENARIO_LATTICE,
        }

    def test_a_duplicate_message_invalidates_nothing(self):
        assert determine_direct_invalidations(
            event=event(envelope_id="e", event_class="duplicate_message")
        ) == set()

    def test_supporting_evidence_for_a_known_fact_invalidates_nothing(self):
        assert determine_direct_invalidations(
            event=event(envelope_id="e", event_class="supporting_evidence")
        ) == set()

    def test_an_unknown_event_class_invalidates_nothing(self):
        assert determine_direct_invalidations(
            event=event(envelope_id="e", event_class="brand_new_webhook")
        ) == set(), (
            "guessing would mean a system that rebuilds itself whenever a "
            "tool adds a webhook type"
        )

    def test_the_declared_map_covers_the_spec_examples(self):
        for event_class in (
            "kpi_observation", "decision_conclusive", "architecture_evidence",
            "duplicate_message", "shared_service_incident",
        ):
            assert event_class in DIRECT_INVALIDATIONS


class TestScope:
    def test_a_local_kpi_event_stays_local(self):
        scope = build_event_scope(event=_kpi_event(), topology=topology())
        assert scope.project_ids == {PROJECT_A}
        assert scope.portfolio_refresh_required is False

    def test_a_shared_resource_event_reaches_every_dependent_project(self):
        scope = build_event_scope(event=_capacity_event(), topology=topology())
        assert scope.project_ids == {PROJECT_A, PROJECT_B, PROJECT_C}
        assert PROJECT_D not in scope.project_ids

    def test_a_shared_resource_event_requires_a_portfolio_refresh(self):
        assert build_event_scope(
            event=_capacity_event(), topology=topology(),
        ).portfolio_refresh_required is True

    def test_without_topology_an_event_reaches_only_its_own_projects(self):
        scope = build_event_scope(event=_capacity_event())
        assert scope.project_ids == {PROJECT_A}


class TestCoalescing:
    def test_two_paths_to_readiness_produce_one_request(self):
        batch = _batch([
            _kpi_event(),
            event(
                envelope_id="env-blocker", event_class="blocker_resolved",
                project_ids=[PROJECT_A], workstream_ids=["ws-a"],
            ),
        ])
        assert len(batch.request_for(A.LAUNCH_READINESS)) == 1, (
            "readiness is one artifact; it is computed once per revision"
        )

    def test_the_scenario_is_also_computed_once(self):
        batch = _batch([
            _kpi_event(),
            event(
                envelope_id="env-blocker", event_class="blocker_resolved",
                project_ids=[PROJECT_A],
            ),
        ])
        assert len(batch.request_for(A.SCENARIO_LATTICE)) == 1

    def test_merged_scopes_take_the_union(self):
        batch = _batch([
            _kpi_event(),
            _kpi_event(envelope_id="env-kpi-c", project_ids=[PROJECT_C]),
        ])
        assert batch.request_for(A.KPI_STATE)[0].scope.project_ids == {
            PROJECT_A, PROJECT_C,
        }

    def test_merging_keeps_every_reason_and_source(self):
        merged = merge_requests([
            ArtifactRecomputeRequest(
                artifact_type=A.KPI_STATE,
                scope=RecomputeScope(project_ids={PROJECT_A}),
                reasons=["kpi observed"], source_event_ids=["e1"],
                causal_event_ids=["c1"],
            ),
            ArtifactRecomputeRequest(
                artifact_type=A.KPI_STATE,
                scope=RecomputeScope(project_ids={PROJECT_C}),
                reasons=["kpi restated"], source_event_ids=["e2"],
                causal_event_ids=["c2"],
            ),
        ])
        assert len(merged) == 1
        assert merged[0].reasons == ["kpi observed", "kpi restated"]
        assert merged[0].source_event_ids == ["e1", "e2"]
        assert merged[0].causal_event_ids == ["c1", "c2"]

    def test_coalescing_is_counted(self):
        raw = [
            ArtifactRecomputeRequest(
                artifact_type=A.KPI_STATE, scope=RecomputeScope(),
            )
            for _ in range(3)
        ]
        assert coalesced_request_count(raw, merge_requests(raw)) == 2

    def test_batch_order_is_deterministic(self):
        first = _batch([_kpi_event()]).topological_order
        second = _batch([_kpi_event()]).topological_order
        assert first == second

    def test_every_requested_artifact_appears_in_the_order(self):
        batch = _batch([_kpi_event()])
        assert {r.artifact_type for r in batch.requests} <= set(
            batch.topological_order
        )


class TestPortfolioIsRefreshedOnlyWhenNeeded:
    def test_a_local_kpi_event_does_not_rebuild_the_portfolio(self):
        batch = _batch([_kpi_event()])
        assert batch.request_for(A.PORTFOLIO_GRAPH) == []
        assert batch.request_for(A.PORTFOLIO_SCENARIO) == []

    def test_a_shared_capacity_event_does(self):
        batch = _batch([_capacity_event()])
        assert batch.request_for(A.PORTFOLIO_GRAPH)
        assert batch.request_for(A.PORTFOLIO_RECOMMENDATION)

    def test_a_shared_service_incident_reaches_every_connected_project(self):
        batch = _batch([
            event(
                envelope_id="env-incident",
                event_class="shared_service_incident",
                project_ids=[PROJECT_A],
                shared_resource_ids=["Account Graph"],
            )
        ])
        assert batch.affected_project_ids == {PROJECT_A, PROJECT_C}
        assert PROJECT_B not in batch.affected_project_ids


class TestUnrelatedProjectsAreUntouched:
    def test_one_projects_kpi_does_not_touch_another(self):
        batch = _batch([_kpi_event()])
        assert batch.affected_project_ids == {PROJECT_A}

    def test_the_services_never_see_the_unrelated_project(self):
        services = FakeIntelligenceServices()
        execute_recompute_batch(
            batch=_batch([_kpi_event()]), runtime_services=services,
        )
        assert PROJECT_D not in services.projects_seen()
        assert PROJECT_B not in services.projects_seen()


class TestFanout:
    def test_a_wide_blast_radius_fits_when_the_budget_allows(self):
        immediate, deferred = apply_fanout_policy(
            project_ids=set(ALL_PROJECTS), policy=RuntimeFanoutPolicy(),
        )
        assert immediate == sorted(ALL_PROJECTS)
        assert deferred == []

    def test_exceeding_the_budget_defers_rather_than_drops(self):
        immediate, deferred = apply_fanout_policy(
            project_ids=set(ALL_PROJECTS),
            policy=RuntimeFanoutPolicy(
                maximum_projects_per_immediate_batch=2,
            ),
        )
        assert len(immediate) == 2
        assert len(deferred) == 2
        assert set(immediate) | set(deferred) == set(ALL_PROJECTS), (
            "no project may silently disappear from scope"
        )

    def test_high_priority_projects_go_first(self):
        immediate, _ = apply_fanout_policy(
            project_ids=set(ALL_PROJECTS),
            policy=RuntimeFanoutPolicy(
                maximum_projects_per_immediate_batch=1,
            ),
            high_priority_project_ids=[PROJECT_C],
        )
        assert immediate == [PROJECT_C]

    def test_tiering_can_be_turned_off(self):
        immediate, _ = apply_fanout_policy(
            project_ids=set(ALL_PROJECTS),
            policy=RuntimeFanoutPolicy(
                maximum_projects_per_immediate_batch=1,
                allow_priority_tiering=False,
            ),
            high_priority_project_ids=[PROJECT_C],
        )
        assert immediate == [PROJECT_A]

    def test_the_batch_reports_deferred_scope_explicitly(self):
        batch = _batch(
            [_capacity_event()],
            fanout_policy=RuntimeFanoutPolicy(
                maximum_projects_per_immediate_batch=1,
            ),
        )
        assert batch.deferred_project_ids
        assert "deferred to a later pass" in batch.deferral_reason
        assert "dropped" in batch.deferral_reason

    def test_deferred_projects_leave_the_immediate_scopes(self):
        batch = _batch(
            [_capacity_event()],
            fanout_policy=RuntimeFanoutPolicy(
                maximum_projects_per_immediate_batch=1,
            ),
        )
        for request in batch.requests:
            assert request.scope.project_ids <= batch.affected_project_ids


class TestExecution:
    def test_artifacts_run_in_dependency_order(self):
        services = FakeIntelligenceServices()
        execute_recompute_batch(
            batch=_batch([_kpi_event()]), runtime_services=services,
        )
        called = services.called_types()
        assert called.index(A.KPI_STATE) < called.index(A.LAUNCH_READINESS)
        assert called.index(A.LAUNCH_READINESS) < called.index(
            A.SCENARIO_LATTICE
        )

    def test_every_artifact_carries_the_revision(self):
        execution = execute_recompute_batch(
            batch=_batch([_kpi_event()], revision_id="revision-7"),
            runtime_services=FakeIntelligenceServices(),
        )
        assert all(a.revision_id == "revision-7" for a in execution.artifacts)

    def test_an_unchanged_fingerprint_skips_the_work(self):
        batch = _batch([_kpi_event()])
        request = batch.request_for(A.KPI_STATE)[0]
        stamped = batch.model_copy(update={
            "requests": [
                r.model_copy(update={"input_fingerprint": "same"})
                if r.artifact_type == A.KPI_STATE else r
                for r in batch.requests
            ],
        })
        services = FakeIntelligenceServices()
        execution = execute_recompute_batch(
            batch=stamped,
            runtime_services=services,
            known_fingerprints={request.request_key(): "same"},
        )
        assert A.KPI_STATE not in services.called_types()
        assert any(r.artifact_type == A.KPI_STATE for r in execution.skipped)

    def test_skipping_can_be_disabled(self):
        batch = _batch([_kpi_event()])
        request = batch.request_for(A.KPI_STATE)[0]
        stamped = batch.model_copy(update={
            "requests": [
                r.model_copy(update={"input_fingerprint": "same"})
                for r in batch.requests
            ],
        })
        services = FakeIntelligenceServices()
        execute_recompute_batch(
            batch=stamped, runtime_services=services,
            known_fingerprints={request.request_key(): "same"},
            skip_unchanged=False,
        )
        assert A.KPI_STATE in services.called_types()

    def test_a_failure_is_recorded_not_swallowed(self):
        execution = execute_recompute_batch(
            batch=_batch([_kpi_event()]),
            runtime_services=FakeIntelligenceServices(failures={
                A.SCENARIO_LATTICE: ArtifactRecomputeError(
                    "scenario generation timed out",
                    error_class="TimeoutError", retryable=True,
                ),
            }),
        )
        assert execution.failures
        failure = execution.failures[0]
        assert failure.artifact_type == A.SCENARIO_LATTICE
        assert failure.retryable is True
        assert "timed out" in failure.explanation

    def test_downstream_of_a_failure_is_skipped_not_computed(self):
        services = FakeIntelligenceServices(failures={
            A.SCENARIO_LATTICE: ArtifactRecomputeError("boom"),
        })
        execute_recompute_batch(
            batch=_batch([_kpi_event()]), runtime_services=services,
        )
        assert A.SCENARIO_DECISION_RECOMMENDATION not in (
            services.called_types()
        ), (
            "a recommendation derived from a scenario that did not build "
            "would be worse than no recommendation"
        )

    def test_an_unexpected_exception_is_still_recorded(self):
        class Exploding:
            def recompute_artifact(self, *, request, revision_id):
                raise RuntimeError("unexpected")

        execution = execute_recompute_batch(
            batch=_batch([_kpi_event()]), runtime_services=Exploding(),
        )
        assert execution.failures
        assert execution.failures[0].error_class == "RuntimeError"

    def test_a_clean_run_reports_success(self):
        execution = execute_recompute_batch(
            batch=_batch([_kpi_event()]),
            runtime_services=FakeIntelligenceServices(),
        )
        assert execution.succeeded is True
        assert execution.safety_critical_failed is False

    def test_a_scenario_failure_is_safety_critical(self):
        execution = execute_recompute_batch(
            batch=_batch([_kpi_event()]),
            runtime_services=FakeIntelligenceServices(failures={
                A.SCENARIO_LATTICE: ArtifactRecomputeError("boom"),
            }),
        )
        assert execution.safety_critical_failed is True
        assert A.SCENARIO_LATTICE in execution.failed_artifact_types

    def test_artifacts_are_retrievable_by_type(self):
        execution = execute_recompute_batch(
            batch=_batch([_kpi_event()]),
            runtime_services=FakeIntelligenceServices(),
        )
        assert execution.artifact_of(A.KPI_STATE) is not None
        assert execution.artifact_of(A.PORTFOLIO_GRAPH) is None


class TestEmptyBatch:
    def test_a_batch_from_noise_has_no_requests(self):
        batch = _batch([
            event(envelope_id="e1", event_class="chat_message"),
            event(envelope_id="e2", event_class="duplicate_message"),
        ])
        assert batch.requests == []
        assert batch.topological_order == []

    def test_executing_nothing_succeeds(self):
        execution = execute_recompute_batch(
            batch=_batch([event(envelope_id="e", event_class="chat_message")]),
            runtime_services=FakeIntelligenceServices(),
        )
        assert execution.artifacts == []
        assert execution.succeeded is True
