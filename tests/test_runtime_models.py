"""Tests for Commit 30 — runtime vocabulary and fingerprints.

The models are where several invariants are made unrepresentable rather
than merely discouraged: an event received before it happened, a
non-material change demanding attention, a scope that cannot identify
itself.
"""

from __future__ import annotations

import pytest

from lltpm.runtime import (
    PLANNED_EVENT_CLASSES,
    SAFETY_CRITICAL_ARTIFACT_TYPES,
    DecisionContext,
    IntelligenceArtifactType,
    IntelligenceMaterialityAssessment,
    LiveOrchestrationResult,
    MaterialityReason,
    NoActionReason,
    RecomputeFailure,
    RecomputeScope,
    RuntimeAttentionAction,
    RuntimeEvidenceEvent,
    RuntimeFanoutPolicy,
    RuntimeMetrics,
    RuntimeResearchBudget,
    RuntimeTopology,
    ScenarioMaterialityPolicy,
    build_decision_context_fingerprint,
    build_portfolio_state_fingerprint,
    build_project_state_fingerprint,
    build_revision_fingerprint,
    build_scenario_input_fingerprint,
)
from tests._runtime_fixtures import (
    PLATFORM,
    PROJECT_A,
    PROJECT_C,
    T0,
    at,
    decision_context,
    event,
    topology,
)


class TestArtifactTypes:
    def test_every_spec_artifact_type_exists(self):
        expected = {
            "evidence", "identity", "temporal_state", "project_understanding",
            "workstream_state", "kpi_state", "launch_readiness",
            "decision_state", "decision_capsule", "decision_recommendation",
            "scenario_decision_recommendation", "scenario_lattice",
            "ai_quality", "portfolio_graph", "portfolio_bottlenecks",
            "portfolio_scenario", "portfolio_recommendation",
            "procedural_memory",
        }
        assert {a.value for a in IntelligenceArtifactType} == expected

    def test_decision_artifacts_are_safety_critical(self):
        assert IntelligenceArtifactType.SCENARIO_LATTICE in (
            SAFETY_CRITICAL_ARTIFACT_TYPES
        )
        assert IntelligenceArtifactType.DECISION_RECOMMENDATION in (
            SAFETY_CRITICAL_ARTIFACT_TYPES
        )

    def test_advisory_artifacts_are_not_safety_critical(self):
        assert IntelligenceArtifactType.AI_QUALITY not in (
            SAFETY_CRITICAL_ARTIFACT_TYPES
        )


class TestRuntimeEvent:
    def test_an_event_cannot_be_received_before_it_happened(self):
        with pytest.raises(ValueError, match="received_at precedes"):
            event(
                envelope_id="env-1", event_class="kpi_observation",
                occurred_at=at(10), received_at=at(5),
            )

    def test_a_causal_root_is_required(self):
        with pytest.raises(ValueError, match="causal_event_id"):
            RuntimeEvidenceEvent(
                envelope_id="env-1", occurred_at=T0, received_at=T0,
                event_class="kpi_observation", causal_event_id="",
            )

    def test_late_arrival_is_measured_not_rejected(self):
        late = event(
            envelope_id="env-late", event_class="meeting_transcript",
            occurred_at=at(0), received_at=at(10),
        )
        assert late.arrival_lag_seconds == 600.0

    def test_a_plan_is_not_an_observation(self):
        planned = event(
            envelope_id="env-freeze", event_class="scheduled_freeze",
        )
        observed = event(
            envelope_id="env-kpi", event_class="kpi_observation",
        )
        assert planned.is_planned is True
        assert observed.is_planned is False

    def test_planned_classes_cover_commitments_and_forecasts(self):
        assert "commitment_made" in PLANNED_EVENT_CLASSES
        assert "forecast" in PLANNED_EVENT_CLASSES
        assert "kpi_observation" not in PLANNED_EVENT_CLASSES


class TestRecomputeScope:
    def test_equal_scopes_share_a_key(self):
        first = RecomputeScope(project_ids={PROJECT_A, PROJECT_C})
        second = RecomputeScope(project_ids={PROJECT_C, PROJECT_A})
        assert first.scope_key() == second.scope_key()

    def test_different_scopes_differ(self):
        assert RecomputeScope(
            project_ids={PROJECT_A},
        ).scope_key() != RecomputeScope(
            project_ids={PROJECT_C},
        ).scope_key()

    def test_merging_takes_the_union(self):
        merged = RecomputeScope(
            project_ids={PROJECT_A}, kpi_ids={"kpi-1"},
        ).merged_with(
            RecomputeScope(project_ids={PROJECT_C}, kpi_ids={"kpi-2"})
        )
        assert merged.project_ids == {PROJECT_A, PROJECT_C}
        assert merged.kpi_ids == {"kpi-1", "kpi-2"}

    def test_a_portfolio_requirement_survives_the_merge(self):
        merged = RecomputeScope().merged_with(
            RecomputeScope(portfolio_refresh_required=True)
        )
        assert merged.portfolio_refresh_required is True

    def test_an_empty_scope_knows_it(self):
        assert RecomputeScope().is_empty is True
        assert RecomputeScope(project_ids={PROJECT_A}).is_empty is False


class TestMaterialityModel:
    def test_a_non_material_change_cannot_demand_attention(self):
        with pytest.raises(ValueError, match="must not demand human"):
            IntelligenceMaterialityAssessment(
                material=False, human_attention_required=True,
            )

    def test_score_is_bounded(self):
        with pytest.raises(ValueError, match="within"):
            IntelligenceMaterialityAssessment(material=True, score=1.5)

    def test_silence_carries_a_reason(self):
        quiet = IntelligenceMaterialityAssessment(
            no_action_reasons=[NoActionReason.NON_MATERIAL],
        )
        assert quiet.material is False
        assert quiet.no_action_reasons == [NoActionReason.NON_MATERIAL]

    def test_every_spec_materiality_reason_exists(self):
        assert MaterialityReason.AUTONOMOUS_ACTION_BLOCKED
        assert MaterialityReason.BOTTLENECK_RESOLVED
        assert len(list(MaterialityReason)) == 14

    def test_every_spec_no_action_reason_exists(self):
        assert {r.value for r in NoActionReason} == {
            "duplicate", "non_material", "already_resolved",
            "already_answered", "no_human_judgment_required",
            "existing_plan_sufficient",
        }


class TestPolicies:
    def test_scenario_policy_defaults_match_the_spec(self):
        policy = ScenarioMaterialityPolicy()
        assert policy.minimum_absolute_probability_delta == 0.10
        assert policy.severe_outcome_probability_delta == 0.05
        assert policy.threshold_crossings_are_material is True
        assert policy.minimum_confidence_for_probability_alert == 0.30

    def test_scenario_policy_bounds_its_thresholds(self):
        with pytest.raises(ValueError, match="within"):
            ScenarioMaterialityPolicy(minimum_absolute_probability_delta=1.5)

    def test_research_budget_defaults(self):
        budget = RuntimeResearchBudget()
        assert budget.maximum_research_rounds_per_causal_event == 3
        assert budget.maximum_queries_per_round == 5
        assert budget.minimum_expected_information_gain == 0.10

    def test_research_budget_rejects_nonsense(self):
        with pytest.raises(ValueError, match="negative"):
            RuntimeResearchBudget(maximum_research_rounds_per_causal_event=-1)

    def test_fanout_policy_must_admit_at_least_one_project(self):
        with pytest.raises(ValueError, match="at least one"):
            RuntimeFanoutPolicy(maximum_projects_per_immediate_batch=0)

    def test_topology_resolves_shared_resources_to_projects(self):
        assert topology().projects_touching(resource_ids=[PLATFORM]) == {
            "project-a", "project-b", "project-c",
        }

    def test_topology_is_empty_by_default(self):
        assert RuntimeTopology().projects_touching(
            resource_ids=["anything"],
        ) == set()


class TestFingerprints:
    def test_project_fingerprint_is_semantic_not_positional(self):
        first = build_project_state_fingerprint(
            project_id=PROJECT_A, kpi_state_ids=["k1", "k2"],
        )
        second = build_project_state_fingerprint(
            project_id=PROJECT_A, kpi_state_ids=["k2", "k1"],
        )
        assert first == second

    def test_project_fingerprint_moves_with_real_change(self):
        assert build_project_state_fingerprint(
            project_id=PROJECT_A, kpi_state_ids=["k1"],
        ) != build_project_state_fingerprint(
            project_id=PROJECT_A, kpi_state_ids=["k1", "k2"],
        )

    def test_decision_fingerprint_ignores_extra_supporting_evidence(self):
        base = decision_context()
        confirmed = base.model_copy(update={
            "supporting_evidence_ids": ["ev-1", "ev-2", "ev-3"],
        })
        assert build_decision_context_fingerprint(base) == (
            build_decision_context_fingerprint(confirmed)
        ), (
            "a third source confirming a known fact must not invalidate a "
            "sound recommendation"
        )

    def test_decision_fingerprint_ignores_irrelevant_kpis(self):
        base = decision_context()
        noisy = base.model_copy(update={
            "noncritical_kpi_states": {"kpi-page-views": "down"},
        })
        assert build_decision_context_fingerprint(base) == (
            build_decision_context_fingerprint(noisy)
        )

    def test_decision_fingerprint_moves_with_a_critical_kpi(self):
        base = decision_context()
        broken = base.model_copy(update={
            "critical_kpi_states": {"kpi-activation-rate": "failing"},
        })
        assert build_decision_context_fingerprint(base) != (
            build_decision_context_fingerprint(broken)
        )

    def test_decision_fingerprint_moves_with_authority(self):
        base = decision_context()
        rerouted = base.model_copy(update={
            "decision_authority_id": "person_someone_else",
        })
        assert build_decision_context_fingerprint(base) != (
            build_decision_context_fingerprint(rerouted)
        )

    def test_scenario_fingerprint_includes_the_evidence_cutoff(self):
        assert build_scenario_input_fingerprint(
            project_id=PROJECT_A, temporal_state_fingerprint="t1",
            evidence_cutoff_at=T0,
        ) != build_scenario_input_fingerprint(
            project_id=PROJECT_A, temporal_state_fingerprint="t1",
            evidence_cutoff_at=at(60),
        )

    def test_portfolio_fingerprint_is_order_independent(self):
        assert build_portfolio_state_fingerprint(
            project_ids=["a", "b"],
        ) == build_portfolio_state_fingerprint(project_ids=["b", "a"])

    def test_revision_fingerprint_covers_its_inputs(self):
        assert build_revision_fingerprint(
            evidence_cutoff_at=T0, project_ids=["a"], root_evidence_ids=["e"],
            required_artifact_types=[IntelligenceArtifactType.KPI_STATE],
        ) != build_revision_fingerprint(
            evidence_cutoff_at=T0, project_ids=["a"], root_evidence_ids=["e"],
            required_artifact_types=[
                IntelligenceArtifactType.LAUNCH_READINESS,
            ],
        )


class TestDecisionContextShape:
    def test_defaults_are_inert(self):
        context = DecisionContext(decision_id="d", project_id="p")
        assert context.decision_resolved is False
        assert context.critical_unknown_ids == []


class TestFailureModel:
    def test_a_failed_scenario_is_safety_critical(self):
        failure = RecomputeFailure(
            artifact_type=IntelligenceArtifactType.SCENARIO_LATTICE,
            scope=RecomputeScope(), error_class="TimeoutError",
        )
        assert failure.is_safety_critical is True

    def test_a_failed_quality_report_is_not(self):
        failure = RecomputeFailure(
            artifact_type=IntelligenceArtifactType.AI_QUALITY,
            scope=RecomputeScope(), error_class="TimeoutError",
        )
        assert failure.is_safety_critical is False


class TestMetricsAndResult:
    def test_metrics_increment_immutably(self):
        base = RuntimeMetrics()
        raised = base.incremented(events_received=3)
        assert base.events_received == 0
        assert raised.events_received == 3

    def test_a_silent_result_reports_silence(self):
        result = LiveOrchestrationResult(
            attention_actions=[RuntimeAttentionAction.SILENT],
        )
        assert result.stayed_silent is True
        assert result.human_attention_required is False

    def test_a_decision_request_requires_attention(self):
        result = LiveOrchestrationResult(
            attention_actions=[RuntimeAttentionAction.DECISION_REQUEST],
        )
        assert result.stayed_silent is False
        assert result.human_attention_required is True

    def test_an_internal_update_does_not_require_attention(self):
        result = LiveOrchestrationResult(
            attention_actions=[RuntimeAttentionAction.UPDATE_INTERNAL_STATE],
        )
        assert result.human_attention_required is False
