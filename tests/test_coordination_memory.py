"""Coordination traces, problem signatures, and
audience-aware context briefs."""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.coordination import (
    DecisionPosition,
    PositionType,
    WorkstreamType,
    build_workstream_state,
)
from lltpm.memory import (
    CoordinationEventType,
    CoordinationTrace,
    CoordinationTraceEvent,
    append_trace_event,
    build_organizational_context_brief,
    build_problem_signature,
    complete_trace,
)
from tests._coordination_fixtures import (
    AS_OF,
    PROJECT,
    WS,
    actor,
    decision,
    objective,
    request,
    understanding,
)


def _workstream(**overrides):
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
        decisions=[decision("dec-1", decided_days_ago=5)],
        action_requests=[request("req-1")],
        commitments=[],
        structural_signals=[],
        kpi_ids=["kpi-activation"],
        wins=[],
        primary_contact=actor(),
        previous_state=None,
        as_of=AS_OF,
    )
    defaults.update(overrides)
    return build_workstream_state(**defaults)


def _event(eid, event_type, description="Ask routed to Platform Architecture"):
    return CoordinationTraceEvent(
        id=eid, trace_id="trace-1", event_type=event_type,
        occurred_at=AS_OF, description=description,
        actor_refs=[actor()], evidence_ids=[f"ev-{eid}"],
    )


def _trace():
    return CoordinationTrace(
        id="trace-1", project_id=PROJECT,
        problem_signature="request:approval|cross_team",
        initial_problem="Contract v3 approval unresolved across teams",
        created_at=AS_OF,
    )


class TestCoordinationTraces:
    def test_trace_contains_observable_events_only(self):
        # Events describe occurrences; the model has no hidden-reasoning
        # fields at all.
        field_names = set(CoordinationTraceEvent.model_fields)
        for forbidden in ("reasoning", "thought", "chain_of_thought"):
            assert forbidden not in field_names
        with pytest.raises(ValueError, match="observable"):
            _event("e-bad", CoordinationEventType.ASK_ROUTED, description="  ")

    def test_trace_preserves_evidence(self):
        trace = _trace()
        trace = append_trace_event(
            trace=trace,
            event=_event("e1", CoordinationEventType.PROBLEM_DETECTED),
        )
        trace = append_trace_event(
            trace=trace,
            event=_event("e2", CoordinationEventType.DECISION_MADE,
                         "Contract v3 approved for enterprise accounts"),
        )
        done = complete_trace(
            trace=trace,
            final_outcome="Three downstream integrations unblocked",
            successful=True,
            outcome_evidence_ids=["ev-outcome"],
        )
        assert [e.evidence_ids for e in done.events] == [["ev-e1"], ["ev-e2"]]
        assert done.outcome_evidence_ids == ["ev-outcome"]
        assert done.successful is True
        # Original trace untouched (append-only by copy).
        assert _trace().events == []

    def test_outcome_requires_evidence(self):
        with pytest.raises(ValueError, match="evidence"):
            complete_trace(
                trace=_trace(), final_outcome="done", successful=True,
                outcome_evidence_ids=[],
            )


class TestProblemSignature:
    def _signature(self, **overrides):
        defaults = dict(
            workstream=_workstream(),
            request=request("req-1"),
            unresolved_decisions=[],
            kpi_states=[],
            structural_signals=[],
        )
        defaults.update(overrides)
        return build_problem_signature(**defaults)

    def test_signature_describes_structure_not_people(self):
        distinctive = actor(
            "zephyrina-quill", role="Lead", team="payments",
        )
        signature = self._signature(
            request=request("req-1", requested_by=distinctive),
        )
        assert "zephyrina" not in signature.lower()
        assert "quill" not in signature.lower()
        assert "request:approval" in signature
        assert "needs_authority:may_approve" in signature

    def test_signature_captures_cross_team_and_gaps(self):
        cross = request(
            "req-1",
            requested_by=actor("dana", team="payments"),
            preferred_recipient=actor("priya", team="platform-architecture"),
        )
        signature = self._signature(request=cross)
        assert "cross_team" in signature
        gap = self._signature(
            request=request("req-1", preferred_recipient=None),
        )
        assert "authority_gap" in gap

    def test_signature_deterministic(self):
        assert self._signature() == self._signature()


class TestContextBriefs:
    def _brief(self, audience_role):
        positions = [
            DecisionPosition(
                id="p-1", decision_id="dec-1",
                actor=actor("kim", role="Mobile Lead", team="mobile"),
                position_type=PositionType.CONCERN,
                statement="Mobile expects v2 during phase one.",
                observed_at=AS_OF - timedelta(days=1),
                evidence_ids=["ev-p1"],
            )
        ]
        return build_organizational_context_brief(
            audience_role=audience_role,
            workstream=_workstream(),
            decisions=[decision("dec-1", decided_days_ago=5)],
            positions=positions,
            request=request("req-1", preferred_recipient=actor()),
            understanding=understanding(),
            kpi_summaries=["Activation 61% vs 70% target"],
            consequences_of_delay=["Three integrations stay blocked"],
            last_meeting_at=AS_OF - timedelta(days=2),
            last_meeting_summary="Prepaid validation reviewed.",
        )

    def test_brief_preserves_latest_conclusive_decision(self):
        brief = self._brief("director")
        assert brief.latest_conclusive_decision == (
            "Contract v3 preferred, pending prepaid validation"
        )

    def test_brief_preserves_positions_and_roles(self):
        brief = self._brief("executive")
        assert any("Mobile Lead" in p for p in brief.current_team_positions)
        assert brief.requester_role == "Payments Integration Lead"
        assert brief.decision_authority_role == "Platform Architecture Lead"

    def test_audience_changes_emphasis_not_facts(self):
        engineer = self._brief("engineer")
        executive = self._brief("executive")
        # Facts identical.
        assert engineer.latest_conclusive_decision == (
            executive.latest_conclusive_decision
        )
        assert engineer.current_team_positions == (
            executive.current_team_positions
        )
        assert engineer.product_value == executive.product_value
        # Emphasis differs: the engineer brief may carry deeper technical
        # detail; the executive brief stays compressed.
        assert len(engineer.current_state) >= len(executive.current_state)

    def test_engineer_brief_includes_mechanism_when_known(self):
        deep = understanding().model_copy(
            update={
                "technical_mechanism": understanding().technical_mechanism.
                model_copy(update={
                    "mechanism_summary": "billing → graph → clients",
                })
            }
        )
        positions: list[DecisionPosition] = []
        brief = build_organizational_context_brief(
            audience_role="engineer",
            workstream=_workstream(),
            decisions=[],
            positions=positions,
            understanding=deep,
        )
        assert "billing → graph → clients" in brief.current_state


class TestGuardrails:
    def test_no_automatic_messages_sent(self):
        import inspect
        import lltpm.memory.coordination_traces as traces
        import lltpm.memory.pattern_matching as matching
        import lltpm.memory.resolution_patterns as patterns
        import lltpm.memory.context_briefs as briefs

        for mod in (traces, matching, patterns, briefs):
            source = inspect.getsource(mod)
            for pattern in ("send_message", "post_message", "requests.post",
                            "slack", "webhook"):
                assert pattern not in source.lower(), (
                    f"{mod.__name__} must not contain '{pattern}' — "
                    f"memory is advisory and never sends anything."
                )

    def test_no_application_authority_imports(self):
        import inspect
        import lltpm.memory.coordination_traces as traces
        import lltpm.memory.pattern_matching as matching
        import lltpm.memory.resolution_patterns as patterns
        import lltpm.memory.context_briefs as briefs

        for mod in (traces, matching, patterns, briefs):
            source = inspect.getsource(mod)
            for pattern in ("lltpm.application", "apply_calibration",
                            "register_active_calibration"):
                assert pattern not in source
