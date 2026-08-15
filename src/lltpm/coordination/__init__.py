"""Coordination layer — organizational memory and workstream intelligence.

Durable, evidence-backed organizational state: workstreams, objectives,
decisions with explicit lifecycle, positions with provenance, action
requests with authority relations, and team-level capacity constraints.

This layer is intelligence/coordination state, not calibration authority.
It never imports from the application layer, and it models teams and
structures — never individual people's output.
"""

from lltpm.coordination.ask_router import (
    ActionRoutingPlan,
    ExpertiseRequirement,
    RoutingCandidate,
    answer_already_exists,
    build_action_routing_plan,
    identify_routing_candidates,
)
from lltpm.coordination.context_packets import (
    ContextPacket,
    build_context_packet,
)
from lltpm.coordination.decision_capsules import (
    DecisionCapsule,
    DecisionOption,
    build_decision_capsule,
)
from lltpm.coordination.decisions import (
    decision_touches_workstream,
    get_latest_conclusive_decision,
    is_decision_unresolved,
    reopen_decision,
    supersede_decision,
)
from lltpm.coordination.models import (
    ActionRequest,
    ActionRequestStatus,
    ActionRequestType,
    ActorRelationType,
    CapacityConstraintType,
    CommitmentType,
    DecisionPosition,
    OrganizationalActorRef,
    OrganizationalDecisionRecord,
    OrganizationalDecisionStatus,
    PositionType,
    TeamCapacityConstraint,
    WorkstreamCommitment,
    WorkstreamHealth,
    WorkstreamKeyDate,
    WorkstreamObjective,
    WorkstreamState,
    WorkstreamType,
)
from lltpm.coordination.meeting_intelligence import (
    DiscussionCandidate,
    DiscussionNeed,
    MaterialChange,
    MaterialChangeType,
    MeetingAgendaPlan,
    build_exception_driven_meeting_agenda,
    detect_material_changes,
    determine_discussion_need,
)
from lltpm.coordination.recommendations import (
    AgentDecisionRecommendation,
    AgentRecommendationStatus,
    DecisionConstraintType,
    DecisionCriterion,
    DecisionCriterionType,
    DecisionEvaluationAction,
    DecisionOptionAssessment,
    DecisionRationale,
    DecisionRecommendationPolicy,
    DecisionRecommendationStrength,
    HumanDecisionEvaluation,
)
from lltpm.coordination.decision_evaluation import (
    DecisionEvaluationProvider,
    build_decision_criteria,
    option_is_eligible,
    score_decision_option,
    select_agent_recommendation,
)
from lltpm.coordination.recommendation_validation import (
    DECISION_ORIGIN_HUMAN_MODIFIED_AGENT,
    DECISION_ORIGIN_HUMAN_ONLY,
    DECISION_ORIGIN_HUMAN_VALIDATED_AGENT,
    DecisionIntelligencePanel,
    DeferredDecisionResearchNeed,
    build_decision_intelligence_panel,
    build_deferral_research_need,
    finalize_human_validated_decision,
    render_panel_sections,
)
from lltpm.coordination.projections import (
    ProjectExecutiveBrief,
    build_project_executive_brief,
)
from lltpm.coordination.requests import (
    blocked_requests_for_workstream,
    is_authority_request,
    is_open_request,
    is_overdue,
    open_requests_for_workstream,
    request_touches_workstream,
)
from lltpm.coordination.workstreams import (
    build_workstream_state,
    compute_workstream_health,
    health_rank,
)

__all__ = [
    # Commit 21 — agent decision recommendations
    "AgentDecisionRecommendation",
    "AgentRecommendationStatus",
    "DECISION_ORIGIN_HUMAN_MODIFIED_AGENT",
    "DECISION_ORIGIN_HUMAN_ONLY",
    "DECISION_ORIGIN_HUMAN_VALIDATED_AGENT",
    "DecisionConstraintType",
    "DecisionCriterion",
    "DecisionCriterionType",
    "DecisionEvaluationAction",
    "DecisionEvaluationProvider",
    "DecisionIntelligencePanel",
    "DecisionOptionAssessment",
    "DecisionRationale",
    "DecisionRecommendationPolicy",
    "DecisionRecommendationStrength",
    "DeferredDecisionResearchNeed",
    "HumanDecisionEvaluation",
    "build_decision_criteria",
    "build_decision_intelligence_panel",
    "build_deferral_research_need",
    "finalize_human_validated_decision",
    "option_is_eligible",
    "render_panel_sections",
    "score_decision_option",
    "select_agent_recommendation",
    # Commit 15 — meetings, capsules, routing, packets
    "ActionRoutingPlan",
    "ContextPacket",
    "DecisionCapsule",
    "DecisionOption",
    "DiscussionCandidate",
    "DiscussionNeed",
    "ExpertiseRequirement",
    "MaterialChange",
    "MaterialChangeType",
    "MeetingAgendaPlan",
    "RoutingCandidate",
    "answer_already_exists",
    "build_action_routing_plan",
    "build_context_packet",
    "build_decision_capsule",
    "build_exception_driven_meeting_agenda",
    "detect_material_changes",
    "determine_discussion_need",
    "identify_routing_candidates",
    # Commit 14 — organizational memory
    "ActionRequest",
    "ActionRequestStatus",
    "ActionRequestType",
    "ActorRelationType",
    "CapacityConstraintType",
    "CommitmentType",
    "DecisionPosition",
    "OrganizationalActorRef",
    "OrganizationalDecisionRecord",
    "OrganizationalDecisionStatus",
    "PositionType",
    "ProjectExecutiveBrief",
    "TeamCapacityConstraint",
    "WorkstreamCommitment",
    "WorkstreamHealth",
    "WorkstreamKeyDate",
    "WorkstreamObjective",
    "WorkstreamState",
    "WorkstreamType",
    "blocked_requests_for_workstream",
    "build_project_executive_brief",
    "build_workstream_state",
    "compute_workstream_health",
    "decision_touches_workstream",
    "get_latest_conclusive_decision",
    "health_rank",
    "is_authority_request",
    "is_decision_unresolved",
    "is_open_request",
    "is_overdue",
    "open_requests_for_workstream",
    "reopen_decision",
    "request_touches_workstream",
    "supersede_decision",
]
