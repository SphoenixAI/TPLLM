"""Tests for the Palantir-aligned ontology layer.

Covers:
1. Core primitives — SourceRef, AIExtractedValue, EvidenceClaim, Contradiction
2. Interfaces — Trackable, Assignable, Schedulable, EvidenceBacked, Escalatable, SourceMapped, Reportable
3. Canonical entities — WorkItem, Decision, Issue, Risk, etc.
4. Interface composition — entities correctly inherit interface fields
5. Actions — business operation types
6. Precedence rules — source-of-truth conflict resolution
7. Mapper — integration objects → canonical ontology entities
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from tpllm.ontology.core import (
    AIExtractedValue,
    ClaimType,
    Contradiction,
    ContradictionSeverity,
    ContradictionStatus,
    EvidenceClaim,
    SourceRef,
    SourceSystem,
)
from tpllm.ontology.interfaces import (
    Assignable,
    Escalatable,
    EvidenceBacked,
    Reportable,
    Schedulable,
    SourceMapped,
    Trackable,
)
from tpllm.ontology.entities import (
    ActionItem,
    AgentRecommendation,
    Artifact,
    Decision,
    DecisionStatus,
    Deliverable,
    Dependency,
    DependencyState,
    Issue,
    IssueState,
    Meeting,
    Milestone,
    OntologyChangeProposal,
    OntologyNotification,
    Person,
    Priority,
    Program,
    Project,
    Question,
    Requirement,
    Risk,
    RiskState,
    Signal,
    SignalType,
    Sprint,
    StatusReport,
    Team,
    WorkItem,
    WorkItemStatus,
    Workstream,
)
from tpllm.ontology.actions import (
    ActionType,
    ConfirmDecisionAction,
    ConvertRiskToIssueAction,
    CreateFollowUpFromMeetingAction,
    CreateSprintPlanAction,
    EscalateBlockerAction,
    GenerateStatusReportAction,
    MarkDecisionDisputedAction,
    MergeDuplicateWorkItemsAction,
    OntologyAction,
    PromoteSignalToClaimAction,
    RequestOwnerUpdateAction,
    ResolveContradictionAction,
    ResolveDependencyAction,
    TriageIssueAction,
)
from tpllm.ontology.precedence import (
    PRECEDENCE_RULES,
    PrecedenceField,
    explain_precedence,
    get_precedence_rank,
    resolve_conflict,
)
from tpllm.ontology.mapper import (
    map_board_task,
    map_calendar_event,
    map_decision,
    map_github_pr,
    map_github_pr_to_signal,
    map_jira_ticket,
    map_linear_issue,
    map_notion_page,
    map_raid_dependency,
    map_raid_issue,
    map_raid_risk,
    map_slack_message_to_claim,
    map_slack_message_to_signal,
    map_zoom_action_item,
    map_zoom_meeting,
)


# ── 1. Core Primitives ─────────────────────────────────────────────


class TestSourceRef:
    def test_create_source_ref(self):
        ref = SourceRef(system=SourceSystem.GITHUB, external_id="PR-42")
        assert ref.system == SourceSystem.GITHUB
        assert ref.external_id == "PR-42"
        assert ref.url == ""
        assert isinstance(ref.observed_at, datetime)

    def test_all_source_systems_exist(self):
        expected = {
            "github", "jira", "linear", "slack", "zoom",
            "google_calendar", "microsoft_calendar", "notion",
            "sendgrid", "task_board", "agent", "manual",
        }
        actual = {s.value for s in SourceSystem}
        assert actual == expected


class TestAIExtractedValue:
    def test_create_extracted_value(self):
        val = AIExtractedValue(
            value="Sprint is on track",
            confidence=0.85,
            extractor="claude",
            reasoning_summary="Based on merged PRs and milestone dates",
        )
        assert val.value == "Sprint is on track"
        assert val.confidence == 0.85
        assert val.human_reviewed is False

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            AIExtractedValue(value="x", confidence=1.5)
        with pytest.raises(Exception):
            AIExtractedValue(value="x", confidence=-0.1)

    def test_with_source_refs(self):
        ref = SourceRef(system=SourceSystem.SLACK, external_id="thread-1")
        val = AIExtractedValue(
            value="blocked",
            confidence=0.6,
            source_refs=[ref],
        )
        assert len(val.source_refs) == 1
        assert val.source_refs[0].system == SourceSystem.SLACK


class TestEvidenceClaim:
    def test_create_claim(self):
        claim = EvidenceClaim(
            id="ec-001",
            claim_text="Feature X is 80% complete",
            claim_type=ClaimType.PROGRESS,
            confidence=0.7,
            subject_object_id="wi-123",
        )
        assert claim.id == "ec-001"
        assert claim.claim_type == ClaimType.PROGRESS
        assert claim.contradicted_by_claim_ids == []

    def test_claim_with_contradictions(self):
        claim = EvidenceClaim(
            id="ec-002",
            claim_text="Feature X is blocked",
            claim_type=ClaimType.BLOCKER,
            contradicted_by_claim_ids=["ec-001"],
        )
        assert len(claim.contradicted_by_claim_ids) == 1

    def test_all_claim_types(self):
        expected = {
            "status_update", "ownership", "blocker", "risk", "decision",
            "requirement", "dependency", "timeline", "scope_change",
            "commitment", "progress", "sentiment",
        }
        actual = {ct.value for ct in ClaimType}
        assert actual == expected


class TestContradiction:
    def test_create_contradiction(self):
        c = Contradiction(
            id="cx-001",
            title="Status conflict on WI-42",
            conflicting_claim_ids=["ec-001", "ec-002"],
            affected_object_ids=["wi-42"],
            severity=ContradictionSeverity.HIGH,
        )
        assert c.status == ContradictionStatus.OPEN
        assert len(c.conflicting_claim_ids) == 2
        assert c.resolved_at is None


# ── 2. Interfaces ───────────────────────────────────────────────────


class TestInterfaces:
    def test_trackable(self):
        t = Trackable(status="active")
        assert t.lifecycle_state == "active"
        assert isinstance(t.created_at, datetime)

    def test_assignable(self):
        a = Assignable(owner_id="alice", accountable_team_id="platform")
        assert a.owner_id == "alice"
        assert a.backup_owner_id == ""

    def test_schedulable_overdue(self):
        s = Schedulable(due_date=date.today() - timedelta(days=2))
        assert s.is_overdue is True
        assert s.days_until_due < 0

    def test_schedulable_not_overdue(self):
        s = Schedulable(due_date=date.today() + timedelta(days=5))
        assert s.is_overdue is False
        assert s.days_until_due == 5

    def test_schedulable_no_due_date(self):
        s = Schedulable()
        assert s.is_overdue is False
        assert s.days_until_due is None

    def test_evidence_backed(self):
        ref = SourceRef(system=SourceSystem.ZOOM, external_id="mtg-1")
        e = EvidenceBacked(
            evidence_claim_ids=["ec-1", "ec-2"],
            confidence=0.9,
            source_refs=[ref],
        )
        assert len(e.evidence_claim_ids) == 2
        assert e.confidence == 0.9

    def test_escalatable(self):
        e = Escalatable(severity="critical", escalation_owner_id="vp-eng")
        assert e.severity == "critical"
        assert e.escalated_at is None

    def test_source_mapped(self):
        ref = SourceRef(system=SourceSystem.LINEAR, external_id="ENG-42")
        s = SourceMapped(
            linked_sources=[ref],
            canonical_id="wi-001",
            source_precedence=["manual", "linear", "slack"],
        )
        assert len(s.linked_sources) == 1
        assert s.canonical_id == "wi-001"

    def test_reportable(self):
        r = Reportable(health="yellow", trend="declining")
        assert r.reporting_summary == ""


# ── 3. Canonical Entities ──────────────────────────────────────────


class TestEntities:
    def test_person(self):
        p = Person(id="p-1", name="Alice", email="alice@co.com", role="Engineer")
        assert p.name == "Alice"
        assert p.expertise == []

    def test_team(self):
        t = Team(id="t-1", name="Platform", lead_id="p-1", member_ids=["p-1", "p-2"])
        assert len(t.member_ids) == 2

    def test_work_item_composition(self):
        """WorkItem should have fields from all its interfaces."""
        wi = WorkItem(
            id="wi-001",
            title="Implement OAuth",
            status=WorkItemStatus.IN_PROGRESS.value,
            priority=Priority.HIGH.value,
            owner_id="alice",
            due_date=date.today() + timedelta(days=7),
            effort_hours=16.0,
            tags=["auth", "backend"],
        )
        # Trackable
        assert wi.lifecycle_state == "active"
        assert isinstance(wi.created_at, datetime)
        # Assignable
        assert wi.owner_id == "alice"
        assert wi.accountable_team_id == ""
        # Schedulable
        assert wi.is_overdue is False
        assert wi.days_until_due == 7
        # EvidenceBacked
        assert wi.confidence == 1.0
        assert wi.evidence_claim_ids == []
        # SourceMapped
        assert wi.linked_sources == []
        # Reportable
        assert wi.health == "green"

    def test_work_item_with_linked_sources(self):
        """WorkItem linked to Linear and GitHub."""
        wi = WorkItem(
            id="wi-multi",
            title="Fix auth bug",
            linked_sources=[
                SourceRef(system=SourceSystem.LINEAR, external_id="ENG-42"),
                SourceRef(system=SourceSystem.GITHUB, external_id="PR-99"),
                SourceRef(system=SourceSystem.SLACK, external_id="thread-abc"),
            ],
        )
        assert len(wi.linked_sources) == 3
        systems = {s.system for s in wi.linked_sources}
        assert systems == {SourceSystem.LINEAR, SourceSystem.GITHUB, SourceSystem.SLACK}

    def test_issue_composition(self):
        """Issue has Trackable, Assignable, Escalatable, EvidenceBacked, Reportable."""
        issue = Issue(
            id="iss-1",
            title="Login broken for SSO users",
            status=IssueState.INVESTIGATING.value,
            severity="critical",
            escalation_owner_id="vp-eng",
            owner_id="bob",
        )
        assert issue.severity == "critical"
        assert issue.escalation_owner_id == "vp-eng"
        assert issue.owner_id == "bob"
        assert issue.health == "green"

    def test_risk_composition(self):
        risk = Risk(
            id="r-1",
            title="Vendor API may deprecate v2",
            status=RiskState.ASSESSING.value,
            probability="high",
            severity="high",
            mitigation="Start migration to v3",
        )
        assert risk.status == "assessing"
        assert risk.mitigation == "Start migration to v3"

    def test_dependency(self):
        dep = Dependency(
            id="dep-1",
            title="Auth service API",
            upstream_team_id="t-auth",
            downstream_team_id="t-platform",
            commitment_date=date.today() + timedelta(days=14),
        )
        assert dep.status == DependencyState.IDENTIFIED.value
        assert dep.is_overdue is False

    def test_decision(self):
        d = Decision(
            id="dec-1",
            title="Use PostgreSQL over DynamoDB",
            decision_text="We chose PostgreSQL for relational query support",
            status=DecisionStatus.DECIDED.value,
            decision_owner_id="alice",
            rationale="Relational queries needed for reporting",
            options_considered=["PostgreSQL", "DynamoDB", "MongoDB"],
        )
        assert len(d.options_considered) == 3
        assert d.decided_at is None

    def test_meeting(self):
        m = Meeting(
            id="mtg-1",
            title="Sprint Planning",
            attendee_ids=["alice", "bob", "charlie"],
            duration_minutes=60,
        )
        assert len(m.attendee_ids) == 3
        assert m.executive_summary is None

    def test_signal(self):
        s = Signal(
            id="sig-1",
            signal_type=SignalType.BLOCKER_HINT.value,
            title="Possible blocker mentioned in standup",
            detail="Bob said 'we might be stuck on the API integration'",
            source_system="slack",
        )
        assert s.promoted_to_claim_id == ""

    def test_agent_recommendation(self):
        ar = AgentRecommendation(
            id="rec-1",
            title="Escalate auth blocker",
            recommendation_text="Escalate to VP Eng — blocked for 3 days",
            urgency=Priority.HIGH.value,
            confidence=0.85,
        )
        assert ar.accepted is None
        assert ar.confidence == 0.85

    def test_question(self):
        q = Question(
            id="q-1",
            question_text="What is the SLA for the auth service?",
            owner_id="alice",
            blocks_work_item_ids=["wi-001"],
        )
        assert q.answer == ""
        assert q.answered_at is None

    def test_ontology_change_proposal(self):
        p = OntologyChangeProposal(
            id="ocp-1",
            title="Add 'Experiment' entity type",
            description="A/B tests need their own type, not a WorkItem tag",
            change_type="add_entity",
        )
        assert p.approved is None
        assert p.proposed_by == "agent"

    def test_all_work_item_statuses(self):
        expected = {
            "proposed", "accepted", "planned", "in_progress", "blocked",
            "in_review", "done", "dropped", "superseded",
        }
        actual = {s.value for s in WorkItemStatus}
        assert actual == expected


# ── 4. Actions ──────────────────────────────────────────────────────


class TestActions:
    def test_triage_issue(self):
        a = TriageIssueAction(
            issue_id="iss-1",
            severity="critical",
            owner_id="bob",
            impacted_milestone_ids=["ms-3"],
            next_action_title="Hotfix the login flow",
            next_action_owner_id="bob",
            program_id="prog-1",
        )
        assert a.action_type == ActionType.TRIAGE_ISSUE
        assert a.completed is False

    def test_escalate_blocker(self):
        a = EscalateBlockerAction(
            blocker_object_id="dep-1",
            escalate_to_id="vp-eng",
            blocked_work_item_ids=["wi-1", "wi-2"],
        )
        assert a.urgency == "high"

    def test_convert_risk_to_issue(self):
        a = ConvertRiskToIssueAction(
            risk_id="r-1",
            new_issue_id="iss-new",
            severity="high",
            owner_id="alice",
        )
        assert a.action_type == ActionType.CONVERT_RISK_TO_ISSUE

    def test_confirm_decision(self):
        a = ConfirmDecisionAction(
            decision_id="dec-1",
            decided_by_id="alice",
            rationale="Team consensus in sprint planning",
        )
        assert a.action_type == ActionType.CONFIRM_DECISION

    def test_create_sprint_plan(self):
        a = CreateSprintPlanAction(
            sprint_id="spr-5",
            selected_work_item_ids=["wi-1", "wi-2", "wi-3"],
            total_committed_hours=60,
            capacity_hours=80,
            planning_rationale="Focus on auth epic",
        )
        assert a.total_committed_hours == 60

    def test_merge_duplicates(self):
        a = MergeDuplicateWorkItemsAction(
            canonical_work_item_id="wi-1",
            duplicate_work_item_ids=["wi-2", "wi-3"],
        )
        assert a.merge_strategy == "keep_canonical"

    def test_resolve_contradiction(self):
        a = ResolveContradictionAction(
            contradiction_id="cx-1",
            winning_claim_id="ec-1",
            losing_claim_ids=["ec-2"],
            resolution_rationale="PR merge is authoritative over Slack message",
        )
        assert a.action_type == ActionType.RESOLVE_CONTRADICTION

    def test_all_action_types(self):
        assert len(ActionType) == 13


# ── 5. Precedence Rules ────────────────────────────────────────────


class TestPrecedenceRules:
    def test_manual_beats_everything_for_status(self):
        rank_manual = get_precedence_rank(PrecedenceField.STATUS, SourceSystem.MANUAL)
        rank_linear = get_precedence_rank(PrecedenceField.STATUS, SourceSystem.LINEAR)
        rank_slack = get_precedence_rank(PrecedenceField.STATUS, SourceSystem.SLACK)
        rank_agent = get_precedence_rank(PrecedenceField.STATUS, SourceSystem.AGENT)
        assert rank_manual > rank_linear > rank_slack > rank_agent

    def test_github_wins_delivery_progress(self):
        rank_github = get_precedence_rank(PrecedenceField.DELIVERY_PROGRESS, SourceSystem.GITHUB)
        rank_linear = get_precedence_rank(PrecedenceField.DELIVERY_PROGRESS, SourceSystem.LINEAR)
        rank_jira = get_precedence_rank(PrecedenceField.DELIVERY_PROGRESS, SourceSystem.JIRA)
        assert rank_github > rank_linear
        assert rank_github > rank_jira

    def test_resolve_status_conflict(self):
        candidates = [
            (SourceSystem.SLACK, "done"),
            (SourceSystem.LINEAR, "in_review"),
            (SourceSystem.TASK_BOARD, "in_progress"),
        ]
        winner = resolve_conflict(PrecedenceField.STATUS, candidates)
        assert winner is not None
        assert winner[0] == SourceSystem.TASK_BOARD
        assert winner[1] == "in_progress"

    def test_resolve_empty_candidates(self):
        result = resolve_conflict(PrecedenceField.STATUS, [])
        assert result is None

    def test_unknown_source_returns_negative_rank(self):
        rank = get_precedence_rank(PrecedenceField.STATUS, SourceSystem.SENDGRID)
        assert rank == -1

    def test_explain_precedence(self):
        explanation = explain_precedence(PrecedenceField.STATUS)
        assert "Source-of-truth" in explanation
        assert "manual" in explanation.lower()

    def test_all_precedence_fields_have_rules(self):
        for field in PrecedenceField:
            assert field in PRECEDENCE_RULES
            assert len(PRECEDENCE_RULES[field]) > 0

    def test_agent_inference_always_lowest(self):
        for field in PrecedenceField:
            chain = PRECEDENCE_RULES[field]
            if SourceSystem.AGENT in chain:
                assert chain.index(SourceSystem.AGENT) == 0

    def test_manual_always_highest(self):
        for field in PrecedenceField:
            chain = PRECEDENCE_RULES[field]
            if SourceSystem.MANUAL in chain:
                assert chain[-1] == SourceSystem.MANUAL


# ── 6. Mapper ──────────────────────────────────────────────────────


class TestMapperLinear:
    def test_map_linear_issue(self):
        wi = map_linear_issue(
            issue_id="ENG-42",
            title="Fix login bug",
            state="In Progress",
            assignee="Alice",
            priority=2,
            labels=["bug", "auth"],
            url="https://linear.app/team/ENG-42",
        )
        assert isinstance(wi, WorkItem)
        assert wi.id == "wi-lin-ENG-42"
        assert wi.title == "Fix login bug"
        assert wi.status == WorkItemStatus.IN_PROGRESS.value
        assert wi.priority == Priority.HIGH.value
        assert wi.owner_id == "Alice"
        assert len(wi.linked_sources) == 1
        assert wi.linked_sources[0].system == SourceSystem.LINEAR

    def test_map_linear_urgent(self):
        wi = map_linear_issue("ENG-1", "Urgent fix", priority=1)
        assert wi.priority == Priority.CRITICAL.value

    def test_map_linear_unknown_state(self):
        wi = map_linear_issue("ENG-99", "Unknown state", state="CustomState")
        assert wi.status == WorkItemStatus.PROPOSED.value


class TestMapperJira:
    def test_map_jira_ticket(self):
        wi = map_jira_ticket(
            key="PROJ-123",
            summary="Add caching layer",
            status="In Progress",
            assignee="Bob",
            priority="High",
            url="https://jira.co/PROJ-123",
        )
        assert isinstance(wi, WorkItem)
        assert wi.id == "wi-jira-PROJ-123"
        assert wi.status == WorkItemStatus.IN_PROGRESS.value
        assert wi.priority == Priority.HIGH.value
        assert wi.linked_sources[0].system == SourceSystem.JIRA

    def test_map_jira_blocked(self):
        wi = map_jira_ticket("PROJ-5", "Blocked task", status="Blocked")
        assert wi.status == WorkItemStatus.BLOCKED.value


class TestMapperGitHub:
    def test_map_github_pr(self):
        art = map_github_pr(
            number=42,
            title="Add OAuth support",
            author="alice",
            state="open",
            url="https://github.com/org/repo/pull/42",
        )
        assert isinstance(art, Artifact)
        assert art.artifact_type == "pull_request"
        assert art.author_id == "alice"
        assert art.linked_sources[0].system == SourceSystem.GITHUB

    def test_map_github_pr_to_signal_merged(self):
        sig = map_github_pr_to_signal(42, "OAuth support", "merged")
        assert isinstance(sig, Signal)
        assert sig.signal_type == SignalType.PROGRESS_UPDATE.value
        assert "merged" in sig.detail.lower()

    def test_map_github_pr_to_signal_closed(self):
        sig = map_github_pr_to_signal(42, "OAuth support", "closed")
        assert "without merge" in sig.detail


class TestMapperSlack:
    def test_map_slack_to_signal(self):
        sig = map_slack_message_to_signal(
            channel="C-eng",
            thread_ts="123.456",
            text="I think we're blocked on the API migration",
            author="alice",
            signal_type=SignalType.BLOCKER_HINT.value,
        )
        assert isinstance(sig, Signal)
        assert sig.source_system == SourceSystem.SLACK.value

    def test_map_slack_to_claim(self):
        claim = map_slack_message_to_claim(
            channel="C-eng",
            thread_ts="123.789",
            text="Auth migration is complete",
            author="bob",
            claim_type=ClaimType.STATUS_UPDATE,
            confidence=0.6,
        )
        assert isinstance(claim, EvidenceClaim)
        assert claim.confidence == 0.6
        assert claim.claim_type == ClaimType.STATUS_UPDATE


class TestMapperZoom:
    def test_map_zoom_meeting(self):
        mtg = map_zoom_meeting(
            meeting_id="ZM-001",
            title="Sprint Planning",
            host="alice",
            attendees=["alice", "bob"],
            duration_minutes=60,
            summary="Reviewed sprint backlog, planned next two weeks",
            program_id="prog-1",
        )
        assert isinstance(mtg, Meeting)
        assert mtg.id == "mtg-zoom-ZM-001"
        assert len(mtg.attendee_ids) == 2
        assert mtg.executive_summary is not None
        assert mtg.executive_summary.value == "Reviewed sprint backlog, planned next two weeks"
        assert mtg.executive_summary.extractor == "zoom_ai"

    def test_map_zoom_action_item(self):
        ai = map_zoom_action_item(
            meeting_id="ZM-001",
            owner="Bob",
            description="Write the API spec",
            due_date="2026-06-20",
        )
        assert isinstance(ai, ActionItem)
        assert ai.owner_id == "Bob"
        assert ai.due_date == date(2026, 6, 20)
        assert ai.source_meeting_id == "mtg-zoom-ZM-001"


class TestMapperNotion:
    def test_map_notion_page(self):
        art = map_notion_page(
            page_id="notion-abc",
            title="Architecture Decision Record",
            url="https://notion.so/page-abc",
            author="alice",
            page_type="decision_log",
        )
        assert isinstance(art, Artifact)
        assert art.artifact_type == "decision_log"
        assert art.linked_sources[0].system == SourceSystem.NOTION


class TestMapperCalendar:
    def test_map_google_calendar_event(self):
        start = datetime(2026, 6, 16, 10, 0)
        end = datetime(2026, 6, 16, 11, 0)
        mtg = map_calendar_event(
            event_id="cal-evt-001",
            title="Design Review",
            start=start,
            end=end,
            attendees=["alice", "bob"],
            source_system=SourceSystem.GOOGLE_CALENDAR,
        )
        assert isinstance(mtg, Meeting)
        assert mtg.duration_minutes == 60
        assert mtg.start_date == date(2026, 6, 16)
        assert mtg.linked_sources[0].system == SourceSystem.GOOGLE_CALENDAR

    def test_map_microsoft_calendar_event(self):
        mtg = map_calendar_event(
            event_id="ms-cal-001",
            title="Standup",
            source_system=SourceSystem.MICROSOFT_CALENDAR,
        )
        assert mtg.linked_sources[0].system == SourceSystem.MICROSOFT_CALENDAR


class TestMapperBoardTask:
    def test_map_board_task(self):
        wi = map_board_task(
            task_id="BT-001",
            title="Implement feature X",
            status="IN_PROGRESS",
            priority="HIGH",
            assignee="Alice",
            due_date="2026-06-20",
            effort_hours=8.0,
            sprint_id="spr-1",
            tags=["backend"],
        )
        assert isinstance(wi, WorkItem)
        assert wi.id == "wi-board-BT-001"
        assert wi.status == WorkItemStatus.IN_PROGRESS.value
        assert wi.priority == Priority.HIGH.value
        assert wi.due_date == date(2026, 6, 20)
        assert wi.confidence == 1.0
        assert wi.linked_sources[0].system == SourceSystem.TASK_BOARD

    def test_map_board_task_with_blocks(self):
        wi = map_board_task(
            task_id="BT-002",
            title="Blocked task",
            blocks=["BT-003"],
            blocked_by=["BT-001"],
        )
        assert wi.blocks == ["wi-board-BT-003"]
        assert wi.blocked_by == ["wi-board-BT-001"]


class TestMapperRAID:
    def test_map_raid_risk(self):
        risk = map_raid_risk(
            risk_id="R-001",
            title="Vendor lock-in",
            severity="high",
            probability="medium",
            owner="alice",
            mitigation="Evaluate alternatives",
        )
        assert isinstance(risk, Risk)
        assert risk.id == "risk-R-001"
        assert risk.severity == "high"
        assert risk.status == RiskState.IDENTIFIED.value

    def test_map_raid_issue(self):
        issue = map_raid_issue(
            issue_id="I-001",
            title="CI pipeline broken",
            severity="critical",
            owner="bob",
            status="MITIGATING",
        )
        assert isinstance(issue, Issue)
        assert issue.status == IssueState.IN_PROGRESS.value

    def test_map_raid_dependency(self):
        dep = map_raid_dependency(
            dep_id="D-001",
            title="Auth service v3 API",
            upstream_team="auth-team",
            downstream_team="platform",
            commitment_date="2026-07-01",
        )
        assert isinstance(dep, Dependency)
        assert dep.commitment_date == date(2026, 7, 1)
        assert dep.upstream_team_id == "auth-team"


class TestMapperDecision:
    def test_map_decision(self):
        d = map_decision(
            decision_id="DEC-001",
            title="Use PostgreSQL",
            decision_text="PostgreSQL for relational queries",
            status="DECIDED",
            decided_by="alice",
            rationale="Better query support",
        )
        assert isinstance(d, Decision)
        assert d.status == DecisionStatus.DECIDED.value
        assert d.decision_owner_id == "alice"


# ── 7. Serialization Round-Trip ────────────────────────────────────


class TestSerialization:
    def test_work_item_round_trip(self):
        wi = WorkItem(
            id="wi-rt",
            title="Round trip test",
            status=WorkItemStatus.IN_PROGRESS.value,
            priority=Priority.HIGH.value,
            owner_id="alice",
            linked_sources=[
                SourceRef(system=SourceSystem.LINEAR, external_id="ENG-1"),
            ],
            evidence_claim_ids=["ec-1"],
            tags=["test"],
        )
        data = wi.model_dump()
        restored = WorkItem.model_validate(data)
        assert restored.id == "wi-rt"
        assert restored.owner_id == "alice"
        assert len(restored.linked_sources) == 1
        assert restored.linked_sources[0].system == SourceSystem.LINEAR

    def test_evidence_claim_round_trip(self):
        claim = EvidenceClaim(
            id="ec-rt",
            claim_text="Feature is complete",
            claim_type=ClaimType.STATUS_UPDATE,
            confidence=0.9,
            source_refs=[
                SourceRef(system=SourceSystem.GITHUB, external_id="PR-1"),
            ],
        )
        data = claim.model_dump()
        restored = EvidenceClaim.model_validate(data)
        assert restored.claim_text == "Feature is complete"
        assert restored.confidence == 0.9

    def test_action_round_trip(self):
        a = TriageIssueAction(
            issue_id="iss-1",
            severity="critical",
            owner_id="bob",
        )
        data = a.model_dump()
        restored = TriageIssueAction.model_validate(data)
        assert restored.action_type == ActionType.TRIAGE_ISSUE
        assert restored.issue_id == "iss-1"

    def test_json_serialization(self):
        wi = map_linear_issue("ENG-42", "Test", state="In Progress")
        json_str = wi.model_dump_json()
        assert "ENG-42" in json_str
        assert "in_progress" in json_str


# ── 8. Full Integration Scenario ───────────────────────────────────


class TestIntegrationScenario:
    """Test a realistic scenario: same work item tracked across 3 systems."""

    def test_multi_source_work_item(self):
        # The same feature is tracked in Linear, has a GitHub PR, and was discussed in Slack
        wi_from_linear = map_linear_issue(
            "ENG-42", "Implement OAuth refresh tokens",
            state="In Progress", assignee="Alice", priority=2,
        )
        pr_artifact = map_github_pr(
            99, "feat: OAuth refresh token handling", author="alice",
        )
        slack_signal = map_slack_message_to_signal(
            "C-eng", "123.456",
            "Almost done with OAuth refresh — just need tests",
            author="alice",
        )
        slack_claim = map_slack_message_to_claim(
            "C-eng", "123.456",
            "OAuth refresh implementation is 90% complete",
            author="alice",
            claim_type=ClaimType.PROGRESS,
            confidence=0.6,
        )

        # The ontology entity is the canonical WorkItem with all sources linked
        canonical = WorkItem(
            id="wi-canonical-oauth",
            title="Implement OAuth refresh tokens",
            status=WorkItemStatus.IN_PROGRESS.value,
            priority=Priority.HIGH.value,
            owner_id="alice",
            linked_sources=[
                wi_from_linear.linked_sources[0],
                pr_artifact.linked_sources[0],
                slack_signal.source_refs[0],
            ],
            evidence_claim_ids=[slack_claim.id],
            confidence=0.9,
        )

        assert len(canonical.linked_sources) == 3
        systems = {s.system for s in canonical.linked_sources}
        assert systems == {SourceSystem.LINEAR, SourceSystem.GITHUB, SourceSystem.SLACK}

        # Precedence: if Linear says "in progress" and Slack says "almost done",
        # Linear wins for status (higher precedence than Slack)
        winner = resolve_conflict(PrecedenceField.STATUS, [
            (SourceSystem.SLACK, "almost_done"),
            (SourceSystem.LINEAR, "in_progress"),
        ])
        assert winner is not None
        assert winner[0] == SourceSystem.LINEAR

        # But for delivery progress, GitHub PR merge would win
        winner = resolve_conflict(PrecedenceField.DELIVERY_PROGRESS, [
            (SourceSystem.LINEAR, "in_progress"),
            (SourceSystem.GITHUB, "merged"),
        ])
        assert winner is not None
        assert winner[0] == SourceSystem.GITHUB
