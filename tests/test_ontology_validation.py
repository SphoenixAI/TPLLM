"""Tests for the validation layer and closed-loop control system.

Covers:
1. Validation gate — evidence sufficiency, confidence, contradictions, staleness
2. Severity-gated approval — high-severity needs human sign-off
3. Policy configuration — adjustable thresholds
4. Enhanced AgentRecommendation — insight link, recommendation type, approval
5. Enhanced KPIInsight — severity, human review flag, evidence claims
6. Five stress-test scenarios:
   a. Contradictory sources
   b. Silent blocker
   c. Decision bottleneck
   d. Ownership ambiguity
   e. Agent mistake (duplicate detection)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from lltpm.ontology.core import (
    ClaimType,
    Contradiction,
    ContradictionSeverity,
    EvidenceClaim,
    SourceRef,
    SourceSystem,
)
from lltpm.ontology.entities import (
    AgentRecommendation,
    Decision,
    DecisionStatus,
    Dependency,
    DependencyState,
    Issue,
    IssueState,
    Priority,
    RecommendationStatus,
    RecommendationType,
    Signal,
    SignalType,
    WorkItem,
    WorkItemStatus,
)
from lltpm.ontology.measurement import (
    HealthScore,
    HealthStatus,
    InsightSeverity,
    InsightType,
    KPIInsight,
    KPIObservation,
    KPIUnit,
    MeasuredObjectType,
    TrendDirection,
    get_builtin_definitions,
)
from lltpm.ontology.precedence import PrecedenceField, resolve_conflict
from lltpm.ontology.validation import (
    DEFAULT_POLICY,
    ValidationOutcome,
    ValidationPolicy,
    ValidationResult,
    validate_insight,
)


# ── 1. Validation Gate ──────────────────────────────────────────────


class TestValidationGate:
    def test_approved_high_confidence_low_severity(self):
        insight = KPIInsight(
            id="ins-1",
            confidence=0.9,
            severity=InsightSeverity.LOW,
            related_kpi_observation_ids=["obs-1"],
            supporting_evidence_claim_ids=["ec-1"],
        )
        result = validate_insight(insight)
        assert result.passed is True
        assert result.outcome == ValidationOutcome.APPROVED
        assert result.human_review_required is False

    def test_needs_review_high_severity(self):
        insight = KPIInsight(
            id="ins-2",
            confidence=0.85,
            severity=InsightSeverity.HIGH,
            related_kpi_observation_ids=["obs-1"],
            supporting_evidence_claim_ids=["ec-1"],
        )
        result = validate_insight(insight)
        assert result.human_review_required is True
        assert "human approval" in result.reasons[0].lower() or "severity" in result.reasons[0].lower()

    def test_critical_severity_always_needs_review(self):
        insight = KPIInsight(
            id="ins-3",
            confidence=0.99,
            severity=InsightSeverity.CRITICAL,
            related_kpi_observation_ids=["obs-1", "obs-2"],
            supporting_evidence_claim_ids=["ec-1", "ec-2", "ec-3"],
        )
        result = validate_insight(insight)
        assert result.human_review_required is True

    def test_insufficient_evidence_rejected(self):
        insight = KPIInsight(
            id="ins-4",
            confidence=0.8,
            severity=InsightSeverity.MEDIUM,
            related_kpi_observation_ids=[],
            supporting_evidence_claim_ids=[],
        )
        result = validate_insight(insight)
        assert result.passed is False
        assert result.outcome == ValidationOutcome.INSUFFICIENT_EVIDENCE

    def test_low_confidence_rejected(self):
        insight = KPIInsight(
            id="ins-5",
            confidence=0.2,
            severity=InsightSeverity.LOW,
            related_kpi_observation_ids=["obs-1"],
            supporting_evidence_claim_ids=["ec-1"],
        )
        result = validate_insight(insight)
        assert result.passed is False
        assert result.outcome == ValidationOutcome.LOW_CONFIDENCE

    def test_contradicted_evidence_rejected(self):
        insight = KPIInsight(
            id="ins-6",
            confidence=0.85,
            severity=InsightSeverity.MEDIUM,
            related_kpi_observation_ids=["obs-1"],
            supporting_evidence_claim_ids=["ec-1", "ec-2"],
        )
        claims = [
            EvidenceClaim(id="ec-1", claim_text="Done", contradicted_by_claim_ids=["ec-x"]),
            EvidenceClaim(id="ec-2", claim_text="Also done", contradicted_by_claim_ids=["ec-y"]),
        ]
        result = validate_insight(insight, evidence_claims=claims)
        assert result.outcome == ValidationOutcome.CONTRADICTED
        assert "contradiction" in result.reasons[0].lower()

    def test_stale_evidence_rejected(self):
        insight = KPIInsight(
            id="ins-7",
            confidence=0.85,
            severity=InsightSeverity.MEDIUM,
            related_kpi_observation_ids=["obs-1"],
            supporting_evidence_claim_ids=["ec-1"],
        )
        old_obs = [
            KPIObservation(
                id="obs-1",
                value=5.0,
                observed_at=datetime.utcnow() - timedelta(days=14),
            ),
        ]
        result = validate_insight(insight, observations=old_obs)
        assert result.outcome == ValidationOutcome.STALE_EVIDENCE

    def test_medium_severity_auto_approved_at_high_confidence(self):
        insight = KPIInsight(
            id="ins-8",
            confidence=0.9,
            severity=InsightSeverity.MEDIUM,
            related_kpi_observation_ids=["obs-1"],
            supporting_evidence_claim_ids=["ec-1"],
        )
        result = validate_insight(insight)
        assert result.passed is True
        assert result.outcome == ValidationOutcome.APPROVED
        assert result.human_review_required is False

    def test_medium_confidence_needs_review(self):
        insight = KPIInsight(
            id="ins-9",
            confidence=0.55,
            severity=InsightSeverity.MEDIUM,
            related_kpi_observation_ids=["obs-1"],
            supporting_evidence_claim_ids=["ec-1"],
        )
        result = validate_insight(insight)
        assert result.outcome == ValidationOutcome.NEEDS_HUMAN_REVIEW
        assert result.human_review_required is True


# ── 2. Policy Configuration ────────────────────────────────────────


class TestValidationPolicy:
    def test_strict_policy(self):
        strict = ValidationPolicy(
            min_confidence_for_auto_action=0.95,
            min_evidence_claims=3,
            min_kpi_observations=2,
            severity_requires_approval=[
                InsightSeverity.MEDIUM.value,
                InsightSeverity.HIGH.value,
                InsightSeverity.CRITICAL.value,
            ],
        )
        insight = KPIInsight(
            id="ins-strict",
            confidence=0.85,
            severity=InsightSeverity.MEDIUM,
            related_kpi_observation_ids=["obs-1"],
            supporting_evidence_claim_ids=["ec-1"],
        )
        result = validate_insight(insight, policy=strict)
        assert result.human_review_required is True

    def test_relaxed_policy(self):
        relaxed = ValidationPolicy(
            min_confidence_for_auto_action=0.5,
            min_evidence_claims=0,
            min_kpi_observations=0,
            severity_requires_approval=[InsightSeverity.CRITICAL.value],
            auto_approve_low_severity=True,
        )
        insight = KPIInsight(
            id="ins-relaxed",
            confidence=0.6,
            severity=InsightSeverity.HIGH,
            related_kpi_observation_ids=["obs-1"],
            supporting_evidence_claim_ids=["ec-1"],
        )
        result = validate_insight(insight, policy=relaxed)
        assert result.passed is True
        assert result.outcome == ValidationOutcome.APPROVED

    def test_default_policy_values(self):
        p = DEFAULT_POLICY
        assert p.min_confidence_for_auto_action == 0.75
        assert p.min_confidence_for_review == 0.4
        assert p.max_evidence_age_hours == 168.0
        assert p.auto_approve_low_severity is True


# ── 3. Enhanced AgentRecommendation ────────────────────────────────


class TestEnhancedAgentRecommendation:
    def test_recommendation_with_insight_link(self):
        rec = AgentRecommendation(
            id="rec-1",
            insight_id="ins-1",
            recommendation_type=RecommendationType.ESCALATE_ISSUE.value,
            title="Escalate security review blocker",
            recommendation_text="Escalate to VP Eng — blocked for 8 days",
            reasoning_summary="Blocker age KPI is critical, dependency on security team unresolved",
            urgency=Priority.HIGH.value,
            approval_required=True,
            recommendation_status=RecommendationStatus.PROPOSED.value,
            confidence=0.85,
            target_object_ids=["dep-security", "wi-auth-refresh"],
        )
        assert rec.insight_id == "ins-1"
        assert rec.recommendation_type == RecommendationType.ESCALATE_ISSUE.value
        assert rec.approval_required is True
        assert rec.recommendation_status == RecommendationStatus.PROPOSED.value

    def test_recommendation_lifecycle(self):
        rec = AgentRecommendation(
            id="rec-2",
            recommendation_status=RecommendationStatus.PROPOSED.value,
        )
        assert rec.accepted is None

        # Simulate approval
        rec.accepted = True
        rec.accepted_by_id = "alice"
        rec.accepted_at = datetime.utcnow()
        rec.recommendation_status = RecommendationStatus.APPROVED.value
        assert rec.recommendation_status == "approved"

        # Simulate execution
        rec.recommendation_status = RecommendationStatus.EXECUTED.value
        rec.executed_action_ids = ["ai-1", "ai-2"]
        assert len(rec.executed_action_ids) == 2

    def test_recommendation_rejection(self):
        rec = AgentRecommendation(
            id="rec-3",
            recommendation_status=RecommendationStatus.PROPOSED.value,
        )
        rec.accepted = False
        rec.rejected_reason = "Already handled in standup"
        rec.recommendation_status = RecommendationStatus.REJECTED.value
        assert rec.recommendation_status == "rejected"
        assert rec.rejected_reason != ""

    def test_all_recommendation_types(self):
        expected = {
            "create_action_item", "escalate_issue", "request_update",
            "schedule_meeting", "confirm_decision", "resolve_contradiction",
            "reassign_owner", "adjust_sprint_plan", "update_status_report",
            "merge_duplicates", "flag_for_review",
        }
        actual = {t.value for t in RecommendationType}
        assert actual == expected

    def test_all_recommendation_statuses(self):
        expected = {"proposed", "approved", "rejected", "executed", "expired"}
        actual = {s.value for s in RecommendationStatus}
        assert actual == expected


# ── 4. Enhanced KPIInsight ──────────────────────────────────────────


class TestEnhancedKPIInsight:
    def test_insight_with_severity(self):
        insight = KPIInsight(
            id="ins-sev",
            severity=InsightSeverity.CRITICAL,
            human_review_required=True,
            supporting_evidence_claim_ids=["ec-1", "ec-2"],
        )
        assert insight.severity == InsightSeverity.CRITICAL
        assert insight.human_review_required is True
        assert len(insight.supporting_evidence_claim_ids) == 2

    def test_all_insight_severities(self):
        expected = {"low", "medium", "high", "critical"}
        actual = {s.value for s in InsightSeverity}
        assert actual == expected


# ── 5. Stress Test Scenarios ────────────────────────────────────────


class TestScenarioContradictorySources:
    """Scenario: Notion says launch June 28, Slack says July 12, Linear says June 28."""

    def test_full_flow(self):
        # Claims from different sources
        notion_claim = EvidenceClaim(
            id="ec-notion-launch",
            claim_text="Launch date is June 28",
            claim_type=ClaimType.TIMELINE,
            confidence=0.8,
            source_refs=[SourceRef(system=SourceSystem.NOTION, external_id="page-launch")],
        )
        slack_claim = EvidenceClaim(
            id="ec-slack-launch",
            claim_text="Launch moved to July 12",
            claim_type=ClaimType.TIMELINE,
            confidence=0.6,
            source_refs=[SourceRef(system=SourceSystem.SLACK, external_id="C-eng:123")],
            contradicted_by_claim_ids=[],
        )
        linear_claim = EvidenceClaim(
            id="ec-linear-launch",
            claim_text="Launch target June 28",
            claim_type=ClaimType.TIMELINE,
            confidence=0.7,
            source_refs=[SourceRef(system=SourceSystem.LINEAR, external_id="ENG-milestone")],
        )

        # Mark contradictions
        notion_claim.contradicted_by_claim_ids = ["ec-slack-launch"]
        slack_claim.contradicted_by_claim_ids = ["ec-notion-launch", "ec-linear-launch"]

        # Create contradiction object
        contradiction = Contradiction(
            id="cx-launch-date",
            title="Launch date conflict: June 28 vs July 12",
            conflicting_claim_ids=[notion_claim.id, slack_claim.id, linear_claim.id],
            affected_object_ids=["ms-launch"],
            severity=ContradictionSeverity.HIGH,
        )
        assert len(contradiction.conflicting_claim_ids) == 3

        # Precedence: for timeline, Notion > Slack
        winner = resolve_conflict(PrecedenceField.TIMELINE, [
            (SourceSystem.NOTION, "June 28"),
            (SourceSystem.SLACK, "July 12"),
            (SourceSystem.LINEAR, "June 28"),
        ])
        assert winner is not None
        # Task board/manual would win, but among these Notion > Linear > Slack

        # Insight generated
        insight = KPIInsight(
            id="ins-launch-conflict",
            insight_type=InsightType.SCOPE_CHURN,
            title="Launch date contradiction detected",
            summary="Notion and Linear say June 28, but Slack says July 12. "
                    "Recommend confirming launch date with decision owner.",
            severity=InsightSeverity.HIGH,
            related_kpi_observation_ids=[],
            supporting_evidence_claim_ids=[
                notion_claim.id, slack_claim.id, linear_claim.id,
            ],
            affected_object_ids=["ms-launch"],
            confidence=0.85,
            human_review_required=True,
        )

        # Validate — contradicted evidence should flag for review
        result = validate_insight(
            insight,
            evidence_claims=[notion_claim, slack_claim, linear_claim],
        )
        assert result.human_review_required is True

        # Recommendation: confirm launch date
        rec = AgentRecommendation(
            id="rec-launch",
            insight_id=insight.id,
            recommendation_type=RecommendationType.CONFIRM_DECISION.value,
            title="Confirm launch date",
            recommendation_text="Sources disagree on launch date. Schedule decision meeting.",
            reasoning_summary="2/3 sources say June 28, but Slack message suggests delay",
            approval_required=True,
            target_object_ids=["ms-launch"],
        )
        assert rec.approval_required is True


class TestScenarioSilentBlocker:
    """Scenario: Linear task unchanged 9 days, GitHub PR no commits, Slack says 'waiting on approval'."""

    def test_full_flow(self):
        # Signals from multiple sources
        linear_signal = Signal(
            id="sig-stale-linear",
            signal_type=SignalType.STATUS_HINT.value,
            title="Linear issue ENG-42 unchanged for 9 days",
            source_system=SourceSystem.LINEAR.value,
        )
        github_signal = Signal(
            id="sig-stale-pr",
            signal_type=SignalType.STATUS_HINT.value,
            title="PR #99 no commits for 9 days",
            source_system=SourceSystem.GITHUB.value,
        )
        slack_claim = EvidenceClaim(
            id="ec-slack-waiting",
            claim_text="Waiting on security approval",
            claim_type=ClaimType.BLOCKER,
            confidence=0.7,
            source_refs=[SourceRef(system=SourceSystem.SLACK, external_id="C-eng:456")],
        )

        # KPI observation: blocker age worsening
        kpi_def = get_builtin_definitions()["kpi-avg-blocker-age"]
        obs = KPIObservation(
            id="obs-blocker-age",
            kpi_definition_id=kpi_def.id,
            measured_object_id="prog-1",
            value=9.0,
            unit=KPIUnit.DAYS,
            health_status=kpi_def.evaluate_health(9.0),
        )
        assert obs.health_status == HealthStatus.WARNING

        # Insight
        insight = KPIInsight(
            id="ins-silent-blocker",
            insight_type=InsightType.DEPENDENCY_DRAG,
            title="Approval dependency dragging for 9 days",
            summary="Security approval blocking ENG-42 and PR #99. "
                    "Slack confirms team is waiting.",
            severity=InsightSeverity.HIGH,
            related_kpi_observation_ids=[obs.id],
            supporting_evidence_claim_ids=[slack_claim.id],
            confidence=0.8,
        )

        # Validate
        result = validate_insight(
            insight,
            observations=[obs],
            evidence_claims=[slack_claim],
        )
        assert result.human_review_required is True  # HIGH severity

        # Recommendation
        rec = AgentRecommendation(
            id="rec-escalate-approval",
            insight_id=insight.id,
            recommendation_type=RecommendationType.ESCALATE_ISSUE.value,
            title="Escalate security review",
            recommendation_text="Request ETA from security team, escalate if no response in 24h",
            reasoning_summary="Blocker age = 9 days (critical threshold = 10). "
                              "Multiple signals confirm stalled work.",
            urgency=Priority.HIGH.value,
            approval_required=True,
        )
        assert rec.recommendation_type == "escalate_issue"


class TestScenarioDecisionBottleneck:
    """Scenario: Three meeting notes mention the same unresolved question, no decision decided."""

    def test_full_flow(self):
        # Meeting evidence
        claims = [
            EvidenceClaim(
                id=f"ec-mtg-{i}",
                claim_text="Team discussed ontology schema but no decision made",
                claim_type=ClaimType.DECISION,
                confidence=0.8,
                source_refs=[SourceRef(system=SourceSystem.ZOOM, external_id=f"mtg-{i}")],
            )
            for i in range(3)
        ]

        # Undecided decision
        decision = Decision(
            id="dec-ontology-schema",
            title="Finalize ontology schema v1",
            status=DecisionStatus.PROPOSED.value,
            decision_owner_id="",  # No owner!
        )
        assert decision.status == "proposed"
        assert decision.decision_owner_id == ""

        # KPI observation
        kpi_def = get_builtin_definitions()["kpi-decision-latency"]
        obs = KPIObservation(
            id="obs-decision-latency",
            kpi_definition_id=kpi_def.id,
            value=12.0,
            health_status=kpi_def.evaluate_health(12.0),
        )
        assert obs.health_status == HealthStatus.CRITICAL

        # Insight
        insight = KPIInsight(
            id="ins-decision-bottleneck",
            insight_type=InsightType.DECISION_BOTTLENECK,
            title="Ontology schema decision pending 12 days",
            summary="Three meeting notes discuss the same unresolved question. "
                    "No decision owner assigned. Decision latency = 12 days (critical).",
            severity=InsightSeverity.CRITICAL,
            related_kpi_observation_ids=[obs.id],
            supporting_evidence_claim_ids=[c.id for c in claims],
            confidence=0.9,
        )

        # Validate — critical severity always needs review
        result = validate_insight(insight, evidence_claims=claims)
        assert result.human_review_required is True

        # Recommendation
        rec = AgentRecommendation(
            id="rec-schedule-decision",
            insight_id=insight.id,
            recommendation_type=RecommendationType.CONFIRM_DECISION.value,
            title="Schedule ontology schema decision meeting",
            recommendation_text="Assign decision owner and schedule 30-min decision meeting",
            reasoning_summary="Decision discussed in 3 meetings with no resolution. "
                              "Latency at 12 days (critical threshold = 10).",
            urgency=Priority.CRITICAL.value,
            approval_required=True,
            target_object_ids=[decision.id],
        )
        assert rec.urgency == "critical"


class TestScenarioOwnershipAmbiguity:
    """Scenario: Slack mentions two possible owners, Jira has no assignee, board has no owner."""

    def test_full_flow(self):
        # Conflicting ownership signals
        slack_claim_a = EvidenceClaim(
            id="ec-owner-alice",
            claim_text="Alice is handling the auth migration",
            claim_type=ClaimType.OWNERSHIP,
            confidence=0.5,
            source_refs=[SourceRef(system=SourceSystem.SLACK, external_id="C-eng:789")],
        )
        slack_claim_b = EvidenceClaim(
            id="ec-owner-bob",
            claim_text="Bob said he'd take over the auth work",
            claim_type=ClaimType.OWNERSHIP,
            confidence=0.5,
            source_refs=[SourceRef(system=SourceSystem.SLACK, external_id="C-eng:790")],
            contradicted_by_claim_ids=["ec-owner-alice"],
        )
        slack_claim_a.contradicted_by_claim_ids = ["ec-owner-bob"]

        # Unowned work item
        wi = WorkItem(
            id="wi-auth-migration",
            title="Auth service migration",
            status=WorkItemStatus.IN_PROGRESS.value,
            owner_id="",  # No owner!
        )

        # KPI observation
        kpi_def = get_builtin_definitions()["kpi-ownership-clarity"]
        obs = KPIObservation(
            id="obs-ownership",
            kpi_definition_id=kpi_def.id,
            value=60.0,  # 60% clarity — well below 95% target
            health_status=kpi_def.evaluate_health(60.0),
        )
        assert obs.health_status == HealthStatus.CRITICAL

        # Insight
        insight = KPIInsight(
            id="ins-ownership-gap",
            insight_type=InsightType.OWNERSHIP_GAP,
            title="Auth migration has no clear owner",
            summary="Slack mentions both Alice and Bob as possible owners. "
                    "Jira and task board have no assignee.",
            severity=InsightSeverity.HIGH,
            related_kpi_observation_ids=[obs.id],
            supporting_evidence_claim_ids=[slack_claim_a.id, slack_claim_b.id],
            confidence=0.75,
        )

        # Validate
        result = validate_insight(
            insight,
            observations=[obs],
            evidence_claims=[slack_claim_a, slack_claim_b],
        )
        # Contradictions + high severity → needs review
        assert result.human_review_required is True

        # Recommendation
        rec = AgentRecommendation(
            id="rec-assign-owner",
            insight_id=insight.id,
            recommendation_type=RecommendationType.REASSIGN_OWNER.value,
            title="Assign accountable owner for auth migration",
            recommendation_text="Clarify single accountable owner between Alice and Bob",
            approval_required=True,
            target_object_ids=[wi.id],
        )
        assert rec.recommendation_type == "reassign_owner"


class TestScenarioAgentMistake:
    """Scenario: Agent creates duplicate action items from Zoom and Slack."""

    def test_full_flow(self):
        # Duplicate work items from different sources
        wi_zoom = WorkItem(
            id="wi-zoom-abc",
            title="Update API spec",
            owner_id="Bob",
            linked_sources=[SourceRef(system=SourceSystem.ZOOM, external_id="mtg-42")],
        )
        wi_slack = WorkItem(
            id="wi-slack-xyz",
            title="Update the API spec document",
            owner_id="Bob",
            linked_sources=[SourceRef(system=SourceSystem.SLACK, external_id="C-eng:thread")],
        )

        # Evidence: duplicate detection across sources
        dupe_claim = EvidenceClaim(
            id="ec-duplicate-detected",
            claim_text="Near-identical work items from Zoom and Slack with same owner",
            claim_type=ClaimType.STATUS_UPDATE,
            confidence=0.9,
            source_refs=[
                SourceRef(system=SourceSystem.ZOOM, external_id="mtg-42"),
                SourceRef(system=SourceSystem.SLACK, external_id="C-eng:thread"),
            ],
        )

        # Agent performance KPI worsens
        kpi_def = get_builtin_definitions()["kpi-human-correction-rate"]
        obs = KPIObservation(
            id="obs-corrections",
            kpi_definition_id=kpi_def.id,
            measured_object_id="agent",
            measured_object_type=MeasuredObjectType.AGENT,
            value=30.0,  # 30% correction rate — warning level
            health_status=kpi_def.evaluate_health(30.0),
        )
        assert obs.health_status == HealthStatus.WARNING

        # Insight about duplicate detection
        insight = KPIInsight(
            id="ins-agent-dupes",
            insight_type=InsightType.AGENT_QUALITY_ISSUE,
            title="Agent creating duplicate work items",
            summary="Same work item extracted from both Zoom and Slack. "
                    "Human correction rate = 30% (warning threshold).",
            severity=InsightSeverity.MEDIUM,
            related_kpi_observation_ids=[obs.id],
            supporting_evidence_claim_ids=[dupe_claim.id],
            confidence=0.9,
        )

        # Validate — medium severity with high confidence → auto-approved
        result = validate_insight(insight, observations=[obs], evidence_claims=[dupe_claim])
        assert result.passed is True
        assert result.outcome == ValidationOutcome.APPROVED

        # Recommendation: merge duplicates
        rec = AgentRecommendation(
            id="rec-merge-dupes",
            insight_id=insight.id,
            recommendation_type=RecommendationType.MERGE_DUPLICATES.value,
            title="Merge duplicate work items",
            recommendation_text=f"Merge '{wi_slack.title}' into '{wi_zoom.title}'",
            reasoning_summary="Same task extracted from Zoom meeting and Slack thread. "
                              "Same owner (Bob), near-identical titles.",
            target_object_ids=[wi_zoom.id, wi_slack.id],
            approval_required=False,
        )
        assert rec.recommendation_type == "merge_duplicates"
        assert len(rec.target_object_ids) == 2


# ── 6. Full Closed-Loop Test ───────────────────────────────────────


class TestClosedLoop:
    """Test the complete closed-loop control flow: activity → ontology →
    KPI → trend → insight → validation → recommendation → action."""

    def test_complete_loop(self):
        # 1. Raw activity → ontology objects
        wi = WorkItem(
            id="wi-feature-x",
            title="Implement feature X",
            status=WorkItemStatus.BLOCKED.value,
            owner_id="alice",
            linked_sources=[
                SourceRef(system=SourceSystem.LINEAR, external_id="ENG-42"),
                SourceRef(system=SourceSystem.GITHUB, external_id="PR-99"),
            ],
        )
        dep = Dependency(
            id="dep-security",
            title="Security review approval",
            status=DependencyState.BLOCKED.value,
            upstream_team_id="security",
            downstream_team_id="platform",
        )

        # 2. KPI observation
        kpi_blocked = get_builtin_definitions()["kpi-blocked-count"]
        obs_blocked = KPIObservation(
            id="obs-blocked",
            kpi_definition_id=kpi_blocked.id,
            measured_object_id="prog-1",
            value=4.0,
            health_status=kpi_blocked.evaluate_health(4.0),
        )
        assert obs_blocked.health_status == HealthStatus.WARNING

        kpi_dep_age = get_builtin_definitions()["kpi-dependency-age"]
        obs_dep = KPIObservation(
            id="obs-dep-age",
            kpi_definition_id=kpi_dep_age.id,
            measured_object_id="ms-beta",
            value=8.0,
            health_status=kpi_dep_age.evaluate_health(8.0),
        )
        assert obs_dep.health_status == HealthStatus.WATCH

        # 3. Trend
        # (both KPIs worsening over 2 weeks)

        # 4. Insight
        insight = KPIInsight(
            id="ins-beta-risk",
            insight_type=InsightType.MILESTONE_AT_RISK,
            title="Beta milestone at risk — blocked work + dependency drag",
            summary="4 work items blocked, security dependency aging 8 days. "
                    "Both KPIs worsening. Milestone likely to slip without intervention.",
            severity=InsightSeverity.HIGH,
            related_kpi_observation_ids=[obs_blocked.id, obs_dep.id],
            supporting_evidence_claim_ids=["ec-1"],
            confidence=0.85,
            human_review_required=True,
        )

        # 5. Validation
        claim = EvidenceClaim(
            id="ec-1",
            claim_text="Security team delayed due to audit",
            claim_type=ClaimType.BLOCKER,
            confidence=0.8,
        )
        result = validate_insight(
            insight,
            observations=[obs_blocked, obs_dep],
            evidence_claims=[claim],
        )
        assert result.passed is True
        assert result.human_review_required is True  # HIGH severity

        # 6. Recommendation
        rec = AgentRecommendation(
            id="rec-escalate-beta",
            insight_id=insight.id,
            recommendation_type=RecommendationType.ESCALATE_ISSUE.value,
            title="Escalate beta blocker to VP Engineering",
            recommendation_text="Security dependency blocking 4 work items. "
                                "Escalate to VP Eng with 24h response deadline.",
            reasoning_summary="Blocked count = 4 (warning), dependency age = 8 days. "
                              "Beta milestone at risk if unresolved by week end.",
            urgency=Priority.HIGH.value,
            approval_required=True,
            recommendation_status=RecommendationStatus.PROPOSED.value,
            confidence=result.adjusted_confidence,
            target_object_ids=[dep.id, wi.id],
        )

        # 7. Simulate approval and execution
        rec.accepted = True
        rec.accepted_by_id = "tpm-lead"
        rec.recommendation_status = RecommendationStatus.APPROVED.value

        rec.recommendation_status = RecommendationStatus.EXECUTED.value
        rec.executed_action_ids = ["ai-escalate-security"]

        assert rec.recommendation_status == "executed"
        assert len(rec.executed_action_ids) == 1

        # 8. Health score reflects the situation
        health = HealthScore(
            id="hs-prog-1",
            measured_object_id="prog-1",
            overall_status=HealthScore.compute_status([
                obs_blocked.health_status,
                obs_dep.health_status,
                HealthStatus.HEALTHY,  # other KPIs fine
            ]),
            score=58.0,
            worst_kpis=[kpi_blocked.id, kpi_dep_age.id],
            trend=TrendDirection.WORSENING,
        )
        assert health.overall_status == HealthStatus.WARNING

        # The loop continues: escalation creates new activity,
        # which updates ontology, which updates KPIs...
