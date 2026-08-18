"""Tests for Commit 30 — revision coherence.

The bug this file exists to prevent has no exception and no crash: a KPI
from revision 51 shown beside a scenario from revision 49, under one
heading, presented as the current state of the world.
"""

from __future__ import annotations

import pytest

from lltpm.runtime import (
    IntelligenceArtifactType as A,
    RecomputeFailure,
    RecomputeScope,
    RecomputedArtifact,
    RevisionIncompleteError,
    RuntimeStateStore,
    apply_artifacts_to_portfolio_state,
    apply_artifacts_to_project_state,
    artifacts_share_revision,
    build_candidate_revision,
    complete_revision,
    mismatched_revision_artifacts,
)
from lltpm.runtime.models import ProjectIntelligenceRuntimeState
from tests._runtime_fixtures import PROJECT_A, PROJECT_C, T0, at, store

REQUIRED = [A.KPI_STATE, A.LAUNCH_READINESS, A.SCENARIO_LATTICE]


def _artifact(artifact_type: A, revision_id: str = "revision-1", **overrides):
    defaults = dict(
        artifact_type=artifact_type,
        artifact_id=f"{artifact_type.value}-1",
        revision_id=revision_id,
        state_fingerprint=f"fp-{artifact_type.value}",
        project_ids=[PROJECT_A],
        after_summary=f"{artifact_type.value} refreshed.",
    )
    defaults.update(overrides)
    return RecomputedArtifact(**defaults)


def _candidate(**overrides):
    defaults = dict(
        revision_index=1,
        evidence_cutoff_at=T0,
        created_at=at(5),
        project_ids=[PROJECT_A],
        root_evidence_ids=["ev-1"],
        required_artifact_types=list(REQUIRED),
    )
    defaults.update(overrides)
    return build_candidate_revision(**defaults)


class TestCandidateRevision:
    def test_a_candidate_starts_incomplete(self):
        candidate = _candidate()
        assert candidate.complete is False
        assert "not yet computed" in candidate.incomplete_reason

    def test_a_candidate_records_its_evidence_cutoff(self):
        assert _candidate().evidence_cutoff_at == T0

    def test_a_candidate_records_its_parent(self):
        assert _candidate(
            parent_revision_id="revision-0",
        ).parent_revision_id == "revision-0"

    def test_identical_inputs_fingerprint_identically(self):
        assert _candidate().state_fingerprint == _candidate().state_fingerprint

    def test_different_evidence_fingerprints_differently(self):
        assert _candidate().state_fingerprint != _candidate(
            root_evidence_ids=["ev-2"],
        ).state_fingerprint


class TestCompletion:
    def test_all_required_artifacts_present_completes_it(self):
        revision = complete_revision(
            revision=_candidate(),
            artifacts=[_artifact(t) for t in REQUIRED],
        )
        assert revision.complete is True
        assert revision.incomplete_reason is None

    def test_a_missing_required_artifact_leaves_it_incomplete(self):
        revision = complete_revision(
            revision=_candidate(),
            artifacts=[_artifact(A.KPI_STATE)],
        )
        assert revision.complete is False
        assert "launch_readiness" in revision.incomplete_reason
        assert "scenario_lattice" in revision.incomplete_reason

    def test_any_failure_leaves_it_incomplete(self):
        revision = complete_revision(
            revision=_candidate(),
            artifacts=[_artifact(t) for t in REQUIRED],
            failures=[
                RecomputeFailure(
                    artifact_type=A.SCENARIO_LATTICE,
                    scope=RecomputeScope(), error_class="TimeoutError",
                ),
            ],
        )
        assert revision.complete is False
        assert "not coherent" in revision.incomplete_reason

    def test_published_artifacts_are_recorded_either_way(self):
        revision = complete_revision(
            revision=_candidate(), artifacts=[_artifact(A.KPI_STATE)],
        )
        assert revision.published_artifact_ids == ["kpi_state-1"]


class TestArtifactsShareTheRevision:
    def test_matching_artifacts_pass(self):
        artifacts = [_artifact(t) for t in REQUIRED]
        assert artifacts_share_revision(artifacts, revision_id="revision-1")
        assert mismatched_revision_artifacts(
            artifacts, revision_id="revision-1",
        ) == []

    def test_a_stale_artifact_is_named(self):
        artifacts = [
            _artifact(A.KPI_STATE, revision_id="revision-51"),
            _artifact(A.SCENARIO_LATTICE, revision_id="revision-49"),
        ]
        assert artifacts_share_revision(
            artifacts, revision_id="revision-51",
        ) is False
        assert mismatched_revision_artifacts(
            artifacts, revision_id="revision-51",
        ) == ["scenario_lattice-1"]


class TestPromotion:
    def test_a_complete_revision_becomes_current(self):
        revision = complete_revision(
            revision=_candidate(),
            artifacts=[_artifact(t) for t in REQUIRED],
        )
        promoted = store().promote(revision)
        assert promoted.current_revision.id == revision.id
        assert revision in promoted.revision_history

    def test_an_incomplete_candidate_is_refused(self):
        with pytest.raises(RevisionIncompleteError, match="not complete"):
            store().promote(_candidate())

    def test_a_failed_candidate_leaves_the_previous_revision_current(self):
        good = complete_revision(
            revision=_candidate(),
            artifacts=[_artifact(t) for t in REQUIRED],
        )
        current = store().promote(good)

        failed = complete_revision(
            revision=_candidate(revision_index=2),
            artifacts=[],
            failures=[
                RecomputeFailure(
                    artifact_type=A.SCENARIO_LATTICE,
                    scope=RecomputeScope(), error_class="TimeoutError",
                ),
            ],
        )
        after = current.record_failed_candidate(failed)
        assert after.current_revision.id == good.id, (
            "a failed recompute must not replace a valid answer"
        )
        assert failed in after.revision_history
        assert after.metrics.failed_revisions == 1

    def test_the_failed_candidate_stays_visible(self):
        failed = complete_revision(
            revision=_candidate(), artifacts=[],
            failures=[
                RecomputeFailure(
                    artifact_type=A.KPI_STATE, scope=RecomputeScope(),
                    error_class="TimeoutError",
                ),
            ],
        )
        after = store().record_failed_candidate(failed)
        assert after.revision_history[-1].incomplete_reason


class TestStoreIsCopyOnWrite:
    def test_promotion_does_not_mutate_the_original(self):
        base = store()
        revision = complete_revision(
            revision=_candidate(),
            artifacts=[_artifact(t) for t in REQUIRED],
        )
        base.promote(revision)
        assert base.current_revision is None

    def test_processed_keys_accumulate_without_duplicates(self):
        after = store().with_processed_keys(["k1", "k2"]).with_processed_keys(
            ["k2", "k3"]
        )
        assert after.processed_idempotency_keys == ["k1", "k2", "k3"]

    def test_fingerprints_merge(self):
        after = store().with_fingerprints({"a": "1"}).with_fingerprints(
            {"b": "2"}
        )
        assert after.artifact_fingerprints == {"a": "1", "b": "2"}

    def test_watermarks_only_move_forward(self):
        after = store().with_temporal_watermarks(
            latest_occurred_at=at(10), subject_latest={"s": at(10)},
        ).with_temporal_watermarks(
            latest_occurred_at=at(5), subject_latest={"s": at(5)},
        )
        assert after.latest_occurred_at == at(10)
        assert after.subject_latest_occurred_at["s"] == at(10)

    def test_metrics_accumulate(self):
        after = store().with_metrics(events_received=2).with_metrics(
            events_received=3
        )
        assert after.metrics.events_received == 5

    def test_research_rounds_are_counted_per_causal_event(self):
        after = store().with_research_round("c1").with_research_round("c1")
        assert after.research_rounds_by_causal_event["c1"] == 2
        assert after.metrics.self_research_rounds_triggered == 2

    def test_contested_subjects_accumulate_without_duplicates(self):
        after = store().with_contested_subjects(
            ["decision:x"]
        ).with_contested_subjects(["decision:x", "decision:y"])
        assert after.contested_subject_keys == ["decision:x", "decision:y"]


class TestStatePointers:
    def test_project_state_points_at_the_new_artifacts(self):
        state = apply_artifacts_to_project_state(
            state=ProjectIntelligenceRuntimeState(project_id=PROJECT_A),
            artifacts=[
                _artifact(A.TEMPORAL_STATE),
                _artifact(A.KPI_STATE),
                _artifact(A.SCENARIO_LATTICE),
            ],
            revision_id="revision-1",
            now=at(5),
        )
        assert state.active_revision_id == "revision-1"
        assert state.temporal_state_id == "temporal_state-1"
        assert state.kpi_state_ids == ["kpi_state-1"]
        assert state.scenario_lattice_id == "scenario_lattice-1"
        assert state.last_refresh_at == at(5)

    def test_a_material_change_stamps_the_material_timestamp(self):
        state = apply_artifacts_to_project_state(
            state=ProjectIntelligenceRuntimeState(project_id=PROJECT_A),
            artifacts=[_artifact(A.KPI_STATE)],
            revision_id="revision-1", now=at(5), material=True,
        )
        assert state.last_material_change_at == at(5)

    def test_a_non_material_refresh_does_not(self):
        state = apply_artifacts_to_project_state(
            state=ProjectIntelligenceRuntimeState(project_id=PROJECT_A),
            artifacts=[_artifact(A.KPI_STATE)],
            revision_id="revision-1", now=at(5), material=False,
        )
        assert state.last_material_change_at is None

    def test_the_project_fingerprint_updates_with_the_pointers(self):
        first = apply_artifacts_to_project_state(
            state=ProjectIntelligenceRuntimeState(project_id=PROJECT_A),
            artifacts=[_artifact(A.KPI_STATE)],
            revision_id="revision-1", now=at(5),
        )
        second = apply_artifacts_to_project_state(
            state=first,
            artifacts=[_artifact(A.LAUNCH_READINESS)],
            revision_id="revision-2", now=at(6),
        )
        assert first.state_fingerprint != second.state_fingerprint

    def test_portfolio_state_points_at_the_new_artifacts(self):
        state = apply_artifacts_to_portfolio_state(
            state=None,
            artifacts=[
                _artifact(A.PORTFOLIO_GRAPH),
                _artifact(A.PORTFOLIO_BOTTLENECKS),
                _artifact(A.PORTFOLIO_RECOMMENDATION),
            ],
            revision_id="revision-1",
            now=at(5),
            affected_project_ids=[PROJECT_A, PROJECT_C],
        )
        assert state.portfolio_graph_id == "portfolio_graph-1"
        assert state.bottleneck_ids == ["portfolio_bottlenecks-1"]
        assert state.portfolio_recommendation_ids == [
            "portfolio_recommendation-1"
        ]
        assert state.affected_project_ids == [PROJECT_A, PROJECT_C]

    def test_an_empty_store_has_no_current_revision(self):
        assert RuntimeStateStore().current_revision is None
        assert RuntimeStateStore().next_revision_index() == 1
