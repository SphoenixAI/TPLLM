"""Core domain contracts."""

from __future__ import annotations

from datetime import datetime

import pytest

from tpllm.core import (
    ActorReference,
    ActorView,
    EvidenceReferenceView,
    ProjectReference,
    ProvenanceStamp,
    ReadinessSummaryView,
    ReadinessView,
    TemporalReference,
    WorkstreamReference,
    summarize_readiness,
)
from tpllm.coordination import ActorRelationType, OrganizationalActorRef
from tpllm.intelligence import (
    KPIResponsePlan,
    KPIResponseState,
    LaunchBlocker,
    LaunchBlockerSeverity,
    LaunchFeatureVector,
    LaunchForecast,
    LaunchForecastMethod,
    LaunchReadinessDimensionType,
    LaunchReadinessScorecard,
    LaunchReadinessSnapshot,
    LaunchRiskBand,
)

_AS_OF = datetime(2026, 8, 20, 12, 0, 0)


class TestActorReference:
    def test_actor_reference_validates(self):
        actor = ActorReference(
            actor_id="priya", display_name="Priya Rao",
            role="Platform Architecture Lead", team_id="platform",
            team_name="Platform", canonical_entity_id="person_priya_rao",
        )
        assert actor.actor_id == "priya"
        assert actor.canonical_entity_id == "person_priya_rao"

    def test_core_actor_carries_no_capacity(self):
        # Identity answers WHO; capacities stay in coordination.
        field_names = set(ActorReference.model_fields)
        for capacity in ("relation_types", "may_approve", "may_decide",
                         "owns_system", "has_expertise", "implements"):
            assert capacity not in field_names

    def test_organizational_actor_compatibility_preserved(self):
        actor = OrganizationalActorRef(
            actor_id="priya", role="Platform Architecture Lead",
            team_id="platform", team_name="Platform",
            relation_types=[ActorRelationType.MAY_APPROVE],
            evidence_ids=["ev-1"],
        )
        # Still an OrganizationalActorRef with all its original fields...
        assert actor.relation_types == [ActorRelationType.MAY_APPROVE]
        assert actor.evidence_ids == ["ev-1"]
        # ...and now also satisfies the core contract.
        assert isinstance(actor, ActorReference)
        assert isinstance(actor, ActorView)

    def test_subclass_instance_survives_core_annotated_field(self):
        # A coordination actor passed into an intelligence model keeps
        # its capacities — the inversion loses no data.
        actor = OrganizationalActorRef(
            actor_id="priya", team_name="Platform",
            relation_types=[ActorRelationType.MAY_DECIDE],
        )
        plan = KPIResponsePlan(
            id="p1", kpi_id="kpi-1", state=KPIResponseState.REMEDIATING,
            measurement_owner=actor, remediation_owner=actor,
            decision_owner=actor, investigation_summary=None,
            remediation_summary=None, last_updated_at=_AS_OF,
        )
        stored = plan.remediation_owner
        assert isinstance(stored, OrganizationalActorRef)
        assert stored.relation_types == [ActorRelationType.MAY_DECIDE]
        assert stored.team_name == "Platform"


class TestReferences:
    def test_lightweight_references(self):
        assert ProjectReference(project_id="atlas").project_name is None
        assert WorkstreamReference(
            workstream_id="ws-1", project_id="atlas",
        ).project_id == "atlas"
        assert EvidenceReferenceView(
            evidence_id="ev-1", source_system="slack",
            observed_at=_AS_OF,
        ).observed_at == _AS_OF
        assert TemporalReference(as_of=_AS_OF).valid_until is None

    def test_provenance_stamp(self):
        stamp = ProvenanceStamp(
            source_system="jira", source_id="JIRA-1",
            observed_at=_AS_OF, reliability=0.8, relevance=0.7,
            evidence_ids=["ev-1"],
        )
        assert stamp.evidence_ids == ["ev-1"]


class TestReadinessView:
    def _snapshot(self):
        scorecard = LaunchReadinessScorecard(
            id="sc-1", project_id="atlas", launch_id="launch-1",
            as_of=_AS_OF, overall_score=61.0, overall_confidence=0.69,
            blockers=[
                LaunchBlocker(
                    id="blk-critical", project_id="atlas",
                    launch_id="launch-1", title="Billing contract unresolved",
                    description="d", severity=LaunchBlockerSeverity.CRITICAL,
                    affected_dimension=(
                        LaunchReadinessDimensionType.INTEGRATION_READINESS
                    ),
                    evidence_ids=["ev-1"],
                ),
                LaunchBlocker(
                    id="blk-resolved", project_id="atlas",
                    launch_id="launch-1", title="Old blocker",
                    description="d", severity=LaunchBlockerSeverity.CRITICAL,
                    affected_dimension=(
                        LaunchReadinessDimensionType.SCOPE_CLARITY
                    ),
                    evidence_ids=["ev-2"], resolved=True,
                ),
                LaunchBlocker(
                    id="blk-high", project_id="atlas", launch_id="launch-1",
                    title="High but not critical", description="d",
                    severity=LaunchBlockerSeverity.HIGH,
                    affected_dimension=(
                        LaunchReadinessDimensionType.SCOPE_CLARITY
                    ),
                    evidence_ids=["ev-3"],
                ),
            ],
            generated_at=_AS_OF,
        )
        fv = LaunchFeatureVector(
            project_id="atlas", launch_id="launch-1", as_of=_AS_OF,
            dimension_scores={}, dimension_confidences={},
            critical_blocker_count=1, high_blocker_count=1,
            unresolved_critical_unknown_count=0, contradiction_count=0,
        )
        forecast = LaunchForecast(
            id="fc-1", project_id="atlas", launch_id="launch-1",
            as_of=_AS_OF, method=LaunchForecastMethod.HEURISTIC_V1,
            on_time_probability=0.43, expected_slip_days=8.0,
            lower_slip_days=3.0, upper_slip_days=16.0,
            risk_band=LaunchRiskBand.HIGH, confidence=0.69,
            feature_vector=fv,
        )
        return LaunchReadinessSnapshot(
            id="lrs-1", scorecard=scorecard, forecast=forecast,
        )

    def test_summary_view_supplies_required_fields(self):
        view = summarize_readiness(self._snapshot())
        assert view.project_id == "atlas"
        assert view.overall_score == 61.0
        assert view.overall_confidence == 0.69
        # Only unresolved critical blockers.
        assert view.critical_blocker_ids == ["blk-critical"]

    def test_summary_view_satisfies_protocol(self):
        view = summarize_readiness(self._snapshot())
        assert isinstance(view, ReadinessView)
        assert isinstance(view, ReadinessSummaryView)

    def test_no_readiness_yields_none(self):
        assert summarize_readiness(None) is None
        assert summarize_readiness(object()) is None
