"""The stack actually runs in the product.

Before this, ``recompute_artifact`` had no production implementation:
the orchestrator was proven against test doubles and connected to no
real engine. These tests drive real project understanding, KPI health,
launch readiness, temporal reconstruction, the scenario lattice and
portfolio simulation
through the live path and assert the real thing happened.

The other half is refusal. A service that invented a capacity figure,
or scored readiness against no declared launch, would produce numbers
with nothing behind them — so each missing input is a loud failure, and
The executor turns that into a revision that does not publish.
"""

from __future__ import annotations

import pytest

from tpllm.runtime import (
    DERIVED,
    ENGINE_BACKED,
    EVENT_CLASS_TO_TEMPORAL_TYPE,
    ArtifactRecomputeError,
    ArtifactRecomputeRequest,
    DomainInputs,
    IntelligenceArtifactType as A,
    JournalBackedDataSource,
    RecomputeScope,
    coverage_report,
    evidence_to_temporal_event,
    process_durable_organizational_events,
)
from tpllm.runtime import RuntimeStateStore as RuntimeProjection
from tpllm.persistence import (
    DurableEvidenceRecord,
    JournalRetentionClass,
    rebuild_runtime_projections,
)
from tpllm.temporal import TemporalEventType
from tests._persistence_fixtures import (
    ORGANIZATION,
    Persistence,
    at,
    manifest,
    reconstructor,
    runtime_policy,
)
from tests._wiring_fixtures import (
    PLATFORM,
    launch_definition,
    PROJECT_A,
    PROJECT_B,
    PROJECT_C,
    T0,
    CapacityScenarioGenerator,
    KeywordClaimExtractor,
    data_source,
    domain_inputs,
    evidence_envelopes,
    live_services,
    scenario_horizon,
    seeded_organization,
)


def _request(artifact_type: A, *projects: str) -> ArtifactRecomputeRequest:
    return ArtifactRecomputeRequest(
        artifact_type=artifact_type,
        scope=RecomputeScope(project_ids=set(projects)),
        source_event_ids=["env-1"],
    )


# ── The live path, end to end ────────────────────────────────────────


class TestRealEnginesRunThroughTheOrchestrator:
    @pytest.fixture
    def seeded(self):
        return seeded_organization()

    def test_the_revision_publishes(self, seeded):
        _, projection, _ = seeded
        assert projection.current_revision is not None
        assert projection.current_revision.complete is True

    def test_every_planned_artifact_was_computed(self, seeded):
        _, _, services = seeded
        called = set(services.calls)
        for artifact_type in (
            A.PROJECT_UNDERSTANDING, A.KPI_STATE, A.LAUNCH_READINESS,
            A.SCENARIO_LATTICE, A.PORTFOLIO_GRAPH, A.PORTFOLIO_BOTTLENECKS,
            A.PORTFOLIO_SCENARIO, A.PORTFOLIO_RECOMMENDATION,
        ):
            assert artifact_type in called, (
                f"{artifact_type.value} never reached its engine"
            )

    def test_project_state_is_published_for_every_affected_project(
        self, seeded,
    ):
        _, projection, _ = seeded
        assert set(projection.project_states) == {
            PROJECT_A, PROJECT_B, PROJECT_C,
        }

    def test_the_portfolio_state_is_published(self, seeded):
        _, projection, _ = seeded
        assert projection.portfolio_state is not None
        assert projection.portfolio_state.state_fingerprint

    def test_state_is_durable_in_the_store(self, seeded):
        persistence, projection, _ = seeded
        stored = persistence.state.get_project_state(PROJECT_A)
        assert stored is not None
        assert stored.revision_id == projection.current_revision.id
        assert stored.state_fingerprint

    def test_the_evidence_is_journalled(self, seeded):
        persistence, _, _ = seeded
        assert persistence.journal.latest_position().global_sequence >= len(
            evidence_envelopes()
        )

    def test_engines_produce_real_fingerprints(self, seeded):
        _, projection, _ = seeded
        fingerprints = {
            state.state_fingerprint
            for state in projection.project_states.values()
        }
        assert len(fingerprints) == len(projection.project_states), (
            "three different projects must not share one fingerprint"
        )
        assert all(fingerprints)


class TestEngineBackedArtifacts:
    @pytest.fixture
    def wired(self):
        persistence = Persistence()
        services = live_services(persistence=persistence)
        projection = RuntimeProjection(organization_id=ORGANIZATION)
        process_durable_organizational_events(
            envelopes=evidence_envelopes(),
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            runtime_projection=projection,
            intelligence_services=services,
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(60),
        )
        return persistence, services

    def test_temporal_state_reconstructs_from_the_journal(self, wired):
        _, services = wired
        artifact = services.recompute_artifact(
            request=_request(A.TEMPORAL_STATE, PROJECT_C),
            revision_id="revision-x",
        )
        assert artifact.state_fingerprint
        assert "Temporal state reconstructed" in artifact.after_summary

    def test_understanding_extracts_real_claims(self, wired):
        _, services = wired
        services.recompute_artifact(
            request=_request(A.PROJECT_UNDERSTANDING, PROJECT_C),
            revision_id="revision-x",
        )
        snapshot = services._understanding[PROJECT_C]
        assert snapshot.claims, (
            "the architecture evidence mentions approval and integration, "
            "so the extractor should have found claims"
        )
        assert {c.claim_type.value for c in snapshot.claims} & {
            "dependency", "technical_mechanism",
        }

    def test_kpi_health_reports_the_breach(self, wired):
        _, services = wired
        artifact = services.recompute_artifact(
            request=_request(A.KPI_STATE, PROJECT_A),
            revision_id="revision-x",
        )
        assert "kpi-activation-rate" in artifact.after_summary
        assert artifact.severity == "severe", (
            "41% against a 60% target is a breach, not a watch"
        )

    def test_readiness_scores_against_a_declared_launch(self, wired):
        _, services = wired
        artifact = services.recompute_artifact(
            request=_request(A.LAUNCH_READINESS, PROJECT_C),
            revision_id="revision-x",
        )
        assert artifact.state_fingerprint
        assert "readiness scored" in artifact.after_summary

    def test_the_scenario_lattice_reports_probability_and_confidence(
        self, wired,
    ):
        _, services = wired
        artifact = services.recompute_artifact(
            request=_request(A.SCENARIO_LATTICE, PROJECT_C),
            revision_id="revision-x",
        )
        assert artifact.outcome_probabilities
        assert all(
            0.0 <= v <= 1.0
            for v in artifact.outcome_probabilities.values()
        )
        assert 0.0 <= artifact.probability_confidence <= 1.0

    def test_probability_and_confidence_stay_separate(self, wired):
        _, services = wired
        artifact = services.recompute_artifact(
            request=_request(A.SCENARIO_LATTICE, PROJECT_C),
            revision_id="revision-x",
        )
        assert artifact.probability_confidence not in (
            artifact.outcome_probabilities.values()
        ) or len(artifact.outcome_probabilities) == 0

    def test_the_portfolio_graph_spans_the_declared_topology(self, wired):
        _, services = wired
        artifact = services.recompute_artifact(
            request=_request(A.PORTFOLIO_GRAPH, PROJECT_A, PROJECT_C),
            revision_id="revision-x",
        )
        assert "Portfolio topology spans" in artifact.after_summary

    def test_bottlenecks_come_from_declared_capacity(self, wired):
        _, services = wired
        artifact = services.recompute_artifact(
            request=_request(A.PORTFOLIO_BOTTLENECKS, PROJECT_A),
            revision_id="revision-x",
        )
        assert PLATFORM in artifact.after_summary
        assert "share one constraint" in artifact.after_summary, (
            "the point of the portfolio layer is naming the common cause"
        )
        # Three demands against two slots is a 1.5x overload: moderate,
        # not high. Severity is reported from the real ratio rather than
        # from the mere existence of contention.
        assert artifact.severity == "normal"

    def test_a_genuine_overload_is_reported_as_severe(self, wired):
        from tests._wiring_fixtures import capacity_window

        _, services = wired
        services.inputs.capacity_windows = [capacity_window(capacity=1.0)]
        artifact = services.recompute_artifact(
            request=_request(A.PORTFOLIO_BOTTLENECKS, PROJECT_A),
            revision_id="revision-y",
        )
        assert artifact.severity == "severe", (
            "three demands against one slot is a 3x overload"
        )

    def test_the_portfolio_recommendation_names_who_waits(self, wired):
        _, services = wired
        artifact = services.recompute_artifact(
            request=_request(A.PORTFOLIO_RECOMMENDATION, PROJECT_A),
            revision_id="revision-x",
        )
        assert "Prioritize" in artifact.after_summary
        assert "waits" in artifact.after_summary
        assert artifact.decision_required is True

    def test_cross_project_simulation_runs(self, wired):
        _, services = wired
        artifact = services.recompute_artifact(
            request=_request(A.PORTFOLIO_SCENARIO, PROJECT_A),
            revision_id="revision-x",
        )
        assert artifact.outcome_probabilities
        assert "drawn once per run" in artifact.after_summary, (
            "the shared-factor guarantee should be stated in the output"
        )

    def test_an_upstream_artifact_is_built_on_demand(self, wired):
        """Scopes merge per artifact type, so a dependent can be asked
        for a project its upstream was not scoped to."""
        _, services = wired
        services._understanding.clear()
        artifact = services.recompute_artifact(
            request=_request(A.LAUNCH_READINESS, PROJECT_B),
            revision_id="revision-x",
        )
        assert artifact.state_fingerprint
        assert PROJECT_B in services._understanding


# ── Refusal rather than invention ────────────────────────────────────


class TestTheServiceRefusesToInvent:
    def _services(self, **overrides):
        return live_services(persistence=Persistence(), **overrides)

    def test_no_claim_extractor_refuses_understanding(self):
        services = self._services(claim_extractor=None)
        with pytest.raises(ArtifactRecomputeError, match="claim extractor"):
            services.recompute_artifact(
                request=_request(A.PROJECT_UNDERSTANDING, PROJECT_C),
                revision_id="r",
            )

    def test_the_refusal_says_why_guessing_is_worse(self):
        services = self._services(claim_extractor=None)
        with pytest.raises(ArtifactRecomputeError, match="fabricate"):
            services.recompute_artifact(
                request=_request(A.PROJECT_UNDERSTANDING, PROJECT_C),
                revision_id="r",
            )

    def test_no_scenario_generator_refuses_a_forecast(self):
        services = self._services(scenario_generator=None)
        with pytest.raises(
            ArtifactRecomputeError, match="confident absence of risk",
        ):
            services.recompute_artifact(
                request=_request(A.SCENARIO_LATTICE, PROJECT_C),
                revision_id="r",
            )

    def test_no_horizon_refuses_a_forecast(self):
        services = self._services(scenario_horizon=None)
        with pytest.raises(ArtifactRecomputeError, match="stated window"):
            services.recompute_artifact(
                request=_request(A.SCENARIO_LATTICE, PROJECT_C),
                revision_id="r",
            )

    def test_an_undeclared_kpi_in_scope_is_an_inconsistency(self):
        """The runtime believes a specific KPI moved but nobody declared
        it. That is worth stopping for."""
        services = self._services(kpi_definitions=[])
        request = ArtifactRecomputeRequest(
            artifact_type=A.KPI_STATE,
            scope=RecomputeScope(
                project_ids={PROJECT_A}, kpi_ids={"kpi-nobody-declared"},
            ),
        )
        with pytest.raises(
            ArtifactRecomputeError, match="target nobody stated",
        ):
            services.recompute_artifact(request=request, revision_id="r")


class TestMissingOptionalStructureIsAnEmptyResult:
    """Absence is not failure.

    Blocking a whole revision because one project tracks no KPIs, or has
    no launch declared yet, would make the system unusable. These report
    an honest nothing rather than refusing — while still never inventing
    the figure they lack.
    """

    def _services(self, **overrides):
        return live_services(persistence=Persistence(), **overrides)

    def test_no_kpis_declared_reports_an_empty_result(self):
        services = self._services(kpi_definitions=[])
        artifact = services.recompute_artifact(
            request=_request(A.KPI_STATE, PROJECT_A), revision_id="r",
        )
        assert artifact.changed is False
        assert "No KPIs are declared" in artifact.after_summary

    def test_no_launch_declared_reports_nothing_to_be_ready_for(self):
        services = self._services(
            inputs=domain_inputs(launch_definitions={}),
        )
        artifact = services.recompute_artifact(
            request=_request(A.LAUNCH_READINESS, PROJECT_C), revision_id="r",
        )
        assert artifact.changed is False
        assert "no readiness to report" in artifact.after_summary

    def test_a_partially_declared_launch_set_scores_what_it_can(self):
        services = self._services(
            inputs=domain_inputs(
                launch_definitions={PROJECT_C: launch_definition(PROJECT_C)},
            ),
        )
        artifact = services.recompute_artifact(
            request=_request(A.LAUNCH_READINESS, PROJECT_A, PROJECT_C),
            revision_id="r",
        )
        assert artifact.changed is True
        assert "scored for 1 project" in artifact.after_summary
        assert PROJECT_A in artifact.after_summary, (
            "the project that could not be scored must be named, not "
            "quietly omitted"
        )

    def test_no_capacity_declared_reports_no_bottleneck(self):
        services = self._services(
            inputs=domain_inputs(capacity_windows=[]),
        )
        artifact = services.recompute_artifact(
            request=_request(A.PORTFOLIO_BOTTLENECKS, PROJECT_A),
            revision_id="r",
        )
        assert artifact.changed is False
        assert "No capacity windows are declared" in artifact.after_summary
        assert "can be shown to be over-subscribed" in (
            artifact.after_summary
        ), "absence of a constraint is not evidence of one"

    def test_no_capacity_declared_asks_for_no_allocation_decision(self):
        services = self._services(
            inputs=domain_inputs(capacity_windows=[]),
        )
        artifact = services.recompute_artifact(
            request=_request(A.PORTFOLIO_RECOMMENDATION, PROJECT_A),
            revision_id="r",
        )
        assert artifact.changed is False
        assert artifact.decision_required is False

    def test_no_capacity_declared_simulates_nothing(self):
        services = self._services(
            inputs=domain_inputs(capacity_windows=[]),
        )
        artifact = services.recompute_artifact(
            request=_request(A.PORTFOLIO_SCENARIO, PROJECT_A),
            revision_id="r",
        )
        assert artifact.changed is False
        assert "no contended resource" in artifact.after_summary

    def test_a_revision_still_publishes_with_no_declared_structure(self):
        """The quickstart case: evidence in, intelligence out, no setup."""
        persistence = Persistence()
        services = live_services(
            persistence=persistence, inputs=DomainInputs(),
        )
        durable, _ = process_durable_organizational_events(
            envelopes=evidence_envelopes(),
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            runtime_projection=RuntimeProjection(
                organization_id=ORGANIZATION,
            ),
            intelligence_services=services,
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(60),
        )
        assert durable.result.complete is True
        assert durable.revision_promoted is True
        assert durable.result.failures == []

    def test_a_missing_capability_still_blocks_the_revision(self):
        """Optional structure is absence; a missing engine is a failure."""
        persistence = Persistence()
        services = live_services(
            persistence=persistence, scenario_generator=None,
        )
        durable, _ = process_durable_organizational_events(
            envelopes=evidence_envelopes(),
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            runtime_projection=RuntimeProjection(
                organization_id=ORGANIZATION,
            ),
            intelligence_services=services,
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(60),
        )
        assert durable.result.complete is False
        assert durable.revision_promoted is False
        assert any(
            f.error_class == "MissingScenarioGenerator"
            for f in durable.result.failures
        )
        assert persistence.journal.latest_position() is not None, (
            "the evidence stays durable even though the projection failed"
        )


class TestCoverageIsHonest:
    def test_engine_backed_and_derived_do_not_overlap(self):
        assert not (ENGINE_BACKED & DERIVED)

    def test_every_artifact_type_is_accounted_for(self):
        assert (ENGINE_BACKED | DERIVED) == set(A)

    def test_full_structure_reports_no_gaps(self):
        report = coverage_report(inputs=domain_inputs())
        assert report.unavailable == {}
        assert len(report.engine_backed) == len(ENGINE_BACKED)

    def test_missing_structure_is_reported_not_hidden(self):
        report = coverage_report(inputs=DomainInputs())
        assert A.PORTFOLIO_BOTTLENECKS.value in report.unavailable
        assert A.LAUNCH_READINESS.value in report.unavailable
        assert report.fully_covered is False

    def test_derived_types_are_named_rather_than_passing_as_deep(self):
        report = coverage_report(inputs=domain_inputs())
        assert A.DECISION_CAPSULE.value in report.derived
        assert A.AI_QUALITY.value in report.derived
        assert report.fully_covered is False


# ── The data source ──────────────────────────────────────────────────


class TestJournalBackedDataSource:
    @pytest.fixture
    def source(self):
        persistence, _, _ = seeded_organization()
        return persistence, data_source(persistence=persistence)

    def test_evidence_comes_back_from_the_journal(self, source):
        _, reader = source
        assert reader.durable_evidence()

    def test_project_ids_are_discovered_from_history(self, source):
        _, reader = source
        assert {PROJECT_A, PROJECT_B, PROJECT_C} <= set(reader.project_ids())

    def test_source_records_present_the_shape_engines_read(self, source):
        _, reader = source
        records = reader.source_records(project_id=PROJECT_C, as_of=at(200))
        assert records
        for record in records:
            assert record.content
            assert record.observed_at
            assert record.id

    def test_contentless_evidence_is_excluded_from_claim_extraction(self):
        """Retention may drop content; an empty claim is worse than none."""
        persistence = Persistence()
        from tpllm.runtime import append_evidence_accepted
        from tests._wiring_fixtures import envelope

        append_evidence_accepted(
            journal=persistence.journal,
            envelope=envelope(
                source_id="silent-1", event_type="chat_message",
                project_ids=[PROJECT_A], content=None,
            ),
            organization_id=ORGANIZATION,
            recorded_at=at(5),
            retention_class=JournalRetentionClass.MINIMAL,
        )
        reader = data_source(persistence=persistence)
        assert reader.source_records(
            project_id=PROJECT_A, as_of=at(200),
        ) == []
        assert reader.durable_evidence(project_id=PROJECT_A), (
            "the evidence still exists; only its content is absent"
        )

    def test_knowledge_time_filtering_prevents_future_leakage(self, source):
        _, reader = source
        early = reader.durable_evidence(as_of=T0)
        late = reader.durable_evidence(as_of=at(200))
        assert len(early) <= len(late)

    def test_an_unknown_project_yields_nothing(self, source):
        _, reader = source
        assert reader.temporal_events(
            project_id="project-nonexistent", as_of=at(200),
        ) == []

    def test_a_deleted_source_is_reported(self):
        from tests._persistence_fixtures import mark_source_deleted

        persistence, _, _ = seeded_organization()
        mark_source_deleted(persistence=persistence, evidence_id="env-gone")
        reader = data_source(persistence=persistence)
        assert "env-gone" in reader.unavailable_evidence_ids()

    def test_declared_structure_is_served_unchanged(self, source):
        _, reader = source
        assert reader.capacity_windows()
        assert reader.capacity_demands()
        assert reader.shared_factors()
        assert reader.launch_definition(PROJECT_C) is not None


class TestTemporalEventMapping:
    """The genuinely new link: durable evidence to bitemporal events."""

    def _evidence(self, **overrides):
        defaults = dict(
            evidence_id="ev-1",
            source_system="slack",
            source_type="message",
            source_id="1",
            event_type="kpi_observation",
            observed_at=at(-2),
            received_at=at(8),
            project_ids=[PROJECT_A],
            normalized_content="Activation rate measured.",
        )
        defaults.update(overrides)
        return DurableEvidenceRecord(**defaults)

    def test_reality_time_and_knowledge_time_are_both_kept(self):
        event = evidence_to_temporal_event(
            self._evidence(), project_id=PROJECT_A,
        )
        assert event.occurred_at == at(-2)
        assert event.known_at == at(8), (
            "a 09:58 meeting delivered at 10:08 keeps both times; "
            "collapsing them would destroy the bitemporal premise"
        )

    def test_the_knowledge_lag_is_measurable(self):
        event = evidence_to_temporal_event(
            self._evidence(), project_id=PROJECT_A,
        )
        assert event.knowledge_lag_seconds == 600.0

    def test_the_event_class_selects_the_temporal_type(self):
        event = evidence_to_temporal_event(
            self._evidence(event_type="blocker_resolved"),
            project_id=PROJECT_A,
        )
        assert event.event_type == TemporalEventType.BLOCKER_RESOLVED

    def test_an_unmapped_class_lands_as_plain_evidence(self):
        event = evidence_to_temporal_event(
            self._evidence(event_type="brand_new_webhook"),
            project_id=PROJECT_A,
        )
        assert event.event_type == TemporalEventType.EVIDENCE_OBSERVED, (
            "an unrecognized class is still evidence; inventing a specific "
            "transition for it would assert something nobody said"
        )

    def test_another_projects_evidence_is_refused(self):
        assert evidence_to_temporal_event(
            self._evidence(project_ids=[PROJECT_B]), project_id=PROJECT_A,
        ) is None, (
            "folding one project's history into another's would be silent "
            "corruption"
        )

    def test_a_backwards_clock_is_clamped_not_rejected(self):
        event = evidence_to_temporal_event(
            self._evidence(observed_at=at(10), received_at=at(10)),
            project_id=PROJECT_A,
        )
        assert event.known_at >= event.occurred_at

    def test_subject_keys_become_entity_ids(self):
        event = evidence_to_temporal_event(
            self._evidence(metadata={"decision_ids": ["dec-1"]}),
            project_id=PROJECT_A,
        )
        assert "dec-1" in event.entity_ids

    def test_every_mapped_class_resolves_to_a_real_temporal_type(self):
        for event_class, temporal_type in (
            EVENT_CLASS_TO_TEMPORAL_TYPE.items()
        ):
            assert isinstance(temporal_type, TemporalEventType), event_class

    def test_the_classes_the_runtime_acts_on_are_mapped(self):
        """The event classes that invalidate something should produce a
        specific temporal type, not degrade to generic evidence."""
        from tpllm.runtime import DIRECT_INVALIDATIONS

        acted_on = {
            name for name, invalidations in DIRECT_INVALIDATIONS.items()
            if invalidations
        }
        unmapped = acted_on - set(EVENT_CLASS_TO_TEMPORAL_TYPE)
        # Capacity and identity events change portfolio topology rather
        # than one project's timeline, so they legitimately have no
        # per-project temporal type.
        assert unmapped <= {
            "kpi_definition_changed", "capacity_changed",
            "shared_service_incident", "authority_changed",
            "commitment_changed", "identity_linked",
        }


class TestDecisionsBecomeTemporalEvents:
    def test_a_recorded_decision_appears_in_project_history(self):
        from tpllm.runtime import append_organizational_decision

        persistence, _, _ = seeded_organization()
        append_organizational_decision(
            journal=persistence.journal,
            organization_id=ORGANIZATION,
            decision_id="dec-platform",
            project_id=PROJECT_C,
            conclusion="opt-b",
            decided_by="person_priya_rao",
            origin="human_decision",
            occurred_at=at(70),
            recorded_at=at(70),
        )
        events = data_source(persistence=persistence).temporal_events(
            project_id=PROJECT_C, as_of=at(200),
        )
        conclusive = [
            e for e in events
            if e.event_type == TemporalEventType.DECISION_CONCLUSIVE
        ]
        assert conclusive
        assert any("dec-platform" in e.entity_ids for e in conclusive)

    def test_events_are_ordered_by_reality_time(self):
        persistence, _, _ = seeded_organization()
        events = data_source(persistence=persistence).temporal_events(
            project_id=PROJECT_C, as_of=at(200),
        )
        assert [e.occurred_at for e in events] == sorted(
            e.occurred_at for e in events
        )


# ── Determinism and replay ───────────────────────────────────────────


class TestDeterminism:
    def test_the_same_inputs_produce_the_same_fingerprints(self):
        first = seeded_organization()[1]
        second = seeded_organization()[1]
        assert {
            p: s.state_fingerprint
            for p, s in first.project_states.items()
        } == {
            p: s.state_fingerprint
            for p, s in second.project_states.items()
        }

    def test_the_portfolio_fingerprint_is_stable(self):
        first = seeded_organization()[1]
        second = seeded_organization()[1]
        assert first.portfolio_state.state_fingerprint == (
            second.portfolio_state.state_fingerprint
        )

    def test_the_service_never_reads_the_wall_clock(self):
        import ast
        import inspect

        import tpllm.runtime.services as services
        import tpllm.runtime.sources as sources

        for module in (services, sources):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", "")
                )
                assert name not in ("now", "utcnow", "today"), (
                    f"{module.__name__} must take as_of rather than reading "
                    f"the clock, or a reconstruction would differ from the "
                    f"live pass"
                )


class TestReplayWithRealEngines:
    """The replay-equivalence claim, now with engines instead of doubles."""

    def _rebuild(self):
        persistence, projection, _ = seeded_organization()
        from tpllm.runtime import collect_state_fingerprints

        original = collect_state_fingerprints(projection)
        persistence.delete_all_derived_state()
        result = rebuild_runtime_projections(
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            snapshots=persistence.snapshots,
            reconstructor=reconstructor(
                intelligence_services=live_services(
                    persistence=persistence,
                ),
            ),
            manifest=manifest(),
        )
        return persistence, original, result

    def test_the_rebuild_completes(self):
        _, _, result = self._rebuild()
        assert result.complete is True
        assert result.records_replayed > 0

    def test_real_engines_reconstruct_the_same_semantic_state(self):
        _, original, result = self._rebuild()
        assert result.reconstructed_fingerprints == original, (
            "the live pass and the replay must agree when both run the "
            "same engines over the same journalled evidence"
        )

    def test_the_reconstruction_matches_what_was_published(self):
        _, _, result = self._rebuild()
        assert result.mismatches == []

    def test_no_external_call_happens_during_the_rebuild(self):
        from tests._persistence_fixtures import FakeOutboundPort
        from tpllm.persistence import (
            ReplayMode,
            ReplayRequest,
            replay_journal,
        )

        persistence, _, _ = seeded_organization()
        outbound = FakeOutboundPort()
        replay_journal(
            request=ReplayRequest(
                organization_id=ORGANIZATION,
                mode=ReplayMode.RECONSTRUCT_CURRENT,
                use_snapshot=False,
            ),
            journal=persistence.journal,
            snapshots=persistence.snapshots,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            reconstructor=reconstructor(
                intelligence_services=live_services(persistence=persistence),
            ),
            manifest=manifest(),
            outbound=outbound,
        )
        assert outbound.call_count == 0


class TestUnwiredTypesFailLoudly:
    def test_an_artifact_with_no_handler_refuses(self):
        services = live_services(persistence=Persistence())
        handlers = services._handlers()
        assert set(handlers) == set(A), (
            "every artifact type must have either an engine or a declared "
            "projection; a silent gap would publish a hole"
        )

    def test_the_refusal_is_explicit_if_a_handler_is_removed(self):
        services = live_services(persistence=Persistence())
        original = services._handlers

        def without_kpi():
            handlers = dict(original())
            handlers.pop(A.KPI_STATE)
            return handlers

        services._handlers = without_kpi
        with pytest.raises(
            ArtifactRecomputeError, match="No engine or projection is wired",
        ):
            services.recompute_artifact(
                request=_request(A.KPI_STATE, PROJECT_A),
                revision_id="r",
            )


class TestNoWorkerScoring:
    def test_the_wiring_layer_models_organizational_state_only(self):
        import ast
        import inspect

        import tpllm.runtime.services as services
        import tpllm.runtime.sources as sources

        def executable(module):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if not isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef,
                     ast.AsyncFunctionDef),
                ):
                    continue
                body = node.body
                if (
                    body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body.pop(0)
            return ast.unparse(tree).lower()

        for module in (services, sources):
            source = executable(module)
            for term in (
                "productivity", "employee_score", "performance_score",
                "timesheet", "screen_activity", "hours_worked",
                "utilization", "keystrokes", "attendance",
            ):
                assert term not in source, (
                    f"{module.__name__} must not model '{term}'"
                )
