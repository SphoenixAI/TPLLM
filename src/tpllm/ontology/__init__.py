"""TPLLM Ontology — Palantir-aligned canonical domain model.

Architecture layers:
    1. Source Integration Layer → GitHub, Jira, Linear, Slack, Zoom, etc.
    2. Raw Source Store → Raw payloads, webhooks, transcripts
    3. Semantic Extraction → Evidence claims, signals, contradictions
    4. Canonical Ontology (this package) → WorkItem, Decision, Risk, etc.
    5. Interface / Capability Layer → Trackable, Assignable, EvidenceBacked
    6. Measurement & Operational Health → KPIs, trends, insights, health scores
    7. Reasoning Layer → Precedence rules, contradiction detection
    8. Agentic Action Layer → Triage, escalate, plan, confirm
    9. Human Review Layer → Approve ontology changes, resolve contradictions
   10. Dashboard / Board Layer → Command surface

The board is the UI. The ontology is the brain.
The integrations are senses. The measurement layer is the nervous system.
The agentic layer is the motor cortex.
"""

# Core primitives
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

# Interfaces
from tpllm.ontology.interfaces import (
    Assignable,
    Escalatable,
    EvidenceBacked,
    Reportable,
    Schedulable,
    SourceMapped,
    Trackable,
)

# Canonical entities
from tpllm.ontology.entities import (
    ActionItem,
    AgentRecommendation,
    AuthorizedBridge,
    BridgeReason,
    RecommendationStatus,
    RecommendationType,
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

# Actions
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

# Precedence
from tpllm.ontology.precedence import (
    PRECEDENCE_RULES,
    PrecedenceField,
    explain_precedence,
    get_precedence_rank,
    resolve_conflict,
)

# Mapper
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

# Measurement & Operational Health
from tpllm.ontology.measurement import (
    BUILTIN_KPI_DEFINITIONS,
    CalculationType,
    HealthScore,
    HealthStatus,
    InsightSeverity,
    InsightType,
    KPICategory,
    KPIDefinition,
    KPIInsight,
    KPIObservation,
    KPITrend,
    KPIUnit,
    MeasuredObjectType,
    TrendDirection,
    get_builtin_definitions,
    get_definitions_by_category,
)

# Validation layer
from tpllm.ontology.validation import (
    DEFAULT_POLICY,
    MergedRecommendationPair,
    ValidationOutcome,
    ValidationPolicy,
    ValidationResult,
    deduplicate_recommendations,
    validate_insight,
)

__all__ = [
    # Core
    "SourceSystem", "SourceRef", "AIExtractedValue",
    "ClaimType", "EvidenceClaim",
    "ContradictionSeverity", "ContradictionStatus", "Contradiction",
    # Interfaces
    "Trackable", "Assignable", "Schedulable", "EvidenceBacked",
    "Escalatable", "SourceMapped", "Reportable",
    # Entities
    "Person", "Team", "Program", "Project", "Workstream",
    "WorkItem", "WorkItemStatus", "Priority",
    "ActionItem", "Issue", "IssueState", "Risk", "RiskState",
    "Dependency", "DependencyState", "Decision", "DecisionStatus",
    "Requirement", "Question", "Milestone", "Deliverable", "Sprint",
    "Meeting", "Artifact", "Signal", "SignalType",
    "AgentRecommendation", "StatusReport",
    "OntologyNotification", "OntologyChangeProposal",
    "AuthorizedBridge", "BridgeReason",
    # Actions
    "ActionType", "OntologyAction",
    "TriageIssueAction", "EscalateBlockerAction",
    "ResolveDependencyAction", "ConvertRiskToIssueAction",
    "ConfirmDecisionAction", "CreateSprintPlanAction",
    "GenerateStatusReportAction", "RequestOwnerUpdateAction",
    "MarkDecisionDisputedAction", "MergeDuplicateWorkItemsAction",
    "CreateFollowUpFromMeetingAction",
    "PromoteSignalToClaimAction", "ResolveContradictionAction",
    # Precedence
    "PrecedenceField", "PRECEDENCE_RULES",
    "get_precedence_rank", "resolve_conflict", "explain_precedence",
    # Measurement & Operational Health
    "KPICategory", "KPIUnit", "CalculationType", "HealthStatus", "InsightSeverity",
    "TrendDirection", "InsightType", "MeasuredObjectType",
    "KPIDefinition", "KPIObservation", "KPITrend", "KPIInsight",
    "HealthScore", "BUILTIN_KPI_DEFINITIONS",
    "get_builtin_definitions", "get_definitions_by_category",
    # Validation
    "ValidationOutcome", "ValidationPolicy", "ValidationResult",
    "DEFAULT_POLICY", "validate_insight",
    "MergedRecommendationPair", "deduplicate_recommendations",
    # Entities (new enums)
    "RecommendationType", "RecommendationStatus",
    # Mapper
    "map_linear_issue", "map_jira_ticket", "map_github_pr",
    "map_github_pr_to_signal", "map_slack_message_to_signal",
    "map_slack_message_to_claim", "map_zoom_meeting",
    "map_zoom_action_item", "map_notion_page", "map_calendar_event",
    "map_board_task", "map_raid_risk", "map_raid_issue",
    "map_raid_dependency", "map_decision",
]
