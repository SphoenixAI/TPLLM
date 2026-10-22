"""Exception-driven meetings."""

from __future__ import annotations

from tpllm.coordination import (
    ActionRequestStatus,
    ActionRequestType,
    DiscussionNeed,
    MaterialChangeType,
    OrganizationalDecisionStatus,
    WorkstreamType,
    build_exception_driven_meeting_agenda,
    build_workstream_state,
    detect_material_changes,
    determine_discussion_need,
)
from tests._coordination_fixtures import (
    AS_OF,
    PROJECT,
    WS,
    actor,
    decision,
    objective,
    request,
    signal,
    understanding,
)


def _state(workstream_id=WS, feature="Account Graph Migration", **overrides):
    defaults = dict(
        workstream_id=workstream_id,
        project_id=PROJECT,
        vertical="Payments",
        feature_or_scope=feature,
        workstream_type=WorkstreamType.MIGRATION,
        objective=objective(workstream_id=workstream_id, id=f"obj-{workstream_id}"),
        key_dates=[],
        understanding=understanding(),
        readiness=None,
        decisions=[],
        action_requests=[],
        commitments=[],
        structural_signals=[],
        kpi_ids=[],
        wins=[],
        primary_contact=actor(),
        previous_state=None,
        as_of=AS_OF,
    )
    defaults.update(overrides)
    return build_workstream_state(**defaults)


class TestMaterialChanges:
    def test_health_transition_detected(self):
        previous = _state()
        current = _state(
            previous_state=previous,
            structural_signals=[signal("integration_failure"),
                                signal("kpi_breach")],
        )
        changes = detect_material_changes(
            previous_workstreams=[previous],
            current_workstreams=[current],
            previous_decisions=[],
            current_decisions=[],
            as_of=AS_OF,
        )
        assert any(
            c.change_type == MaterialChangeType.STATUS_CHANGE for c in changes
        )
        status = next(
            c for c in changes
            if c.change_type == MaterialChangeType.STATUS_CHANGE
        )
        assert status.before_summary == "on_track"
        assert status.severity == "high"

    def test_new_blocker_and_resolution_detected(self):
        blocked_request = request("r-block", status=ActionRequestStatus.BLOCKED)
        previous = _state()
        current = _state(action_requests=[blocked_request])
        changes = detect_material_changes(
            previous_workstreams=[previous],
            current_workstreams=[current],
            previous_decisions=[],
            current_decisions=[],
            as_of=AS_OF,
        )
        assert any(
            c.change_type == MaterialChangeType.NEW_BLOCKER for c in changes
        )
        # And the reverse direction: blocker resolved.
        back = detect_material_changes(
            previous_workstreams=[current],
            current_workstreams=[previous],
            previous_decisions=[],
            current_decisions=[],
            as_of=AS_OF,
        )
        assert any(
            c.change_type == MaterialChangeType.BLOCKER_RESOLVED for c in back
        )

    def test_new_conclusive_decision_detected(self):
        tentative = decision(
            "d1", status=OrganizationalDecisionStatus.TENTATIVE,
        )
        concluded = decision("d1")
        changes = detect_material_changes(
            previous_workstreams=[],
            current_workstreams=[],
            previous_decisions=[tentative],
            current_decisions=[concluded],
            as_of=AS_OF,
        )
        assert len(changes) == 1
        assert changes[0].change_type == MaterialChangeType.DECISION_CHANGE
        assert "Decided" in changes[0].after_summary


class TestDiscussionNeed:
    def test_no_change_no_discussion(self):
        need = determine_discussion_need(
            workstream=_state(),
            material_changes=[],
            open_requests=[],
            unresolved_decisions=[],
        )
        assert need == DiscussionNeed.NO_DISCUSSION

    def test_new_blocker_gets_discussion(self):
        previous = _state()
        current = _state(
            action_requests=[request("r-b", status=ActionRequestStatus.BLOCKED,
                                     request_type=ActionRequestType.SUPPORT)],
        )
        changes = detect_material_changes(
            previous_workstreams=[previous], current_workstreams=[current],
            previous_decisions=[], current_decisions=[], as_of=AS_OF,
        )
        need = determine_discussion_need(
            workstream=current,
            material_changes=changes,
            open_requests=[],
            unresolved_decisions=[],
        )
        assert need == DiscussionNeed.DISCUSSION

    def test_blocking_authority_decision_required(self):
        need = determine_discussion_need(
            workstream=_state(),
            material_changes=[],
            open_requests=[request("r-appr",
                                   request_type=ActionRequestType.APPROVAL)],
            unresolved_decisions=[],
        )
        assert need == DiscussionNeed.DECISION_REQUIRED

    def test_async_only_change(self):
        previous = _state(structural_signals=[signal("new_blocker")])
        current = _state(previous_state=previous)  # improved back to on_track
        changes = detect_material_changes(
            previous_workstreams=[previous], current_workstreams=[current],
            previous_decisions=[], current_decisions=[], as_of=AS_OF,
        )
        need = determine_discussion_need(
            workstream=current,
            material_changes=changes,
            open_requests=[],
            unresolved_decisions=[],
        )
        assert need == DiscussionNeed.ASYNC_UPDATE


class TestAgenda:
    def _agenda(self):
        quiet = _state("ws-aaa-search", feature="Search Migration")
        decisive = _state(
            "ws-zzz-billing", feature="Billing Contract",
            action_requests=[
                request("r-appr", workstream_id="ws-zzz-billing"),
            ],
        )
        improved_prev = _state("ws-mmm-report",
                               structural_signals=[signal("new_blocker")])
        improved = _state(
            "ws-mmm-report", feature="Reporting API",
            previous_state=improved_prev,
        )
        changes = detect_material_changes(
            previous_workstreams=[improved_prev],
            current_workstreams=[improved],
            previous_decisions=[], current_decisions=[], as_of=AS_OF,
        )
        return build_exception_driven_meeting_agenda(
            project_id=PROJECT,
            workstreams=[quiet, decisive, improved],
            material_changes=changes,
            open_requests=[request("r-appr", workstream_id="ws-zzz-billing")],
            decisions=[],
            as_of=AS_OF,
        )

    def test_agenda_buckets(self):
        agenda = self._agenda()
        assert "ws-aaa-search" in agenda.no_discussion_workstream_ids
        assert "ws-mmm-report" in agenda.async_update_workstream_ids
        assert [c.workstream_id for c in agenda.decision_required_candidates] \
            == ["ws-zzz-billing"]

    def test_decision_impact_beats_alphabetical(self):
        agenda = self._agenda()
        # zzz-billing sorts last alphabetically but leads the agenda.
        assert agenda.recommended_order[0] == "cand-ws-zzz-billing"

    def test_no_round_robin_prompting(self):
        agenda = self._agenda()
        text = agenda.executive_summary.lower()
        for phrase in ("around the room", "any updates", "go around",
                       "team, any update"):
            assert phrase not in text
        assert "DECISION REQUIRED" in agenda.executive_summary
        assert "NO DISCUSSION" in agenda.executive_summary

    def test_agenda_deterministic(self):
        assert self._agenda().model_dump() == self._agenda().model_dump()

    def test_async_change_does_not_consume_meeting_slot(self):
        agenda = self._agenda()
        assert "ws-mmm-report" not in [
            c.workstream_id
            for c in agenda.discussion_candidates
            + agenda.decision_required_candidates
        ]
