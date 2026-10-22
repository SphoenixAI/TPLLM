"""KPI stewardship (response state, not people)."""

from __future__ import annotations

from lltpm.coordination import ActorRelationType, OrganizationalActorRef
from lltpm.intelligence import (
    KPIHealthStatus,
    KPIMilestoneLink,
    KPIResponsePlan,
    KPIResponseState,
    assess_kpi_health,
    assess_kpi_stewardship,
    build_kpi_resolution_packet,
)
from tests._kpi_fixtures import AS_OF, definition, observation, target


def _actor(actor_id, team, role):
    return OrganizationalActorRef(
        actor_id=actor_id, display_name=actor_id.title(), role=role,
        team_id=team, team_name=team.replace("-", " ").title(),
        relation_types=[ActorRelationType.MAY_DECIDE],
    )


def _trend(value=61.0):
    return assess_kpi_health(
        definition=definition(),
        target=target(),
        observations=[observation(value=value, days_ago=1)],
        profile=None,
        as_of=AS_OF,
    )


def _plan(state=KPIResponseState.REMEDIATING, **overrides):
    defaults = dict(
        id="plan-1",
        kpi_id="kpi-activation",
        state=state,
        measurement_owner=_actor("mia", "data-platform", "Analytics Lead"),
        remediation_owner=_actor(
            "ivan", "identity-infrastructure", "Infra Lead",
        ),
        decision_owner=_actor(
            "priya", "platform-architecture", "Platform Architecture Lead",
        ),
        investigation_summary="Auth timeout spike traced to contract path.",
        remediation_summary="Contract approval, owned by Platform Architecture",
        blocker_ids=[],
        decision_request_ids=[],
        evidence_ids=["ev-plan"],
        last_updated_at=AS_OF,
    )
    defaults.update(overrides)
    return KPIResponsePlan(**defaults)


class TestStewardship:
    def test_owners_are_distinct_roles(self):
        plan = _plan()
        assert plan.measurement_owner.team_id == "data-platform"
        assert plan.remediation_owner.team_id == "identity-infrastructure"
        assert plan.decision_owner.team_id == "platform-architecture"

    def test_waiting_on_dependency_represented(self):
        assessment = assess_kpi_stewardship(
            definition=definition(),
            trend=_trend(),
            plan=_plan(
                state=KPIResponseState.WAITING_ON_DEPENDENCY,
                blocker_ids=["dec-mobile-contract"],
            ),
            as_of=AS_OF,
        )
        assert assessment.blocked_by_dependency is True
        assert assessment.blocker_ids == ["dec-mobile-contract"]
        assert assessment.next_decision_owner.team_id == (
            "platform-architecture"
        )
        assert "blocked on a dependency" in assessment.explanation

    def test_structural_narrative_not_blame(self):
        assessment = assess_kpi_stewardship(
            definition=definition(),
            trend=_trend(),
            plan=_plan(),
            as_of=AS_OF,
        )
        text = assessment.explanation.lower()
        assert "acknowledged" in text
        assert "remediating" in text
        for judgment in ("failing to", "unresponsive", "not taking care"):
            assert judgment not in text

    def test_unacknowledged_breach_names_next_action(self):
        assessment = assess_kpi_stewardship(
            definition=definition(),
            trend=_trend(),
            plan=None,
            as_of=AS_OF,
        )
        assert assessment.acknowledged is False
        assert assessment.response_state == KPIResponseState.UNACKNOWLEDGED
        assert "Acknowledge" in assessment.next_required_action

    def test_no_person_score_fields(self):
        from lltpm.intelligence.kpi_stewardship import (
            KPIStewardshipAssessment,
        )
        field_names = set(KPIStewardshipAssessment.model_fields)
        assert not any("score" in n for n in field_names)
        assert not any("rank" in n for n in field_names)

    def test_no_worker_performance_vocabulary(self):
        import inspect
        import lltpm.intelligence.kpi_stewardship as mod

        source = inspect.getsource(mod).lower()
        for pattern in ("productivity", "responsiveness", "employee_rank",
                        "sentiment_score"):
            assert pattern not in source


class TestResolutionPacket:
    def test_packet_identifies_next_action(self):
        trend = _trend()
        assessment = assess_kpi_stewardship(
            definition=definition(),
            trend=trend,
            plan=_plan(state=KPIResponseState.WAITING_ON_DEPENDENCY,
                       blocker_ids=["dec-mobile-contract"]),
            as_of=AS_OF,
        )
        packet = build_kpi_resolution_packet(
            definition=definition(),
            trend=trend,
            assessment=assessment,
            target_summary="≥ 70% before beta",
            related_workstream_ids=["ws-account-graph"],
            milestone_links=[
                KPIMilestoneLink(
                    id="ml-1", kpi_id="kpi-activation",
                    milestone_id="milestone-beta", importance="critical",
                    launch_blocking=True, required_target_id="tgt-1",
                    explanation="Activation gates beta.",
                    evidence_ids=["ev-launch-doc"],
                )
            ],
            probable_contributing_signals=[
                "Mobile onboarding regression (hypothesis)",
            ],
            decision_capsule_id="capsule-req-9",
        )
        assert packet.current_health == KPIHealthStatus.BREACHED
        assert packet.next_required_action is not None
        assert packet.decision_capsule_id == "capsule-req-9"
        assert packet.related_milestone_ids == ["milestone-beta"]
        assert packet.why_it_matters.startswith("Beta gate")
        assert packet.evidence_ids
