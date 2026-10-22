"""End-to-end tests of the live orchestration pass.

The canonical organization:

    A  Enterprise Billing      needs a Platform review slot
    B  Mobile Migration        needs one, has a viable workaround
    C  Identity Reliability    needs one, has no workaround
    D  Partner Launch          shares nothing with Platform

Platform Architecture enters incident response and its approval capacity
falls from two slots to one. A, B, and C must move together, D must be
left alone, the five derived effects must arrive as one explanation, and
a replay of the same webhook must do nothing at all.
"""

from __future__ import annotations

import pytest

from lltpm.runtime import (
    ArtifactRecomputeError,
    IntelligenceArtifactType as A,
    NoActionReason,
    RuntimeAttentionAction,
    RuntimeFanoutPolicy,
    describe_no_action,
    process_live_organizational_events,
    render_project_surface,
    render_root_cause_surface,
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
    capacity_incident_envelope,
    duplicate_chat_envelope,
    envelope,
    kpi_envelope,
    runtime_policy,
    scenario_outcome,
    store,
)


def _services(**overrides):
    """The canonical incident outcome: C's exposure rises sharply."""
    defaults = {
        A.SCENARIO_LATTICE: scenario_outcome(
            before=0.19, after=0.49,
            project_ids=[PROJECT_A, PROJECT_B, PROJECT_C],
        ),
        A.PORTFOLIO_BOTTLENECKS: {
            "after_summary": (
                f"{PLATFORM} can serve approximately 1 of 3 requested "
                f"review slot(s)."
            ),
        },
        A.PORTFOLIO_RECOMMENDATION: {
            "decision_required": True,
            "after_summary": (
                "Prioritize project-c: no viable workaround. project-b uses "
                "its existing workaround; project-a takes the compatibility "
                "route."
            ),
        },
    }
    defaults.update(overrides)
    return FakeIntelligenceServices(outcomes=defaults)


def _run(envelopes, *, state=None, services=None, policy=None, now=None):
    return process_live_organizational_events(
        envelopes=envelopes,
        runtime_state_store=state or store(),
        intelligence_services=services or _services(),
        runtime_policy=policy or runtime_policy(),
        now=now or at(30),
    )


class TestTheCanonicalIncident:
    def test_the_right_projects_are_affected(self):
        result, _ = _run([capacity_incident_envelope()])
        assert result.projects_affected == [PROJECT_A, PROJECT_B, PROJECT_C]

    def test_the_unrelated_project_is_untouched(self):
        result, _ = _run([capacity_incident_envelope()])
        assert PROJECT_D not in result.projects_affected, (
            "Partner Launch shares nothing with Platform; touching it would "
            "be a false alarm"
        )

    def test_the_scenario_and_portfolio_both_refresh(self):
        services = _services()
        _run([capacity_incident_envelope()], services=services)
        called = services.called_types()
        assert A.SCENARIO_LATTICE in called
        assert A.PORTFOLIO_GRAPH in called
        assert A.PORTFOLIO_RECOMMENDATION in called

    def test_the_portfolio_refresh_happens_once(self):
        services = _services()
        _run([capacity_incident_envelope()], services=services)
        assert services.call_count(A.PORTFOLIO_GRAPH) == 1
        assert services.call_count(A.PORTFOLIO_SCENARIO) == 1

    def test_the_scenario_is_computed_once_for_the_whole_batch(self):
        services = _services()
        _run([capacity_incident_envelope()], services=services)
        assert services.call_count(A.SCENARIO_LATTICE) == 1, (
            "three affected projects share one recompute, not three"
        )

    def test_one_bundle_not_four_alerts(self):
        result, _ = _run([capacity_incident_envelope()])
        assert len(result.causal_bundles) == 1
        assert len(result.attention_actions) == 1

    def test_the_bundle_demands_attention(self):
        result, _ = _run([capacity_incident_envelope()])
        assert result.human_attention_required is True
        assert result.attention_actions == [
            RuntimeAttentionAction.HIGH_PRIORITY_ALERT
        ]

    def test_the_revision_is_complete_and_published(self):
        result, new_store = _run([capacity_incident_envelope()])
        assert result.complete is True
        assert result.revision_id == "revision-1"
        assert new_store.current_revision.id == "revision-1"
        assert new_store.current_revision.complete is True

    def test_every_artifact_belongs_to_the_published_revision(self):
        _, new_store = _run([capacity_incident_envelope()])
        revision = new_store.current_revision
        for project_id in (PROJECT_A, PROJECT_B, PROJECT_C):
            assert new_store.project_states[
                project_id
            ].active_revision_id == revision.id
        assert new_store.portfolio_state.active_revision_id == revision.id

    def test_the_surface_leads_with_the_root_cause(self):
        result, _ = _run([capacity_incident_envelope()])
        surface = render_root_cause_surface(
            bundle=result.causal_bundles[0],
            portfolio_update=result.portfolio_update,
            project_updates=result.project_updates,
            no_action_project_ids=(
                result.causal_bundles[0].no_action_project_ids
            ),
        )
        assert surface.startswith("ROOT CHANGE")
        assert "PORTFOLIO EFFECT" in surface
        assert "DECISION REQUIRED" in surface

    def test_the_surface_names_the_projects_needing_nothing(self):
        result, _ = _run([capacity_incident_envelope()])
        surface = render_root_cause_surface(
            bundle=result.causal_bundles[0],
            portfolio_update=result.portfolio_update,
            project_updates=result.project_updates,
            no_action_project_ids=(
                result.causal_bundles[0].no_action_project_ids
            ),
        )
        assert "NO ACTION REQUIRED" in surface
        assert PROJECT_D in surface

    def test_the_portfolio_recommendation_names_who_yields(self):
        result, _ = _run([capacity_incident_envelope()])
        assert any(
            "Prioritize project-c" in line
            for line in result.portfolio_update.priority_changes
        )
        assert result.portfolio_update.decisions_required

    def test_the_scenario_shift_is_stated_with_numbers(self):
        result, _ = _run([capacity_incident_envelope()])
        project_c = next(
            u for u in result.project_updates if u.project_id == PROJECT_C
        )
        assert any("19% → 49%" in c for c in project_c.scenario_changes)

    def test_the_project_surface_reads_as_change_then_action(self):
        result, _ = _run([capacity_incident_envelope()])
        project_c = next(
            u for u in result.project_updates if u.project_id == PROJECT_C
        )
        surface = render_project_surface(project_c)
        assert "WHAT CHANGED" in surface
        assert "SCENARIO SHIFT" in surface

    def test_the_quiet_project_gets_an_explicit_no_action_line(self):
        result, _ = _run([capacity_incident_envelope()])
        project_d = next(
            u for u in result.project_updates if u.project_id == PROJECT_D
        )
        assert project_d.no_action_required
        assert "unaffected" in project_d.no_action_required[0]


class TestScopedRecompute:
    def test_a_local_kpi_event_stays_local(self):
        result, _ = _run([kpi_envelope()])
        assert result.projects_affected == [PROJECT_A]

    def test_it_does_not_rebuild_the_portfolio(self):
        services = _services()
        _run([kpi_envelope()], services=services)
        assert A.PORTFOLIO_GRAPH not in services.called_types()
        assert A.PORTFOLIO_RECOMMENDATION not in services.called_types()

    def test_other_projects_decisions_are_untouched(self):
        services = _services()
        _run([kpi_envelope()], services=services)
        assert services.projects_seen() == {PROJECT_A}

    def test_the_dependency_chain_is_still_followed(self):
        services = _services()
        _run([kpi_envelope()], services=services)
        called = services.called_types()
        assert A.KPI_STATE in called
        assert A.LAUNCH_READINESS in called
        assert A.SCENARIO_LATTICE in called


class TestIdempotency:
    def test_a_replayed_webhook_does_nothing(self):
        first_result, state = _run([capacity_incident_envelope()])
        assert first_result.artifacts_recomputed

        services = _services()
        second_result, _ = _run(
            [capacity_incident_envelope()], state=state, services=services,
        )
        assert second_result.events_deduplicated == 1
        assert second_result.artifacts_recomputed == []
        assert services.calls == []

    def test_a_replay_produces_no_notification(self):
        _, state = _run([capacity_incident_envelope()])
        result, _ = _run([capacity_incident_envelope()], state=state)
        assert result.attention_actions == [RuntimeAttentionAction.SILENT]
        assert result.human_attention_required is False
        assert result.causal_bundles == []

    def test_a_replay_creates_no_second_revision(self):
        _, state = _run([capacity_incident_envelope()])
        _, after = _run([capacity_incident_envelope()], state=state)
        assert len(after.revision_history) == 1
        assert after.current_revision.id == "revision-1"

    def test_a_replay_states_why_it_did_nothing(self):
        _, state = _run([capacity_incident_envelope()])
        result, _ = _run([capacity_incident_envelope()], state=state)
        assert result.no_action_reasons == [NoActionReason.DUPLICATE]
        assert "already been processed" in describe_no_action(
            result.no_action_reasons
        )

    def test_a_replay_is_deterministic(self):
        _, state = _run([capacity_incident_envelope()])
        first, _ = _run([capacity_incident_envelope()], state=state)
        second, _ = _run([capacity_incident_envelope()], state=state)
        assert first.model_dump() == second.model_dump()

    def test_the_run_is_deterministic_from_a_clean_store(self):
        first, _ = _run([capacity_incident_envelope()])
        second, _ = _run([capacity_incident_envelope()])
        assert first.model_dump() == second.model_dump()


class TestSilence:
    def test_pure_noise_produces_silence(self):
        result, _ = _run([duplicate_chat_envelope()])
        assert result.attention_actions == [RuntimeAttentionAction.SILENT]
        assert result.artifacts_recomputed == []
        assert result.no_action_reasons == [NoActionReason.NON_MATERIAL]

    def test_noise_still_marks_the_event_processed(self):
        _, state = _run([duplicate_chat_envelope()])
        assert state.processed_idempotency_keys

    def test_noise_does_not_create_a_revision(self):
        _, state = _run([duplicate_chat_envelope()])
        assert state.current_revision is None

    def test_a_plan_is_not_treated_as_an_observation(self):
        result, state = _run([
            envelope(
                source_id="freeze-1", event_type="scheduled_freeze",
                project_ids=[PROJECT_A],
            ),
        ])
        assert result.artifacts_recomputed == []
        assert result.no_action_reasons == [
            NoActionReason.EXISTING_PLAN_SUFFICIENT
        ]
        assert state.current_revision is None


class TestConcurrentEvents:
    def test_three_events_resolve_into_one_revision(self):
        result, _ = _run([
            kpi_envelope(),
            envelope(
                source_id="blocker-1", event_type="blocker_resolved",
                project_ids=[PROJECT_A], workstream_ids=["ws-project-a"],
            ),
            envelope(
                source_id="decision-1", event_type="decision_conclusive",
                project_ids=[PROJECT_A], decision_ids=["dec-1"],
            ),
        ])
        assert result.revision_id == "revision-1"
        assert result.complete is True

    def test_they_do_not_produce_three_conflicting_surfaces(self):
        result, _ = _run([
            kpi_envelope(),
            envelope(
                source_id="blocker-1", event_type="blocker_resolved",
                project_ids=[PROJECT_A],
            ),
            envelope(
                source_id="decision-1", event_type="decision_conclusive",
                project_ids=[PROJECT_A],
            ),
        ])
        assert len(result.project_updates) <= len(ALL_PROJECTS)
        assert len({u.revision_id for u in result.project_updates}) == 1

    def test_readiness_is_computed_once_across_all_three(self):
        services = _services()
        _run(
            [
                kpi_envelope(),
                envelope(
                    source_id="blocker-1", event_type="blocker_resolved",
                    project_ids=[PROJECT_A],
                ),
            ],
            services=services,
        )
        assert services.call_count(A.LAUNCH_READINESS) == 1


class TestOutOfOrderEndToEnd:
    """The 09:58 transcript that arrives at 10:08."""

    def _approval_then_implementation(self):
        return [
            envelope(
                source_id="approval-1", event_type="decision_conclusive",
                observed_at=at(3), received_at=at(3),
                project_ids=[PROJECT_A],
                subject_keys=["decision:contract-standard"],
                asserted_state="approved",
            ),
            envelope(
                source_id="impl-1", event_type="blocker_resolved",
                observed_at=at(5), received_at=at(5),
                project_ids=[PROJECT_A],
                subject_keys=["blocker:integration"],
                asserted_state="resolved",
            ),
        ]

    def _late_transcript(self):
        return envelope(
            source_id="transcript-1", event_type="meeting_transcript",
            observed_at=at(-2), received_at=at(8),
            project_ids=[PROJECT_A],
            subject_keys=["decision:contract-standard"],
            asserted_state="pending",
        )

    def test_the_late_transcript_is_lineage_only(self):
        _, state = _run(self._approval_then_implementation(), now=at(6))
        result, _ = _run([self._late_transcript()], state=state, now=at(10))
        assert result.lineage_only_event_ids, (
            "the approval is newer on the same decision; the transcript "
            "enriches lineage rather than reopening it"
        )

    def test_it_does_not_drag_the_present_backwards(self):
        _, state = _run(self._approval_then_implementation(), now=at(6))
        services = _services()
        _run(
            [self._late_transcript()], state=state, services=services,
            now=at(10),
        )
        called = services.called_types()
        assert A.SCENARIO_LATTICE not in called, (
            "a superseded historical event must not force present-tense "
            "artifacts to recompute"
        )
        assert A.DECISION_RECOMMENDATION not in called

    def test_it_still_refreshes_the_historical_record(self):
        _, state = _run(self._approval_then_implementation(), now=at(6))
        services = _services()
        _run(
            [self._late_transcript()], state=state, services=services,
            now=at(10),
        )
        assert A.TEMPORAL_STATE in services.called_types(), (
            "an old event is not automatically an irrelevant event"
        )

    def test_the_watermark_does_not_move_backwards(self):
        _, state = _run(self._approval_then_implementation(), now=at(6))
        _, after = _run([self._late_transcript()], state=state, now=at(10))
        assert after.latest_occurred_at == at(5)

    def test_an_event_from_the_future_is_refused(self):
        with pytest.raises(ValueError, match="History is not prediction"):
            _run(
                [kpi_envelope(observed_at=at(120), received_at=at(120))],
                now=at(30),
            )


class TestContestedEvidenceEndToEnd:
    def test_conflicting_simultaneous_claims_are_recorded_as_contested(self):
        result, state = _run([
            envelope(
                source_id="team-a", event_type="decision_conclusive",
                observed_at=at(3), project_ids=[PROJECT_A],
                subject_keys=["decision:approval"], asserted_state="approved",
            ),
            envelope(
                source_id="team-b", event_type="decision_proposed",
                observed_at=at(4), project_ids=[PROJECT_A],
                subject_keys=["decision:approval"], asserted_state="pending",
            ),
        ])
        assert result.contested_subject_keys == ["decision:approval"]
        assert state.contested_subject_keys == ["decision:approval"]

    def test_processing_order_does_not_resolve_it(self):
        events = [
            envelope(
                source_id="team-a", event_type="decision_conclusive",
                observed_at=at(3), project_ids=[PROJECT_A],
                subject_keys=["decision:approval"], asserted_state="approved",
            ),
            envelope(
                source_id="team-b", event_type="decision_proposed",
                observed_at=at(4), project_ids=[PROJECT_A],
                subject_keys=["decision:approval"], asserted_state="pending",
            ),
        ]
        forward, _ = _run(events)
        backward, _ = _run(list(reversed(events)))
        assert forward.contested_subject_keys == (
            backward.contested_subject_keys
        )


class TestFailureIsSafe:
    def test_a_scenario_failure_does_not_publish_the_revision(self):
        result, state = _run(
            [capacity_incident_envelope()],
            services=FakeIntelligenceServices(failures={
                A.SCENARIO_LATTICE: ArtifactRecomputeError(
                    "scenario generation timed out",
                    error_class="TimeoutError", retryable=True,
                ),
            }),
        )
        assert result.complete is False
        assert result.revision_id is None
        assert state.current_revision is None

    def test_the_failure_reason_is_observable(self):
        result, _ = _run(
            [capacity_incident_envelope()],
            services=FakeIntelligenceServices(failures={
                A.SCENARIO_LATTICE: ArtifactRecomputeError(
                    "scenario generation timed out",
                    error_class="TimeoutError", retryable=True,
                ),
            }),
        )
        assert result.failures
        assert result.failures[0].error_class == "TimeoutError"
        assert result.failures[0].retryable is True
        assert "timed out" in result.failures[0].explanation

    def test_autonomous_action_is_blocked_when_the_scenario_is_missing(self):
        result, _ = _run(
            [capacity_incident_envelope()],
            services=FakeIntelligenceServices(failures={
                A.SCENARIO_LATTICE: ArtifactRecomputeError("boom"),
            }),
        )
        assert result.autonomous_actions_blocked
        assert "scenario_lattice unavailable" in (
            result.autonomous_actions_blocked[0]
        )

    def test_a_valid_prior_revision_keeps_answering(self):
        _, good_state = _run([kpi_envelope()])
        assert good_state.current_revision.id == "revision-1"

        _, after = _run(
            [capacity_incident_envelope()],
            state=good_state,
            services=FakeIntelligenceServices(failures={
                A.SCENARIO_LATTICE: ArtifactRecomputeError("boom"),
            }),
        )
        assert after.current_revision.id == "revision-1", (
            "a failed recompute must not replace a valid answer"
        )
        assert after.metrics.failed_revisions == 1

    def test_the_failed_candidate_stays_in_history(self):
        _, state = _run(
            [capacity_incident_envelope()],
            services=FakeIntelligenceServices(failures={
                A.SCENARIO_LATTICE: ArtifactRecomputeError("boom"),
            }),
        )
        assert state.revision_history
        assert state.revision_history[-1].complete is False
        assert state.revision_history[-1].incomplete_reason

    def test_a_non_critical_failure_still_blocks_publication(self):
        # Coherence is all-or-nothing: a revision missing any planned
        # artifact is not the state of the world. Readiness is not
        # safety-critical, so no autonomous action is blocked — but the
        # revision is still not publishable.
        result, _ = _run(
            [kpi_envelope()],
            services=FakeIntelligenceServices(failures={
                A.LAUNCH_READINESS: ArtifactRecomputeError("boom"),
            }),
        )
        assert result.complete is False
        assert result.autonomous_actions_blocked == []


class TestFanoutEndToEnd:
    def test_deferred_scope_is_reported_not_dropped(self):
        result, _ = _run(
            [capacity_incident_envelope()],
            policy=runtime_policy(
                fanout=RuntimeFanoutPolicy(
                    maximum_projects_per_immediate_batch=1,
                ),
            ),
        )
        assert result.deferred_project_ids
        assert set(result.projects_affected) | set(
            result.deferred_project_ids
        ) == {PROJECT_A, PROJECT_B, PROJECT_C}

    def test_the_high_priority_project_is_handled_first(self):
        result, _ = _run(
            [capacity_incident_envelope()],
            policy=runtime_policy(
                fanout=RuntimeFanoutPolicy(
                    maximum_projects_per_immediate_batch=1,
                ),
            ),
        )
        assert result.projects_affected == [PROJECT_C], (
            "the project with no workaround is the one that cannot wait"
        )

    def test_portfolio_work_still_coalesces_under_a_tight_budget(self):
        services = _services()
        _run(
            [capacity_incident_envelope()],
            services=services,
            policy=runtime_policy(
                fanout=RuntimeFanoutPolicy(
                    maximum_projects_per_immediate_batch=1,
                ),
            ),
        )
        assert services.call_count(A.PORTFOLIO_GRAPH) == 1


class TestMetrics:
    def test_the_pass_reports_what_it_did(self):
        result, _ = _run([capacity_incident_envelope()])
        metrics = result.metrics
        assert metrics.events_received == 1
        assert metrics.projects_affected == 3
        assert metrics.artifacts_recomputed > 0
        assert metrics.candidate_revisions == 1
        assert metrics.successful_revisions == 1

    def test_compression_is_counted(self):
        result, _ = _run([capacity_incident_envelope()])
        assert result.metrics.notifications_compressed > 0

    def test_suppression_is_counted(self):
        result, _ = _run([capacity_incident_envelope()])
        assert result.metrics.non_material_changes_suppressed > 0

    def test_deduplication_is_counted(self):
        _, state = _run([capacity_incident_envelope()])
        result, _ = _run([capacity_incident_envelope()], state=state)
        assert result.metrics.events_deduplicated == 1

    def test_metrics_accumulate_across_passes(self):
        _, state = _run([kpi_envelope()])
        _, after = _run(
            [capacity_incident_envelope()], state=state, now=at(40),
        )
        assert after.metrics.events_received == 2


class TestServerlessShape:
    def test_a_pass_is_a_pure_function_of_its_inputs(self):
        base = store()
        _run([capacity_incident_envelope()], state=base)
        assert base.current_revision is None, (
            "the store is replaced, never mutated, so a failed pass leaves "
            "the previous state intact"
        )

    def test_state_round_trips_through_serialization(self):
        from lltpm.runtime import RuntimeStateStore

        _, state = _run([capacity_incident_envelope()])
        restored = RuntimeStateStore.model_validate(state.model_dump())
        assert restored.current_revision.id == state.current_revision.id

        result, _ = _run([capacity_incident_envelope()], state=restored)
        assert result.events_deduplicated == 1, (
            "a serverless invocation can load state, compute, write, and "
            "exit — including remembering what it already processed"
        )


class TestNoEmployeeSurveillance:
    def test_the_runtime_models_organizational_state_not_people(self):
        import ast
        import inspect

        import lltpm.runtime.causality as causality
        import lltpm.runtime.dependency_graph as dependency_graph
        import lltpm.runtime.ingest as ingest
        import lltpm.runtime.materiality as materiality
        import lltpm.runtime.models as models
        import lltpm.runtime.orchestrator as orchestrator
        import lltpm.runtime.recompute as recompute
        import lltpm.runtime.revisions as revisions
        import lltpm.runtime.surfaces as surfaces
        import lltpm.runtime.validity as validity

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

        forbidden = (
            "time_spent", "screen_activity", "browser_activity",
            "message_volume", "hours_worked", "utilization",
            "personal_productivity", "employee_efficiency",
            "employee_score", "performance_score", "productivity",
            "timesheet", "keystrokes", "active_minutes", "idle_time",
            "attendance",
        )
        for module in (
            models, dependency_graph, ingest, recompute, revisions,
            validity, materiality, causality, surfaces, orchestrator,
        ):
            source = executable(module)
            for term in forbidden:
                assert term not in source, (
                    f"{module.__name__} must not model '{term}' — the "
                    f"runtime's question is what the organization needs to "
                    f"understand or decide, not what each person was doing."
                )

    def test_metrics_measure_the_system_not_the_staff(self):
        from lltpm.runtime import RuntimeMetrics

        for field in RuntimeMetrics.model_fields:
            for term in ("employee", "person", "user", "worker", "hours"):
                assert term not in field, (
                    f"runtime metric '{field}' must measure LLTPM, not "
                    f"people"
                )

    def test_the_runtime_state_holds_organizational_objects_only(self):
        from lltpm.runtime import (
            PortfolioIntelligenceRuntimeState,
            ProjectIntelligenceRuntimeState,
        )

        fields = (
            set(ProjectIntelligenceRuntimeState.model_fields)
            | set(PortfolioIntelligenceRuntimeState.model_fields)
        )
        for field in fields:
            for term in ("employee", "activity", "hours", "timesheet"):
                assert term not in field
