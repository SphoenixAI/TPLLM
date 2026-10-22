"""Measurement & Operational Health — KPIs as first-class ontology objects.

KPIs are how the agent knows whether the project is healthy, drifting,
blocked, improving, or lying to itself. They sit beside work, risks,
and decisions as a measurement layer — not buried as random fields
on project objects.

Architecture position:
    Raw project activity → ontology objects → derived KPI observations
    → trends → alerts → agent recommendations → action items

Design: Split into Definition (what you measure) and Observation (one
value at one moment). Observations carry evidence, so a KPI is not just
a number — it traces back to the claims that produced it.

KPI categories:
    delivery          — is work moving? (sprint completion, throughput, cycle time)
    execution_health  — is the program operationally healthy? (blocked count, blocker age)
    decision          — are decisions being made? (latency, unowned, disputed)
    dependency        — how tangled is the program? (external deps, age, satisfaction)
    risk              — future danger level (open count, materialization rate)
    ownership         — accountability clarity (unowned work, conflicting owners)
    communication     — is communication creating alignment? (follow-up rate, contradictions)
    quality           — execution quality (bug count, reopen rate, review cycle time)
    business          — product/business outcomes (adoption, SLA, conversion)
    agent_performance — is the agent itself improving? (correction rate, precision)
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from tpllm.ontology.core import SourceRef
from tpllm.ontology.interfaces import EvidenceBacked, Trackable


# ── Enums ───────────────────────────────────────────────────────────

class KPICategory(str, Enum):
    DELIVERY = "delivery"
    EXECUTION_HEALTH = "execution_health"
    DECISION = "decision"
    DEPENDENCY = "dependency"
    RISK = "risk"
    OWNERSHIP = "ownership"
    COMMUNICATION = "communication"
    QUALITY = "quality"
    BUSINESS = "business"
    AGENT_PERFORMANCE = "agent_performance"


class KPIUnit(str, Enum):
    COUNT = "count"
    PERCENT = "percent"
    DAYS = "days"
    HOURS = "hours"
    SCORE = "score"
    CURRENCY = "currency"
    BOOLEAN = "boolean"
    RATIO = "ratio"


class CalculationType(str, Enum):
    DERIVED = "derived"
    SOURCE_IMPORTED = "source_imported"
    AI_INFERRED = "ai_inferred"
    MANUAL = "manual"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    WATCH = "watch"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class TrendDirection(str, Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    WORSENING = "worsening"
    VOLATILE = "volatile"
    UNKNOWN = "unknown"


class InsightType(str, Enum):
    MILESTONE_AT_RISK = "milestone_at_risk"
    DECISION_BOTTLENECK = "decision_bottleneck"
    DEPENDENCY_DRAG = "dependency_drag"
    OWNERSHIP_GAP = "ownership_gap"
    SCOPE_CHURN = "scope_churn"
    EXECUTION_SLOWDOWN = "execution_slowdown"
    COMMUNICATION_GAP = "communication_gap"
    QUALITY_REGRESSION = "quality_regression"
    RISK_ACCELERATION = "risk_acceleration"
    AGENT_QUALITY_ISSUE = "agent_quality_issue"
    POSITIVE_MOMENTUM = "positive_momentum"


class MeasuredObjectType(str, Enum):
    PROGRAM = "program"
    PROJECT = "project"
    WORKSTREAM = "workstream"
    MILESTONE = "milestone"
    TEAM = "team"
    PERSON = "person"
    SPRINT = "sprint"
    AGENT = "agent"


# ── KPI Definition ──────────────────────────────────────────────────

class KPIDefinition(BaseModel):
    """What you are measuring — the template for observations.

    Defines the metric name, category, unit, calculation method,
    and thresholds. Observations are instances of this definition
    measured at a point in time against a specific object.
    """
    id: str = ""
    name: str = ""
    category: KPICategory = KPICategory.EXECUTION_HEALTH
    description: str = ""
    unit: KPIUnit = KPIUnit.COUNT
    applies_to: list[str] = Field(default_factory=list)
    calculation_type: CalculationType = CalculationType.DERIVED
    calculation_method: str = ""
    target_value: float | None = None
    target_direction: str = "lower_is_better"
    warning_threshold: float | None = None
    critical_threshold: float | None = None
    owner_team_id: str = ""
    source_systems: list[str] = Field(default_factory=list)
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def evaluate_health(self, value: float) -> HealthStatus:
        """Determine health status for an observed value."""
        if self.critical_threshold is not None:
            if self.target_direction == "lower_is_better" and value >= self.critical_threshold:
                return HealthStatus.CRITICAL
            if self.target_direction == "higher_is_better" and value <= self.critical_threshold:
                return HealthStatus.CRITICAL

        if self.warning_threshold is not None:
            if self.target_direction == "lower_is_better" and value >= self.warning_threshold:
                return HealthStatus.WARNING
            if self.target_direction == "higher_is_better" and value <= self.warning_threshold:
                return HealthStatus.WARNING

        if self.target_value is not None:
            if self.target_direction == "lower_is_better" and value > self.target_value:
                return HealthStatus.WATCH
            if self.target_direction == "higher_is_better" and value < self.target_value:
                return HealthStatus.WATCH

        return HealthStatus.HEALTHY


# ── KPI Observation ─────────────────────────────────────────────────

class KPIObservation(EvidenceBacked):
    """One logged value at one moment in time.

    Not just a number — carries evidence tracing back to the ontology
    objects and claims that produced it. This is what makes the
    measurement layer trustworthy.
    """
    id: str = ""
    kpi_definition_id: str = ""
    measured_object_id: str = ""
    measured_object_type: MeasuredObjectType = MeasuredObjectType.PROGRAM
    value: float = 0.0
    unit: KPIUnit = KPIUnit.COUNT
    health_status: HealthStatus = HealthStatus.UNKNOWN
    observed_at: datetime = Field(default_factory=datetime.utcnow)
    period_start: datetime | None = None
    period_end: datetime | None = None
    generated_by: CalculationType = CalculationType.DERIVED
    detail: str = ""


# ── KPI Trend ───────────────────────────────────────────────────────

class KPITrend(BaseModel):
    """Direction of a KPI over time — summarizes multiple observations.

    Detects whether a metric is improving, worsening, stable, or volatile.
    """
    id: str = ""
    kpi_definition_id: str = ""
    measured_object_id: str = ""
    direction: TrendDirection = TrendDirection.UNKNOWN
    change_percent: float | None = None
    current_value: float | None = None
    previous_value: float | None = None
    summary: str = ""
    observation_ids: list[str] = Field(default_factory=list)
    observation_count: int = 0
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    period_days: int = 7


# ── KPI Insight ─────────────────────────────────────────────────────

class InsightSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class KPIInsight(EvidenceBacked):
    """Cross-KPI analysis that explains WHY a metric changed.

    Connects multiple observations into a narrative: "The project is
    not blocked by engineering execution — it's blocked by unresolved
    product/security decisions."

    The severity and human_review_required fields form the validation
    gate between insights and recommendations. High-severity insights
    with low confidence require human review before the agent acts.
    """
    id: str = ""
    insight_type: InsightType = InsightType.EXECUTION_SLOWDOWN
    title: str = ""
    summary: str = ""
    severity: InsightSeverity = InsightSeverity.MEDIUM
    related_kpi_observation_ids: list[str] = Field(default_factory=list)
    supporting_evidence_claim_ids: list[str] = Field(default_factory=list)
    affected_object_ids: list[str] = Field(default_factory=list)
    recommended_action_ids: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    detected_at: datetime = Field(default_factory=datetime.utcnow)


# ── Health Score ────────────────────────────────────────────────────

class HealthScore(BaseModel):
    """Aggregate health for a measured object across all its KPIs.

    Rolls up individual KPI statuses into an overall health picture
    with the worst-status KPIs surfaced as the reason.
    """
    id: str = ""
    measured_object_id: str = ""
    measured_object_type: MeasuredObjectType = MeasuredObjectType.PROGRAM
    overall_status: HealthStatus = HealthStatus.UNKNOWN
    score: float = Field(default=0.0, ge=0.0, le=100.0)
    kpi_summary: list[dict] = Field(default_factory=list)
    worst_kpis: list[str] = Field(default_factory=list)
    trend: TrendDirection = TrendDirection.UNKNOWN
    computed_at: datetime = Field(default_factory=datetime.utcnow)

    @staticmethod
    def compute_status(kpi_statuses: list[HealthStatus]) -> HealthStatus:
        """Compute overall status from individual KPI statuses."""
        if not kpi_statuses:
            return HealthStatus.UNKNOWN
        if HealthStatus.CRITICAL in kpi_statuses:
            return HealthStatus.CRITICAL
        if HealthStatus.WARNING in kpi_statuses:
            return HealthStatus.WARNING
        if HealthStatus.WATCH in kpi_statuses:
            return HealthStatus.WATCH
        return HealthStatus.HEALTHY


# ── Built-in KPI Definitions ───────────────────────────────────────
# These are the default TPM KPIs the agent starts with.
# Users can add custom definitions; these provide immediate value.

BUILTIN_KPI_DEFINITIONS: list[KPIDefinition] = [
    # ── Delivery ────────────────────────────────────────────────
    KPIDefinition(
        id="kpi-sprint-completion",
        name="Sprint Completion Rate",
        category=KPICategory.DELIVERY,
        description="Percentage of sprint work items completed by sprint end",
        unit=KPIUnit.PERCENT,
        applies_to=["sprint", "program"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="done_tasks / total_sprint_tasks * 100",
        target_value=85.0,
        target_direction="higher_is_better",
        warning_threshold=60.0,
        critical_threshold=40.0,
    ),
    KPIDefinition(
        id="kpi-work-item-throughput",
        name="Work Item Throughput",
        category=KPICategory.DELIVERY,
        description="Number of work items completed per week",
        unit=KPIUnit.COUNT,
        applies_to=["program", "team"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(work_items completed in period)",
        target_direction="higher_is_better",
    ),
    KPIDefinition(
        id="kpi-milestone-completion",
        name="Milestone Completion",
        category=KPICategory.DELIVERY,
        description="Percentage of milestones completed on time",
        unit=KPIUnit.PERCENT,
        applies_to=["program"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="completed_milestones / total_milestones * 100",
        target_value=90.0,
        target_direction="higher_is_better",
        warning_threshold=70.0,
        critical_threshold=50.0,
    ),

    # ── Execution Health ────────────────────────────────────────
    KPIDefinition(
        id="kpi-blocked-count",
        name="Blocked Work Item Count",
        category=KPICategory.EXECUTION_HEALTH,
        description="Number of currently blocked work items",
        unit=KPIUnit.COUNT,
        applies_to=["program", "sprint", "team"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(work_items where status = blocked)",
        target_value=0.0,
        target_direction="lower_is_better",
        warning_threshold=3.0,
        critical_threshold=5.0,
    ),
    KPIDefinition(
        id="kpi-avg-blocker-age",
        name="Average Blocker Age",
        category=KPICategory.EXECUTION_HEALTH,
        description="Average number of days work items have been blocked",
        unit=KPIUnit.DAYS,
        applies_to=["program", "milestone"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="avg(days_since_blocked for blocked items)",
        target_value=2.0,
        target_direction="lower_is_better",
        warning_threshold=5.0,
        critical_threshold=10.0,
    ),
    KPIDefinition(
        id="kpi-overdue-count",
        name="Overdue Work Item Count",
        category=KPICategory.EXECUTION_HEALTH,
        description="Number of work items past their due date",
        unit=KPIUnit.COUNT,
        applies_to=["program", "sprint", "team"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(work_items where is_overdue = true)",
        target_value=0.0,
        target_direction="lower_is_better",
        warning_threshold=3.0,
        critical_threshold=8.0,
    ),
    KPIDefinition(
        id="kpi-stale-item-count",
        name="Stale Work Item Count",
        category=KPICategory.EXECUTION_HEALTH,
        description="Work items with no update in 7+ days",
        unit=KPIUnit.COUNT,
        applies_to=["program", "team"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(open work_items where last_updated > 7 days ago)",
        target_value=0.0,
        target_direction="lower_is_better",
        warning_threshold=5.0,
        critical_threshold=10.0,
    ),

    # ── Decision ────────────────────────────────────────────────
    KPIDefinition(
        id="kpi-decision-latency",
        name="Decision Latency",
        category=KPICategory.DECISION,
        description="Average days from decision proposed to decided",
        unit=KPIUnit.DAYS,
        applies_to=["program", "project"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="avg(decided_at - created_at for decided decisions)",
        target_value=3.0,
        target_direction="lower_is_better",
        warning_threshold=5.0,
        critical_threshold=10.0,
    ),
    KPIDefinition(
        id="kpi-open-decisions",
        name="Open Decision Count",
        category=KPICategory.DECISION,
        description="Decisions awaiting resolution",
        unit=KPIUnit.COUNT,
        applies_to=["program"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(decisions where status not in [decided, communicated, reversed])",
        target_value=3.0,
        target_direction="lower_is_better",
        warning_threshold=5.0,
        critical_threshold=10.0,
    ),
    KPIDefinition(
        id="kpi-decisions-without-owner",
        name="Unowned Decisions",
        category=KPICategory.DECISION,
        description="Decisions with no assigned decision owner",
        unit=KPIUnit.COUNT,
        applies_to=["program"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(open decisions where decision_owner_id = empty)",
        target_value=0.0,
        target_direction="lower_is_better",
        warning_threshold=2.0,
        critical_threshold=5.0,
    ),

    # ── Dependency ──────────────────────────────────────────────
    KPIDefinition(
        id="kpi-open-dependencies",
        name="Open Dependency Count",
        category=KPICategory.DEPENDENCY,
        description="Unresolved dependencies across the program",
        unit=KPIUnit.COUNT,
        applies_to=["program", "milestone"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(dependencies where status != resolved)",
        target_direction="lower_is_better",
        warning_threshold=5.0,
        critical_threshold=10.0,
    ),
    KPIDefinition(
        id="kpi-dependency-age",
        name="Critical Dependency Age",
        category=KPICategory.DEPENDENCY,
        description="Days the oldest unresolved critical-path dependency has been open",
        unit=KPIUnit.DAYS,
        applies_to=["program", "milestone"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="max(days_open for unresolved dependencies on critical path)",
        target_value=7.0,
        target_direction="lower_is_better",
        warning_threshold=14.0,
        critical_threshold=21.0,
    ),

    # ── Risk ────────────────────────────────────────────────────
    KPIDefinition(
        id="kpi-open-risks",
        name="Open Risk Count",
        category=KPICategory.RISK,
        description="Number of identified but unresolved risks",
        unit=KPIUnit.COUNT,
        applies_to=["program"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(risks where status not in [resolved, accepted])",
        target_direction="lower_is_better",
        warning_threshold=5.0,
        critical_threshold=10.0,
    ),
    KPIDefinition(
        id="kpi-unmitigated-risks",
        name="Unmitigated Risk Count",
        category=KPICategory.RISK,
        description="Risks with no mitigation plan defined",
        unit=KPIUnit.COUNT,
        applies_to=["program"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(open risks where mitigation = empty)",
        target_value=0.0,
        target_direction="lower_is_better",
        warning_threshold=2.0,
        critical_threshold=5.0,
    ),
    KPIDefinition(
        id="kpi-risk-materialization-rate",
        name="Risk Materialization Rate",
        category=KPICategory.RISK,
        description="Percentage of identified risks that became issues",
        unit=KPIUnit.PERCENT,
        applies_to=["program"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="risks_became_issues / total_risks * 100",
        target_value=20.0,
        target_direction="lower_is_better",
        warning_threshold=40.0,
        critical_threshold=60.0,
    ),

    # ── Ownership ───────────────────────────────────────────────
    KPIDefinition(
        id="kpi-ownership-clarity",
        name="Ownership Clarity",
        category=KPICategory.OWNERSHIP,
        description="Percentage of active critical objects with exactly one owner",
        unit=KPIUnit.PERCENT,
        applies_to=["program", "team"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="owned_critical_objects / total_critical_objects * 100",
        target_value=95.0,
        target_direction="higher_is_better",
        warning_threshold=80.0,
        critical_threshold=60.0,
    ),
    KPIDefinition(
        id="kpi-unowned-work",
        name="Unowned Work Count",
        category=KPICategory.OWNERSHIP,
        description="Open work items with no assigned owner",
        unit=KPIUnit.COUNT,
        applies_to=["program", "sprint"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(open work_items where owner_id = empty)",
        target_value=0.0,
        target_direction="lower_is_better",
        warning_threshold=3.0,
        critical_threshold=8.0,
    ),

    # ── Communication ───────────────────────────────────────────
    KPIDefinition(
        id="kpi-followup-completion",
        name="Meeting Follow-up Completion Rate",
        category=KPICategory.COMMUNICATION,
        description="Percentage of meeting action items completed by due date",
        unit=KPIUnit.PERCENT,
        applies_to=["program", "team"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="completed_action_items / total_action_items * 100",
        target_value=80.0,
        target_direction="higher_is_better",
        warning_threshold=60.0,
        critical_threshold=40.0,
    ),
    KPIDefinition(
        id="kpi-contradiction-count",
        name="Active Contradiction Count",
        category=KPICategory.COMMUNICATION,
        description="Unresolved contradictions between evidence claims",
        unit=KPIUnit.COUNT,
        applies_to=["program"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(contradictions where status = open)",
        target_value=0.0,
        target_direction="lower_is_better",
        warning_threshold=3.0,
        critical_threshold=5.0,
    ),
    KPIDefinition(
        id="kpi-unanswered-questions",
        name="Unanswered Question Count",
        category=KPICategory.COMMUNICATION,
        description="Questions blocking work that have no answer yet",
        unit=KPIUnit.COUNT,
        applies_to=["program"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="count(questions where answer = empty)",
        target_value=0.0,
        target_direction="lower_is_better",
        warning_threshold=5.0,
        critical_threshold=10.0,
    ),

    # ── Quality ─────────────────────────────────────────────────
    KPIDefinition(
        id="kpi-pr-review-cycle",
        name="PR Review Cycle Time",
        category=KPICategory.QUALITY,
        description="Average hours from PR opened to review complete",
        unit=KPIUnit.HOURS,
        applies_to=["team", "project"],
        calculation_type=CalculationType.SOURCE_IMPORTED,
        calculation_method="avg(review_completed_at - pr_opened_at)",
        source_systems=["github"],
        target_value=24.0,
        target_direction="lower_is_better",
        warning_threshold=48.0,
        critical_threshold=96.0,
    ),

    # ── Agent Performance ───────────────────────────────────────
    KPIDefinition(
        id="kpi-human-correction-rate",
        name="Human Correction Rate",
        category=KPICategory.AGENT_PERFORMANCE,
        description="Percentage of AI-created objects edited or rejected by a human",
        unit=KPIUnit.PERCENT,
        applies_to=["agent"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="corrected_objects / ai_created_objects * 100",
        target_value=10.0,
        target_direction="lower_is_better",
        warning_threshold=25.0,
        critical_threshold=50.0,
    ),
    KPIDefinition(
        id="kpi-alert-fatigue-rate",
        name="Alert Fatigue Rate",
        category=KPICategory.AGENT_PERFORMANCE,
        description="Percentage of alerts ignored or dismissed without action",
        unit=KPIUnit.PERCENT,
        applies_to=["agent"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="dismissed_alerts / total_alerts * 100",
        target_value=15.0,
        target_direction="lower_is_better",
        warning_threshold=30.0,
        critical_threshold=50.0,
    ),
    KPIDefinition(
        id="kpi-extraction-precision",
        name="Action Extraction Precision",
        category=KPICategory.AGENT_PERFORMANCE,
        description="Percentage of auto-extracted action items that are valid",
        unit=KPIUnit.PERCENT,
        applies_to=["agent"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="valid_extractions / total_extractions * 100",
        target_value=90.0,
        target_direction="higher_is_better",
        warning_threshold=75.0,
        critical_threshold=60.0,
    ),

    # ── Agent Performance: Validation Quality ───────────────────
    # These measure the validation gate itself — how the agent's insights
    # fare as they pass through validate_insight(). They turn the validator
    # into an observable, improvable subsystem.
    KPIDefinition(
        id="kpi-insight-approval-rate",
        name="Insight Approval Rate",
        category=KPICategory.AGENT_PERFORMANCE,
        description="Percentage of validated insights that pass the gate (auto or for review)",
        unit=KPIUnit.PERCENT,
        applies_to=["agent"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="passed_validations / total_validations * 100",
        target_value=70.0,
        target_direction="higher_is_better",
        warning_threshold=50.0,
        critical_threshold=30.0,
    ),
    KPIDefinition(
        id="kpi-insight-rejection-rate",
        name="Insight Rejection Rate",
        category=KPICategory.AGENT_PERFORMANCE,
        description="Percentage of validated insights rejected by the gate",
        unit=KPIUnit.PERCENT,
        applies_to=["agent"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="rejected_validations / total_validations * 100",
        target_value=15.0,
        target_direction="lower_is_better",
        warning_threshold=30.0,
        critical_threshold=50.0,
    ),
    KPIDefinition(
        id="kpi-human-review-rate",
        name="Human Review Rate",
        category=KPICategory.AGENT_PERFORMANCE,
        description="Percentage of insights that require human review before action",
        unit=KPIUnit.PERCENT,
        applies_to=["agent"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="human_review_required / total_validations * 100",
        target_value=20.0,
        target_direction="lower_is_better",
        warning_threshold=40.0,
        critical_threshold=60.0,
    ),
    KPIDefinition(
        id="kpi-missing-evidence-rate",
        name="Missing Evidence Rate",
        category=KPICategory.AGENT_PERFORMANCE,
        description="Percentage of insights rejected for insufficient evidence",
        unit=KPIUnit.PERCENT,
        applies_to=["agent"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="insufficient_evidence_validations / total_validations * 100",
        target_value=5.0,
        target_direction="lower_is_better",
        warning_threshold=15.0,
        critical_threshold=30.0,
    ),
    KPIDefinition(
        id="kpi-stale-insight-rate",
        name="Stale Insight Rate",
        category=KPICategory.AGENT_PERFORMANCE,
        description="Percentage of insights rejected because all evidence was stale",
        unit=KPIUnit.PERCENT,
        applies_to=["agent"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="stale_evidence_validations / total_validations * 100",
        target_value=5.0,
        target_direction="lower_is_better",
        warning_threshold=15.0,
        critical_threshold=25.0,
    ),
    KPIDefinition(
        id="kpi-auto-approval-rate",
        name="Auto-Approval Rate",
        category=KPICategory.AGENT_PERFORMANCE,
        description="Percentage of insights auto-approved without requiring human review",
        unit=KPIUnit.PERCENT,
        applies_to=["agent"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="auto_approved_validations / total_validations * 100",
        target_value=50.0,
        target_direction="higher_is_better",
        warning_threshold=30.0,
        critical_threshold=15.0,
    ),
    KPIDefinition(
        id="kpi-contradiction-rejection-rate",
        name="Contradiction Rejection Rate",
        category=KPICategory.AGENT_PERFORMANCE,
        description="Percentage of insights rejected due to contradictory evidence",
        unit=KPIUnit.PERCENT,
        applies_to=["agent"],
        calculation_type=CalculationType.DERIVED,
        calculation_method="contradicted_validations / total_validations * 100",
        target_value=5.0,
        target_direction="lower_is_better",
        warning_threshold=15.0,
        critical_threshold=30.0,
    ),
]


def get_builtin_definitions() -> dict[str, KPIDefinition]:
    """Return built-in KPI definitions keyed by ID."""
    return {d.id: d for d in BUILTIN_KPI_DEFINITIONS}


def get_definitions_by_category(category: KPICategory) -> list[KPIDefinition]:
    """Return built-in KPI definitions for a specific category."""
    return [d for d in BUILTIN_KPI_DEFINITIONS if d.category == category]
