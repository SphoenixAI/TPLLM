"""Workstream health, board projection, executive
brief, and layer guardrails."""

from __future__ import annotations

from datetime import timedelta

from tpllm.coordination import (
    ActionRequestStatus,
    OrganizationalDecisionStatus,
    WorkstreamCommitment,
    WorkstreamHealth,
    WorkstreamType,
    build_project_executive_brief,
    build_workstream_state,
    compute_workstream_health,
    health_rank,
)
from tpllm.coordination.models import CommitmentType
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


def _health(**overrides):
    defaults = dict(
        understanding=understanding(),
        readiness=None,
        workstream_id=WS,
        decisions=[],
        action_requests=[],
        structural_signals=[],
        commitments=[],
        as_of=AS_OF,
    )
    defaults.update(overrides)
    return compute_workstream_health(**defaults)


def _state(**overrides):
    defaults = dict(
        workstream_id=WS,
        project_id=PROJECT,
        vertical="Payments",
        feature_or_scope="Account Graph Migration",
        workstream_type=WorkstreamType.MIGRATION,
        objective=objective(),
        key_dates=[],
        understanding=understanding(),
        readiness=None,
        decisions=[],
        action_requests=[],
        commitments=[],
        structural_signals=[],
        kpi_ids=["kpi-contract-validation"],
        wins=[],
        primary_contact=actor(),
        previous_state=None,
        as_of=AS_OF,
    )
    defaults.update(overrides)
    return build_workstream_state(**defaults)


class TestWorkstreamHealth:
    def test_no_signals_on_track(self):
        health, confidence, key_signal = _health()
        assert health == WorkstreamHealth.ON_TRACK
        assert "on track" in key_signal.lower()

    def test_unresolved_decision_lowers_health(self):
        baseline, _, _ = _health()
        lowered, _, key_signal = _health(
            decisions=[
                decision("d1", status=OrganizationalDecisionStatus.TENTATIVE),
            ]
        )
        assert health_rank(lowered) > health_rank(baseline)
        assert "Unresolved decision" in key_signal

    def test_resolved_blocker_improves_health(self):
        with_blocker, _, _ = _health(
            structural_signals=[signal("new_blocker", "Billing API blocked")],
        )
        with_resolution, _, _ = _health(
            structural_signals=[
                signal("new_blocker", "Billing API blocked"),
                signal("blocker_resolved", "Billing API unblocked"),
            ],
        )
        assert health_rank(with_resolution) < health_rank(with_blocker)

    def test_blocked_dependency_gives_blocked(self):
        health, _, _ = _health(
            action_requests=[
                request("r1", status=ActionRequestStatus.BLOCKED),
            ],
            structural_signals=[signal("integration_failure")],
        )
        assert health == WorkstreamHealth.BLOCKED

    def test_missed_commitment_raises_risk(self):
        missed = WorkstreamCommitment(
            id="cm-1", workstream_id=WS,
            commitment_type=CommitmentType.CUSTOMER,
            description="Customer delivery of graph authorization",
            target_date=AS_OF - timedelta(days=3),
            status="open", evidence_ids=["ev-commit"],
        )
        health, _, key_signal = _health(commitments=[missed])
        assert health_rank(health) > health_rank(WorkstreamHealth.ON_TRACK)
        assert "Missed commitment" in key_signal

    def test_no_evidence_is_unknown(self):
        bare = understanding()
        bare = bare.model_copy(update={"evidence_ids": []})
        health, confidence, _ = _health(understanding=bare)
        assert health == WorkstreamHealth.UNKNOWN
        assert confidence <= 0.3


class TestWorkstreamState:
    def test_previous_health_preserved(self):
        previous = _state()
        current = _state(
            previous_state=previous,
            structural_signals=[signal("new_blocker")],
        )
        assert current.previous_health == previous.current_health
        assert current.previous_health == WorkstreamHealth.ON_TRACK
        assert current.current_health != WorkstreamHealth.ON_TRACK

    def test_board_projection_deterministic(self):
        kwargs = dict(
            decisions=[decision("d1")],
            action_requests=[request("r1")],
            structural_signals=[signal("dependency_risk")],
        )
        assert _state(**kwargs).model_dump() == _state(**kwargs).model_dump()

    def test_projection_carries_board_fields(self):
        state = _state(decisions=[decision("d1")])
        assert state.vertical == "Payments"
        assert state.feature_or_scope == "Account Graph Migration"
        assert state.latest_conclusive_decision_id == "d1"
        assert state.primary_contact.actor_id == "priya"
        assert state.product_value == (
            "Single authorization layer for all clients"
        )
        assert state.kpi_ids == ["kpi-contract-validation"]
        assert state.evidence_ids  # traceable


class TestExecutiveBrief:
    def test_brief_separates_wins_and_risks(self):
        healthy = _state(wins=["Prepaid validation completed"])
        risky = _state(
            workstream_id="ws-billing",
            feature_or_scope="Billing Contract",
            structural_signals=[
                signal("integration_failure"), signal("kpi_breach"),
            ],
        )
        brief = build_project_executive_brief(
            workstreams=[healthy, risky],
            previous_workstreams=[],
            decisions=[
                decision("d1", status=OrganizationalDecisionStatus.TENTATIVE),
            ],
            requests=[request("r1", status=ActionRequestStatus.BLOCKED)],
            as_of=AS_OF,
        )
        assert "Prepaid validation completed" in brief.wins
        assert "ws-billing" in brief.high_risk_workstream_ids
        assert WS in brief.low_risk_workstream_ids
        assert brief.decisions_required
        assert "r1" in brief.critical_action_request_ids
        assert brief.evidence_ids

    def test_brief_reports_health_transitions(self):
        previous = _state()
        current = _state(
            previous_state=previous,
            structural_signals=[
                signal("integration_failure"), signal("kpi_breach"),
            ],
        )
        brief = build_project_executive_brief(
            workstreams=[current],
            previous_workstreams=[previous],
            decisions=[],
            requests=[],
            as_of=AS_OF,
        )
        assert brief.major_changes_since_previous
        assert "on_track" in brief.major_changes_since_previous[0]


class TestGuardrails:
    def test_no_worker_performance_vocabulary(self):
        import inspect
        import tpllm.coordination.models as models
        import tpllm.coordination.workstreams as workstreams
        import tpllm.coordination.projections as projections
        import tpllm.coordination.decisions as decisions_mod
        import tpllm.coordination.requests as requests_mod

        for mod in (models, workstreams, projections, decisions_mod, requests_mod):
            source = inspect.getsource(mod).lower()
            for pattern in (
                "productivity",
                "responsiveness",
                "employee_rank",
                "sentiment_score",
                "message_count",
                "individual_score",
            ):
                assert pattern not in source, (
                    f"{mod.__name__} must not contain '{pattern}' — the "
                    f"coordination layer models structures, not people."
                )

    def test_no_application_authority_imports(self):
        import inspect
        import tpllm.coordination.models as models
        import tpllm.coordination.workstreams as workstreams
        import tpllm.coordination.projections as projections

        for mod in (models, workstreams, projections):
            source = inspect.getsource(mod)
            for pattern in (
                "tpllm.application",
                "apply_calibration",
                "register_active_calibration",
            ):
                assert pattern not in source, (
                    f"{mod.__name__} must not reference '{pattern}'."
                )
