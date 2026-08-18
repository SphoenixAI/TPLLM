"""Tests for Commit 30 — one root cause, one explanation.

Platform Architecture's approval capacity falls. Five things move: two
project scenarios, a portfolio bottleneck, a project recommendation, a
portfolio priority. Five alerts would be complete and useless; the
reader would have to reconstruct the one cause behind them.

Grouping is by causal identity only. Two unrelated things happening in
the same minute are two things, and no amount of timestamp proximity
makes them one.
"""

from __future__ import annotations

import pytest

from lltpm.runtime import (
    CausalIntelligenceChange,
    IntelligenceArtifactType as A,
    IntelligenceMaterialityAssessment,
    MaterialityReason,
    NoActionReason,
    RecomputedArtifact,
    RuntimeAttentionAction,
    ScenarioMaterialityPolicy,
    assess_artifact_materiality,
    build_causal_change,
    build_root_summaries,
    bundle_causally_related_changes,
    causal_ancestry,
    changes_for_project,
    describe_no_action,
    determine_attention_action,
    explain_runtime_change,
    meeting_is_warranted,
)
from tests._runtime_fixtures import (
    PLATFORM,
    PROJECT_A,
    PROJECT_C,
    PROJECT_D,
    at,
    event,
)

POLICY = ScenarioMaterialityPolicy()
INCIDENT = "causal-platform-incident"


def _artifact(artifact_type, project_ids=None, **overrides):
    defaults = dict(
        artifact_type=artifact_type,
        artifact_id=f"{artifact_type.value}-{project_ids or ['org']}",
        revision_id="revision-1",
        state_fingerprint="fp",
        project_ids=list(project_ids or [PROJECT_A]),
        after_summary=f"{artifact_type.value} refreshed.",
    )
    defaults.update(overrides)
    return RecomputedArtifact(**defaults)


def _change(
    artifact_type,
    *,
    project_ids=None,
    causal_event_id=INCIDENT,
    root_evidence_ids=("ev-incident",),
    parents=(),
    material=True,
    decision_required=False,
    **artifact_overrides,
):
    artifact = _artifact(
        artifact_type, project_ids, decision_required=decision_required,
        **artifact_overrides,
    )
    materiality = (
        assess_artifact_materiality(artifact=artifact, policy=POLICY)
        if material
        else IntelligenceMaterialityAssessment(
            no_action_reasons=[NoActionReason.NON_MATERIAL],
        )
    )
    return build_causal_change(
        artifact=artifact,
        materiality=materiality,
        causal_event_id=causal_event_id,
        root_evidence_ids=list(root_evidence_ids),
        parent_change_ids=list(parents),
        origin_artifact_type=A.WORKSTREAM_STATE,
    )


def _incident_changes():
    """The spec's five derived effects of one incident."""
    scenario_a = _change(
        A.SCENARIO_LATTICE, project_ids=[PROJECT_A],
        previous_outcome_probabilities={"slip": 0.21},
        outcome_probabilities={"slip": 0.32},
    )
    scenario_c = _change(
        A.SCENARIO_LATTICE, project_ids=[PROJECT_C],
        previous_outcome_probabilities={"slip": 0.19},
        outcome_probabilities={"slip": 0.49},
    )
    bottleneck = _change(
        A.PORTFOLIO_BOTTLENECKS, project_ids=[PROJECT_A, PROJECT_C],
    )
    recommendation = _change(
        A.DECISION_RECOMMENDATION, project_ids=[PROJECT_A],
        parents=[scenario_a.id],
    )
    priority = _change(
        A.PORTFOLIO_RECOMMENDATION, project_ids=[PROJECT_A, PROJECT_C],
        parents=[bottleneck.id], decision_required=True,
    )
    return [scenario_a, scenario_c, bottleneck, recommendation, priority]


class TestCausalChange:
    def test_a_change_records_its_root_cause(self):
        change = _change(A.SCENARIO_LATTICE)
        assert change.causal_event_id == INCIDENT
        assert change.root_evidence_ids == ["ev-incident"]

    def test_a_change_records_the_dependency_path_it_travelled(self):
        change = _change(A.SCENARIO_LATTICE)
        assert change.dependency_edges == [
            "workstream_state -> scenario_lattice",
        ]

    def test_before_and_after_are_both_kept(self):
        change = _change(
            A.SCENARIO_LATTICE, before_summary="P(slip) 19%.",
            after_summary="P(slip) 49%.",
        )
        assert change.before_summary == "P(slip) 19%."
        assert change.after_summary == "P(slip) 49%."

    def test_the_revision_is_carried(self):
        assert _change(A.SCENARIO_LATTICE).revision_id == "revision-1"


class TestCompression:
    def test_five_derived_changes_become_one_bundle(self):
        bundles = bundle_causally_related_changes(_incident_changes())
        assert len(bundles) == 1, (
            "one root cause is one explanation, not five alerts"
        )
        assert len(bundles[0].material_changes) == 5

    def test_the_bundle_names_the_affected_projects(self):
        bundle = bundle_causally_related_changes(_incident_changes())[0]
        assert bundle.affected_project_ids == [PROJECT_A, PROJECT_C]

    def test_root_evidence_stays_visible(self):
        bundle = bundle_causally_related_changes(_incident_changes())[0]
        assert bundle.root_evidence_ids == ["ev-incident"]

    def test_the_decision_request_survives_compression(self):
        bundle = bundle_causally_related_changes(_incident_changes())[0]
        assert bundle.decision_required is True, (
            "compressing five alerts into one must not hide the one thing "
            "someone actually has to do"
        )

    def test_severity_is_taken_from_the_worst_change(self):
        bundle = bundle_causally_related_changes(_incident_changes())[0]
        assert bundle.highest_severity == "severe"

    def test_shared_root_evidence_merges_different_causal_ids(self):
        first = _change(
            A.KPI_STATE, causal_event_id="causal-1",
            root_evidence_ids=["ev-shared"],
        )
        second = _change(
            A.SCENARIO_LATTICE, causal_event_id="causal-2",
            root_evidence_ids=["ev-shared"],
        )
        assert len(bundle_causally_related_changes([first, second])) == 1

    def test_ancestry_merges_changes(self):
        parent = _change(
            A.KPI_STATE, causal_event_id="causal-1",
            root_evidence_ids=["ev-1"],
        )
        child = _change(
            A.SCENARIO_LATTICE, causal_event_id="causal-2",
            root_evidence_ids=["ev-2"], parents=[parent.id],
        )
        assert len(bundle_causally_related_changes([parent, child])) == 1

    def test_unrelated_events_stay_separate(self):
        first = _change(
            A.KPI_STATE, causal_event_id="causal-billing",
            root_evidence_ids=["ev-billing"],
        )
        second = _change(
            A.DECISION_STATE, causal_event_id="causal-partner",
            root_evidence_ids=["ev-partner"], project_ids=[PROJECT_D],
        )
        bundles = bundle_causally_related_changes([first, second])
        assert len(bundles) == 2, (
            "two unrelated things happening at once are two things"
        )

    def test_timestamps_are_never_used_to_group(self):
        # Scoped to the code that decides grouping. Rendering a timestamp
        # into a summary line is fine; consulting one to decide what
        # belongs together is not.
        import ast
        import inspect

        from lltpm.runtime.causality import (
            _Groups,
            bundle_causally_related_changes,
        )

        grouping_source = "\n".join([
            inspect.getsource(bundle_causally_related_changes),
            inspect.getsource(_Groups),
        ])
        tree = ast.parse(grouping_source)
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
        executable = ast.unparse(tree).lower()
        for forbidden in (
            "occurred_at", "received_at", "timedelta", "timestamp",
        ):
            assert forbidden not in executable, (
                f"causal grouping must not consult '{forbidden}' — "
                f"simultaneity is not a causal relationship"
            )

    def test_non_material_changes_are_excluded_from_the_surface(self):
        changes = _incident_changes() + [
            _change(A.WORKSTREAM_STATE, material=False),
        ]
        bundle = bundle_causally_related_changes(changes)[0]
        assert len(bundle.material_changes) == 5
        assert len(bundle.all_change_ids) == 6, (
            "the non-material change is still recorded, just not surfaced"
        )

    def test_bundling_nothing_yields_nothing(self):
        assert bundle_causally_related_changes([]) == []

    def test_bundles_are_deterministic(self):
        changes = _incident_changes()
        assert [b.id for b in bundle_causally_related_changes(changes)] == [
            b.id for b in bundle_causally_related_changes(
                list(reversed(changes))
            )
        ]

    def test_a_supplied_root_summary_is_used(self):
        bundle = bundle_causally_related_changes(
            _incident_changes(),
            root_summaries={
                INCIDENT: "Platform Architecture approval capacity reduced.",
            },
        )[0]
        assert bundle.root_summary == (
            "Platform Architecture approval capacity reduced."
        )

    def test_a_default_summary_describes_the_reach(self):
        bundle = bundle_causally_related_changes(_incident_changes())[0]
        assert "5 derived change(s)" in bundle.root_summary
        assert "2 project(s)" in bundle.root_summary

    def test_low_confidence_propagates_to_the_bundle(self):
        changes = [
            _change(
                A.SCENARIO_LATTICE,
                previous_outcome_probabilities={"slip": 0.20},
                outcome_probabilities={"slip": 0.55},
                probability_confidence=0.12,
            ),
        ]
        assert bundle_causally_related_changes(changes)[0] \
            .low_confidence_qualifier is True


class TestRootSummaries:
    def test_a_summary_is_built_per_causal_event(self):
        summaries = build_root_summaries([
            event(
                envelope_id="env-1", event_class="capacity_changed",
                occurred_at=at(0), shared_resource_ids=[PLATFORM],
                causal_event_id=INCIDENT,
            ),
        ])
        assert INCIDENT in summaries
        assert "Capacity changed" in summaries[INCIDENT]
        assert PLATFORM in summaries[INCIDENT]

    def test_events_sharing_a_cause_share_a_summary(self):
        summaries = build_root_summaries([
            event(
                envelope_id="env-1", event_class="capacity_changed",
                causal_event_id=INCIDENT,
            ),
            event(
                envelope_id="env-2", event_class="kpi_observation",
                causal_event_id=INCIDENT,
            ),
        ])
        assert len(summaries) == 1


class TestPerProjectView:
    def test_changes_can_be_filtered_to_one_project(self):
        bundle = bundle_causally_related_changes(_incident_changes())[0]
        changes = changes_for_project(bundle=bundle, project_id=PROJECT_C)
        assert changes
        assert all(PROJECT_C in c.affected_project_ids for c in changes)

    def test_an_unaffected_project_has_no_changes(self):
        bundle = bundle_causally_related_changes(_incident_changes())[0]
        assert changes_for_project(bundle=bundle, project_id=PROJECT_D) == []


class TestAncestry:
    def test_a_child_knows_its_parents(self):
        changes = _incident_changes()
        priority = changes[-1]
        ancestry = causal_ancestry(change_id=priority.id, changes=changes)
        assert ancestry
        assert changes[2].id in ancestry

    def test_a_root_change_has_no_ancestry(self):
        changes = _incident_changes()
        assert causal_ancestry(change_id=changes[0].id, changes=changes) == []

    def test_an_unknown_change_has_no_ancestry(self):
        assert causal_ancestry(
            change_id="nope", changes=_incident_changes(),
        ) == []


class TestExplainability:
    def _explain(self):
        changes = _incident_changes()
        bundles = bundle_causally_related_changes(
            changes, root_summaries={INCIDENT: "Platform capacity fell."},
        )
        return explain_runtime_change(
            changes[0].id,
            changes=changes,
            bundles=bundles,
            root_summaries={INCIDENT: "Platform capacity fell."},
        )

    def test_it_says_why_the_recompute_happened(self):
        explanation = self._explain()
        assert explanation["found"] is True
        assert explanation["causal_event_id"] == INCIDENT
        assert "Platform capacity fell." == explanation["root_summary"]

    def test_it_names_the_dependency_edges(self):
        assert self._explain()["dependency_edges"] == [
            "workstream_state -> scenario_lattice",
        ]

    def test_it_names_which_artifact_changed(self):
        explanation = self._explain()
        assert explanation["artifact_type"] == "scenario_lattice"
        assert explanation["revision_id"] == "revision-1"

    def test_it_says_why_it_was_material(self):
        explanation = self._explain()
        assert explanation["material"] is True
        assert explanation["materiality_reasons"]
        assert explanation["materiality_explanation"]

    def test_it_says_who_was_asked_and_why(self):
        explanation = self._explain()
        assert "human_attention_required" in explanation
        assert explanation["affected_project_ids"] == [PROJECT_A]

    def test_it_links_back_to_its_bundle(self):
        assert self._explain()["bundle_id"] is not None

    def test_an_unknown_change_is_reported_honestly(self):
        explanation = explain_runtime_change(
            "nope", changes=_incident_changes(),
        )
        assert explanation["found"] is False
        assert "No such runtime change" in explanation["explanation"]

    def test_the_prose_explanation_reads_as_a_chain(self):
        explanation = self._explain()
        assert "was recomputed because of" in explanation["explanation"]
        assert "propagating via" in explanation["explanation"]


class TestAttentionRouting:
    def _bundle(self, changes):
        return bundle_causally_related_changes(changes)[0]

    def test_no_material_change_stays_silent(self):
        bundle = self._bundle([_change(A.WORKSTREAM_STATE, material=False)])
        assert determine_attention_action(bundle=bundle) == (
            RuntimeAttentionAction.SILENT
        )

    def test_a_material_change_nobody_must_act_on_updates_state(self):
        bundle = self._bundle([_change(A.LAUNCH_READINESS)])
        assert determine_attention_action(bundle=bundle) == (
            RuntimeAttentionAction.UPDATE_INTERNAL_STATE
        )

    def test_a_severe_risk_raises_a_high_priority_alert(self):
        bundle = self._bundle([_change(A.PORTFOLIO_BOTTLENECKS)])
        assert determine_attention_action(bundle=bundle) == (
            RuntimeAttentionAction.HIGH_PRIORITY_ALERT
        )

    def test_a_decision_requirement_routes_a_decision_request(self):
        bundle = self._bundle([
            _change(A.DECISION_RECOMMENDATION, decision_required=True),
        ])
        assert determine_attention_action(bundle=bundle) == (
            RuntimeAttentionAction.DECISION_REQUEST
        )

    def test_an_ordinary_material_change_goes_async(self):
        bundle = self._bundle([_change(A.DECISION_RECOMMENDATION)])
        assert determine_attention_action(bundle=bundle) == (
            RuntimeAttentionAction.ASYNC_INFORMATION
        )

    def test_severity_outranks_the_decision_request(self):
        bundle = self._bundle(_incident_changes())
        assert determine_attention_action(bundle=bundle) == (
            RuntimeAttentionAction.HIGH_PRIORITY_ALERT
        )
        assert bundle.decision_required is True, (
            "the decision stays visible inside the bundle rather than "
            "becoming a second alert"
        )


class TestMeetingsAreExpensive:
    def test_a_stated_decision_does_not_warrant_a_meeting(self):
        bundle = bundle_causally_related_changes([
            _change(A.DECISION_RECOMMENDATION, decision_required=True),
        ])[0]
        warranted, reason = meeting_is_warranted(bundle=bundle)
        assert warranted is False
        assert "async decision request is sufficient" in reason

    def test_genuine_disagreement_does(self):
        bundle = bundle_causally_related_changes([
            _change(A.DECISION_RECOMMENDATION, decision_required=True),
        ])[0]
        warranted, reason = meeting_is_warranted(
            bundle=bundle, unresolved_disagreement=True,
        )
        assert warranted is True
        assert "conflict" in reason

    def test_ambiguous_authority_does(self):
        bundle = bundle_causally_related_changes([
            _change(A.DECISION_RECOMMENDATION),
        ])[0]
        warranted, reason = meeting_is_warranted(
            bundle=bundle, authority_ambiguous=True,
        )
        assert warranted is True
        assert "who holds the authority" in reason

    def test_a_routine_update_does_not(self):
        bundle = bundle_causally_related_changes([
            _change(A.LAUNCH_READINESS),
        ])[0]
        warranted, _ = meeting_is_warranted(bundle=bundle)
        assert warranted is False


class TestNoActionIsExplained:
    def test_silence_carries_its_reason(self):
        described = describe_no_action([NoActionReason.NON_MATERIAL])
        assert "No human action is required" in described
        assert "too small to act on" in described

    def test_several_reasons_are_all_stated(self):
        described = describe_no_action([
            NoActionReason.DUPLICATE, NoActionReason.ALREADY_RESOLVED,
        ])
        assert "already been processed" in described
        assert "already resolved" in described

    def test_bare_silence_still_says_something(self):
        assert "No human action is required" in describe_no_action([])
