"""Decision capsules and context packets."""

from __future__ import annotations

from datetime import timedelta

from tpllm.coordination import (
    ActorRelationType,
    DecisionPosition,
    PositionType,
    WorkstreamKeyDate,
    WorkstreamType,
    build_context_packet,
    build_decision_capsule,
    build_workstream_state,
    detect_material_changes,
)
from tpllm.intelligence import EvidenceReference, EvidenceSourceType, ProjectEvidence
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


def _position(pid, actor_ref, position_type, statement):
    return DecisionPosition(
        id=pid, decision_id="dec-1", actor=actor_ref,
        position_type=position_type, statement=statement,
        observed_at=AS_OF - timedelta(days=1),
        evidence_ids=[f"ev-{pid}"],
    )


def _evidence(eid, content, days_ago):
    reference = EvidenceReference(
        id=f"ref-{eid}", project_id=PROJECT,
        source_type=EvidenceSourceType.TICKET, source_id=eid,
        observed_at=AS_OF - timedelta(days=days_ago), ingested_at=AS_OF,
        reliability=0.8, relevance=0.8,
    )
    return ProjectEvidence(
        id=eid, project_id=PROJECT, content=content, source=reference,
        normalized_at=AS_OF,
    )


def _workstream(**overrides):
    defaults = dict(
        workstream_id=WS,
        project_id=PROJECT,
        vertical="Payments",
        feature_or_scope="Account Graph Migration",
        workstream_type=WorkstreamType.MIGRATION,
        objective=objective(),
        key_dates=[
            WorkstreamKeyDate(
                id="kd-freeze", workstream_id=WS, name="integration freeze",
                date=AS_OF + timedelta(days=14), date_type="freeze",
                hard_commitment=True, evidence_ids=["ev-freeze"],
            )
        ],
        understanding=understanding(),
        readiness=None,
        decisions=[decision("dec-1", decided_days_ago=5)],
        action_requests=[request("req-1")],
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


def _capsule():
    positions = [
        _position("p-pay", actor("dana", role="Payments Integration Lead",
                                 team="payments"),
                  PositionType.SUPPORT, "Ready for v3."),
        _position("p-plat", actor("priya",
                                  relations=[ActorRelationType.MAY_APPROVE,
                                             ActorRelationType.OWNS_SYSTEM]),
                  PositionType.CONDITIONAL,
                  "v3 preferred if prepaid compatibility passes."),
        _position("p-mob", actor("kim", role="Mobile Lead", team="mobile"),
                  PositionType.CONCERN,
                  "Mobile expects v2 during phase one."),
    ]
    authority = actor(
        relations=[ActorRelationType.MAY_APPROVE,
                   ActorRelationType.OWNS_SYSTEM],
    )
    req = request("req-1", preferred_recipient=authority)
    decisions = [decision("dec-1", decided_days_ago=5)]
    evidence = [
        _evidence("ev-prepaid", "Prepaid compatibility validation complete",
                  days_ago=2),
        _evidence("ev-old", "Original contract discussion", days_ago=10),
    ]
    return build_decision_capsule(
        request=req,
        workstream=_workstream(decisions=decisions),
        decisions=decisions,
        positions=positions,
        understanding=understanding(),
        readiness=None,
        evidence=evidence,
        as_of=AS_OF,
    )


class TestDecisionCapsule:
    def test_capsule_contains_requester_role(self):
        capsule = _capsule()
        assert capsule.requested_by.role == "Payments Integration Lead"
        assert capsule.requested_by.team_id == "payments"

    def test_capsule_contains_decision_authority(self):
        capsule = _capsule()
        assert capsule.decision_authority is not None
        assert capsule.decision_authority.actor_id == "priya"

    def test_capsule_explains_authority_relevance(self):
        capsule = _capsule()
        assert "owns the affected system" in capsule.why_this_person_or_role
        assert "approval authority" in capsule.why_this_person_or_role

    def test_capsule_includes_latest_conclusive_decision(self):
        capsule = _capsule()
        assert capsule.latest_conclusive_decision == (
            "Contract v3 preferred, pending prepaid validation"
        )
        assert capsule.last_relevant_discussion_at is not None

    def test_capsule_includes_current_positions(self):
        capsule = _capsule()
        assert len(capsule.actor_positions) == 3
        statements = [p.statement for p in capsule.actor_positions]
        assert "Mobile expects v2 during phase one." in statements

    def test_capsule_includes_what_changed(self):
        capsule = _capsule()
        # Only evidence after the last conclusive decision (5 days ago).
        assert capsule.what_changed_since_last_discussion == [
            "Prepaid compatibility validation complete"
        ]

    def test_capsule_includes_delay_impact(self):
        capsule = _capsule()
        assert capsule.impact_if_delayed
        assert "blocked" in capsule.impact_if_delayed.lower()

    def test_capsule_includes_kpi_and_milestone_links(self):
        capsule = _capsule()
        assert "kpi-contract-validation" in capsule.affected_kpi_ids
        assert "integration freeze" in capsule.affected_milestones

    def test_capsule_surfaces_disagreements(self):
        capsule = _capsule()
        assert any("v2" in d for d in capsule.open_disagreements)
        assert capsule.evidence_ids


class TestContextPacket:
    def test_packet_compresses_workstream_reality(self):
        decisions = [decision("dec-1", decided_days_ago=5)]
        workstream = _workstream(decisions=decisions)
        previous = _workstream(
            decisions=decisions, action_requests=[],
        )
        changes = detect_material_changes(
            previous_workstreams=[previous],
            current_workstreams=[workstream],
            previous_decisions=[], current_decisions=[], as_of=AS_OF,
        )
        packet = build_context_packet(
            packet_id="pkt-1",
            topic="Billing contract v3",
            workstream=workstream,
            decisions=decisions,
            positions=[],
            material_changes=changes,
            unresolved_questions=["Does scoped approval affect mobile v2?"],
        )
        assert packet.latest_conclusive_decision == (
            "Contract v3 preferred, pending prepaid validation"
        )
        assert packet.objective_summary == (
            "Move authorization onto the shared account graph"
        )
        assert packet.product_value_summary == (
            "Single authorization layer for all clients"
        )
        assert packet.unresolved_questions
        assert packet.evidence_ids
