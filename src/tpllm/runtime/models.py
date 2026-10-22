"""Live runtime models.

The orchestration layer's vocabulary: what an artifact is, what a
revision is, what changed, whether it mattered, and whether a human
should hear about it.

Two commitments shape every model here.

The first is coherence. Intelligence is published as a *revision* — a
set of artifacts computed against one evidence cutoff. A KPI from
revision 51 shown beside a scenario from revision 49, presented as one
answer, is a lie the system tells confidently. Artifacts carry their
revision id so that lie is not expressible.

The second is restraint. The runtime's purpose is to absorb
organizational complexity, not to generate work. Silence is a first-class
successful outcome, and ``NoActionReason`` exists so the system can say
*why* it stayed quiet rather than simply producing nothing.

What this layer tracks is organizational state — projects, decisions,
evidence, KPIs, dependencies, scenarios, portfolio topology, authority.
It does not track people's activity, and there is a source-level test
that keeps it that way.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from tpllm.temporal.models import build_state_fingerprint

# ── Artifacts ────────────────────────────────────────────────────────


class IntelligenceArtifactType(str, Enum):
    """Every kind of derived intelligence the runtime can recompute."""
    EVIDENCE = "evidence"
    IDENTITY = "identity"
    TEMPORAL_STATE = "temporal_state"
    PROJECT_UNDERSTANDING = "project_understanding"
    WORKSTREAM_STATE = "workstream_state"
    KPI_STATE = "kpi_state"
    LAUNCH_READINESS = "launch_readiness"
    DECISION_STATE = "decision_state"
    DECISION_CAPSULE = "decision_capsule"
    DECISION_RECOMMENDATION = "decision_recommendation"
    SCENARIO_DECISION_RECOMMENDATION = "scenario_decision_recommendation"
    SCENARIO_LATTICE = "scenario_lattice"
    AI_QUALITY = "ai_quality"
    PORTFOLIO_GRAPH = "portfolio_graph"
    PORTFOLIO_BOTTLENECKS = "portfolio_bottlenecks"
    PORTFOLIO_SCENARIO = "portfolio_scenario"
    PORTFOLIO_RECOMMENDATION = "portfolio_recommendation"
    PROCEDURAL_MEMORY = "procedural_memory"


PORTFOLIO_ARTIFACT_TYPES = frozenset({
    IntelligenceArtifactType.PORTFOLIO_GRAPH,
    IntelligenceArtifactType.PORTFOLIO_BOTTLENECKS,
    IntelligenceArtifactType.PORTFOLIO_SCENARIO,
    IntelligenceArtifactType.PORTFOLIO_RECOMMENDATION,
})

# Artifacts a governed decision depends on. If one of these is missing or
# failed, a recommendation must not be published as current and no
# autonomous action may finalize against it.
SAFETY_CRITICAL_ARTIFACT_TYPES = frozenset({
    IntelligenceArtifactType.TEMPORAL_STATE,
    IntelligenceArtifactType.DECISION_STATE,
    IntelligenceArtifactType.DECISION_CAPSULE,
    IntelligenceArtifactType.DECISION_RECOMMENDATION,
    IntelligenceArtifactType.SCENARIO_LATTICE,
    IntelligenceArtifactType.SCENARIO_DECISION_RECOMMENDATION,
    IntelligenceArtifactType.PORTFOLIO_RECOMMENDATION,
})


# ── Runtime events ───────────────────────────────────────────────────


# Classes of thing that describe a *plan*, not an observation. The temporal
# layer keeps history and prediction apart: a scheduled freeze is a commitment,
# not an observed temporal fact, and only becomes one when it happens.
PLANNED_EVENT_CLASSES = frozenset({
    "planned_milestone",
    "scheduled_freeze",
    "planned_launch",
    "commitment_made",
    "forecast",
})


class RuntimeEvidenceEvent(BaseModel):
    """A normalized envelope, ready for the orchestration path.

    ``causal_event_id`` is what lets one root cause stay one root cause:
    every artifact change derived from this event inherits it, so five
    downstream effects compress into one explanation rather than five
    alerts.

    ``subject_keys`` name what the event is *about* (a decision, a KPI, a
    blocker) so a late-arriving event can be checked against what later
    events already said about the same subject.
    """
    envelope_id: str
    project_ids: list[str] = Field(default_factory=list)
    canonical_entity_ids: list[str] = Field(default_factory=list)
    occurred_at: datetime
    received_at: datetime
    event_class: str
    material_hint: bool | None = None
    root_evidence_ids: list[str] = Field(default_factory=list)
    causal_event_id: str
    subject_keys: list[str] = Field(default_factory=list)
    asserted_state: str | None = None
    shared_resource_ids: list[str] = Field(default_factory=list)
    kpi_ids: list[str] = Field(default_factory=list)
    workstream_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    idempotency_key: str = ""

    @model_validator(mode="after")
    def _check(self) -> "RuntimeEvidenceEvent":
        if self.received_at < self.occurred_at:
            raise ValueError(
                "received_at precedes occurred_at — an event cannot be "
                "received before it happened."
            )
        if not self.causal_event_id:
            raise ValueError(
                "Every runtime event needs a causal_event_id so derived "
                "changes stay traceable to one root cause."
            )
        return self

    @property
    def is_planned(self) -> bool:
        """A plan is not an observation and never becomes temporal truth
        on its own."""
        return self.event_class in PLANNED_EVENT_CLASSES

    @property
    def arrival_lag_seconds(self) -> float:
        return (self.received_at - self.occurred_at).total_seconds()


# ── Revisions ────────────────────────────────────────────────────────


class IntelligenceRevision(BaseModel):
    """One coherent set of artifacts computed against one evidence
    cutoff.

    A revision is published only when every artifact its recompute plan
    required has succeeded. Until then it is a candidate, and the
    previous revision remains the answer.
    """
    id: str
    organization_id: str | None = None
    project_ids: list[str] = Field(default_factory=list)
    evidence_cutoff_at: datetime
    created_at: datetime
    root_evidence_ids: list[str] = Field(default_factory=list)
    parent_revision_id: str | None = None
    state_fingerprint: str
    complete: bool = False
    causal_event_ids: list[str] = Field(default_factory=list)
    required_artifact_types: list[IntelligenceArtifactType] = Field(
        default_factory=list
    )
    published_artifact_ids: list[str] = Field(default_factory=list)
    incomplete_reason: str | None = None


class ProjectIntelligenceRuntimeState(BaseModel):
    """Which artifacts currently speak for a project."""
    project_id: str
    active_revision_id: str | None = None
    temporal_state_id: str | None = None
    understanding_snapshot_id: str | None = None
    workstream_state_ids: list[str] = Field(default_factory=list)
    kpi_state_ids: list[str] = Field(default_factory=list)
    launch_readiness_id: str | None = None
    active_decision_ids: list[str] = Field(default_factory=list)
    decision_capsule_ids: list[str] = Field(default_factory=list)
    recommendation_ids: list[str] = Field(default_factory=list)
    scenario_recommendation_ids: list[str] = Field(default_factory=list)
    scenario_lattice_id: str | None = None
    ai_quality_report_id: str | None = None
    last_material_change_at: datetime | None = None
    last_refresh_at: datetime | None = None
    state_fingerprint: str = ""


class PortfolioIntelligenceRuntimeState(BaseModel):
    """Which artifacts currently speak for the portfolio."""
    active_revision_id: str | None = None
    portfolio_graph_id: str | None = None
    bottleneck_ids: list[str] = Field(default_factory=list)
    collision_ids: list[str] = Field(default_factory=list)
    portfolio_scenario_id: str | None = None
    portfolio_recommendation_ids: list[str] = Field(default_factory=list)
    affected_project_ids: list[str] = Field(default_factory=list)
    last_refresh_at: datetime | None = None
    state_fingerprint: str = ""


# ── Recompute planning ───────────────────────────────────────────────


class RecomputeScope(BaseModel):
    """What a recomputation is allowed to touch.

    Scope is the difference between refreshing one project's KPI chain
    and rebuilding the organization.
    """
    project_ids: set[str] = Field(default_factory=set)
    workstream_ids: set[str] = Field(default_factory=set)
    kpi_ids: set[str] = Field(default_factory=set)
    decision_ids: set[str] = Field(default_factory=set)
    canonical_entity_ids: set[str] = Field(default_factory=set)
    shared_resource_ids: set[str] = Field(default_factory=set)
    portfolio_refresh_required: bool = False

    @property
    def is_empty(self) -> bool:
        return not (
            self.project_ids or self.workstream_ids or self.kpi_ids
            or self.decision_ids or self.canonical_entity_ids
            or self.shared_resource_ids
        )

    def scope_key(self) -> str:
        """Deterministic identity for this scope, so two equal scopes
        merge instead of running twice."""
        return build_state_fingerprint({
            "projects": sorted(self.project_ids),
            "workstreams": sorted(self.workstream_ids),
            "kpis": sorted(self.kpi_ids),
            "decisions": sorted(self.decision_ids),
            "entities": sorted(self.canonical_entity_ids),
            "resources": sorted(self.shared_resource_ids),
            "portfolio": str(self.portfolio_refresh_required),
        })

    def merged_with(self, other: "RecomputeScope") -> "RecomputeScope":
        return RecomputeScope(
            project_ids=self.project_ids | other.project_ids,
            workstream_ids=self.workstream_ids | other.workstream_ids,
            kpi_ids=self.kpi_ids | other.kpi_ids,
            decision_ids=self.decision_ids | other.decision_ids,
            canonical_entity_ids=(
                self.canonical_entity_ids | other.canonical_entity_ids
            ),
            shared_resource_ids=(
                self.shared_resource_ids | other.shared_resource_ids
            ),
            portfolio_refresh_required=(
                self.portfolio_refresh_required
                or other.portfolio_refresh_required
            ),
        )


class ArtifactRecomputeRequest(BaseModel):
    """One artifact to refresh, and why."""
    artifact_type: IntelligenceArtifactType
    scope: RecomputeScope
    reasons: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    causal_event_ids: list[str] = Field(default_factory=list)
    input_fingerprint: str = ""
    lineage_only: bool = False

    def request_key(self) -> str:
        return f"{self.artifact_type.value}:{self.scope.scope_key()}"


class RecomputeBatch(BaseModel):
    """One coalesced plan for one revision.

    A shared incident touching fourteen projects produces one batch with
    merged scopes, not fourteen independent refresh chains that each
    rebuild the portfolio.
    """
    id: str
    revision_id: str
    root_event_ids: list[str] = Field(default_factory=list)
    causal_event_ids: list[str] = Field(default_factory=list)
    affected_project_ids: set[str] = Field(default_factory=set)
    requests: list[ArtifactRecomputeRequest] = Field(default_factory=list)
    topological_order: list[IntelligenceArtifactType] = Field(
        default_factory=list
    )
    created_at: datetime
    deferred_project_ids: list[str] = Field(default_factory=list)
    deferral_reason: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "RecomputeBatch":
        planned = {r.artifact_type for r in self.requests}
        missing = planned - set(self.topological_order)
        if missing:
            raise ValueError(
                f"Every requested artifact must appear in the topological "
                f"order; missing {sorted(t.value for t in missing)}."
            )
        return self

    def request_for(
        self, artifact_type: IntelligenceArtifactType,
    ) -> list[ArtifactRecomputeRequest]:
        return [r for r in self.requests if r.artifact_type == artifact_type]


class RecomputeFailure(BaseModel):
    """One artifact that could not be refreshed.

    Recorded rather than swallowed: a revision that silently omitted a
    failed scenario would look complete and be wrong.
    """
    artifact_type: IntelligenceArtifactType
    scope: RecomputeScope
    error_class: str
    retryable: bool = False
    explanation: str = ""

    @property
    def is_safety_critical(self) -> bool:
        return self.artifact_type in SAFETY_CRITICAL_ARTIFACT_TYPES


class RecomputedArtifact(BaseModel):
    """The result of one successful artifact refresh."""
    artifact_type: IntelligenceArtifactType
    artifact_id: str
    revision_id: str
    state_fingerprint: str
    project_ids: list[str] = Field(default_factory=list)
    changed: bool = True
    before_summary: str | None = None
    after_summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    outcome_probabilities: dict[str, float] = Field(default_factory=dict)
    previous_outcome_probabilities: dict[str, float] = Field(
        default_factory=dict
    )
    probability_confidence: float = 0.0
    decision_required: bool = False
    severity: str = "normal"


class ArtifactValidity(BaseModel):
    """Whether a published artifact still speaks for the current state."""
    artifact_id: str
    artifact_type: IntelligenceArtifactType
    revision_id: str
    status: str
    stale_reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.status == "current"


# ── Recommendation validity ──────────────────────────────────────────


class RecommendationValidityStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    INVALIDATED = "invalidated"
    SUPERSEDED = "superseded"
    RESOLVED = "resolved"


class DecisionContext(BaseModel):
    """The semantic inputs a decision recommendation rests on.

    Deliberately *semantic*: raw evidence ids are not part of it. A
    duplicate Slack message, a typo correction, or a third source
    confirming something already known changes no field here, so the
    fingerprint does not move and a good recommendation is not thrown
    away for nothing.
    """
    decision_id: str
    project_id: str
    critical_kpi_states: dict[str, str] = Field(default_factory=dict)
    hard_constraints: list[str] = Field(default_factory=list)
    option_ids: list[str] = Field(default_factory=list)
    decision_authority_id: str | None = None
    critical_unknown_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    commitment_ids: list[str] = Field(default_factory=list)
    scope_summary: str = ""
    scenario_outcome_probabilities: dict[str, float] = Field(
        default_factory=dict
    )
    decision_resolved: bool = False
    superseded_by_recommendation_id: str | None = None
    noncritical_kpi_states: dict[str, str] = Field(default_factory=dict)
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class RecommendationValidityAssessment(BaseModel):
    recommendation_id: str
    status: RecommendationValidityStatus
    recommendation_state_fingerprint: str
    current_state_fingerprint: str
    changed_inputs: list[str] = Field(default_factory=list)
    requires_recompute: bool = False
    explanation: str = ""

    @property
    def safe_to_execute(self) -> bool:
        return self.status == RecommendationValidityStatus.CURRENT


class RuntimeBlockReason(str, Enum):
    """Why a governed action did not proceed. Fail-closed by design."""
    KILL_SWITCH_ENGAGED = "kill_switch_engaged"
    STALE_RECOMMENDATION = "stale_recommendation"
    REQUIRED_ARTIFACT_UNAVAILABLE = "required_artifact_unavailable"
    AUTHORITY_DENIED = "authority_denied"
    SCENARIO_CONFIDENCE_TOO_LOW = "scenario_confidence_too_low"
    DECISION_ALREADY_RESOLVED = "decision_already_resolved"


# ── Materiality ──────────────────────────────────────────────────────


class MaterialityReason(str, Enum):
    DECISION_CHANGED = "decision_changed"
    DECISION_REQUIRED = "decision_required"
    KPI_THRESHOLD_CROSSED = "kpi_threshold_crossed"
    READINESS_MATERIAL_CHANGE = "readiness_material_change"
    SCENARIO_PROBABILITY_SHIFT = "scenario_probability_shift"
    SEVERE_FAILURE_RISK_CHANGE = "severe_failure_risk_change"
    NEW_CRITICAL_UNKNOWN = "new_critical_unknown"
    CRITICAL_UNKNOWN_RESOLVED = "critical_unknown_resolved"
    NEW_CONTRADICTION = "new_contradiction"
    NEW_PORTFOLIO_BOTTLENECK = "new_portfolio_bottleneck"
    BOTTLENECK_RESOLVED = "bottleneck_resolved"
    PORTFOLIO_PRIORITY_CHANGED = "portfolio_priority_changed"
    RECOMMENDATION_CHANGED = "recommendation_changed"
    AUTONOMOUS_ACTION_BLOCKED = "autonomous_action_blocked"


class NoActionReason(str, Enum):
    """Why the system stayed quiet. Silence with a reason is a result,
    not an absence of one."""
    DUPLICATE = "duplicate"
    NON_MATERIAL = "non_material"
    ALREADY_RESOLVED = "already_resolved"
    ALREADY_ANSWERED = "already_answered"
    NO_HUMAN_JUDGMENT_REQUIRED = "no_human_judgment_required"
    EXISTING_PLAN_SUFFICIENT = "existing_plan_sufficient"


class IntelligenceMaterialityAssessment(BaseModel):
    material: bool = False
    score: float = 0.0
    reasons: list[MaterialityReason] = Field(default_factory=list)
    affected_project_ids: list[str] = Field(default_factory=list)
    human_attention_required: bool = False
    recommended_surface_types: list[str] = Field(default_factory=list)
    explanation: str = ""
    low_confidence_qualifier: bool = False
    no_action_reasons: list[NoActionReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "IntelligenceMaterialityAssessment":
        if not (0.0 <= self.score <= 1.0):
            raise ValueError("Materiality score must be within [0, 1].")
        if self.human_attention_required and not self.material:
            raise ValueError(
                "A non-material change must not demand human attention — "
                "that is the interruption this layer exists to prevent."
            )
        return self


class ScenarioMaterialityPolicy(BaseModel):
    """When a probability movement is worth someone's attention.

    P(slip) 0.431 → 0.433 is not news. P(>7-day slip) 0.12 → 0.31 is.
    """
    minimum_absolute_probability_delta: float = 0.10
    severe_outcome_probability_delta: float = 0.05
    threshold_crossings_are_material: bool = True
    minimum_confidence_for_probability_alert: float = 0.30

    @model_validator(mode="after")
    def _check(self) -> "ScenarioMaterialityPolicy":
        for name in (
            "minimum_absolute_probability_delta",
            "severe_outcome_probability_delta",
            "minimum_confidence_for_probability_alert",
        ):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be within [0, 1].")
        return self


class ScenarioShiftAssessment(BaseModel):
    """One outcome's probability movement, judged."""
    outcome_label: str
    before_probability: float
    after_probability: float
    absolute_delta: float
    material: bool
    threshold_crossed: bool = False
    severe_outcome: bool = False
    probability_confidence: float = 0.0
    low_confidence_qualifier: bool = False
    explanation: str = ""


# ── Causality ────────────────────────────────────────────────────────


class CausalIntelligenceChange(BaseModel):
    """One artifact change, with its ancestry intact.

    The ancestry is the product: "P(slip) rose" is noise; "Platform's
    approval capacity fell, so P(slip) rose" is an explanation.
    """
    id: str
    causal_event_id: str
    root_evidence_ids: list[str] = Field(default_factory=list)
    artifact_type: IntelligenceArtifactType
    artifact_id: str
    before_summary: str | None = None
    after_summary: str = ""
    materiality: IntelligenceMaterialityAssessment
    parent_change_ids: list[str] = Field(default_factory=list)
    affected_project_ids: list[str] = Field(default_factory=list)
    revision_id: str | None = None
    dependency_edges: list[str] = Field(default_factory=list)


class CausalUpdateBundle(BaseModel):
    """Everything one root cause did, said once."""
    id: str
    causal_event_ids: list[str] = Field(default_factory=list)
    root_summary: str = ""
    material_changes: list[CausalIntelligenceChange] = Field(
        default_factory=list
    )
    affected_project_ids: list[str] = Field(default_factory=list)
    highest_severity: str = "none"
    decision_required: bool = False
    human_attention_required: bool = False
    root_evidence_ids: list[str] = Field(default_factory=list)
    all_change_ids: list[str] = Field(default_factory=list)
    low_confidence_qualifier: bool = False
    no_action_project_ids: list[str] = Field(default_factory=list)

    @property
    def is_material(self) -> bool:
        return bool(self.material_changes)


class RuntimeAttentionAction(str, Enum):
    SILENT = "silent"
    UPDATE_INTERNAL_STATE = "update_internal_state"
    ASYNC_INFORMATION = "async_information"
    DECISION_REQUEST = "decision_request"
    HIGH_PRIORITY_ALERT = "high_priority_alert"


# ── Live updates ─────────────────────────────────────────────────────


class ProjectIntelligenceUpdate(BaseModel):
    project_id: str
    revision_id: str
    what_changed: list[str] = Field(default_factory=list)
    why_it_changed: list[str] = Field(default_factory=list)
    decisions_required: list[str] = Field(default_factory=list)
    recommendation_changes: list[str] = Field(default_factory=list)
    scenario_changes: list[str] = Field(default_factory=list)
    kpi_changes: list[str] = Field(default_factory=list)
    readiness_changes: list[str] = Field(default_factory=list)
    new_risks: list[str] = Field(default_factory=list)
    resolved_risks: list[str] = Field(default_factory=list)
    research_priorities: list[str] = Field(default_factory=list)
    no_action_required: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class PortfolioIntelligenceUpdate(BaseModel):
    revision_id: str
    affected_project_ids: list[str] = Field(default_factory=list)
    new_bottlenecks: list[str] = Field(default_factory=list)
    resolved_bottlenecks: list[str] = Field(default_factory=list)
    collision_changes: list[str] = Field(default_factory=list)
    scenario_distribution_changes: list[str] = Field(default_factory=list)
    priority_changes: list[str] = Field(default_factory=list)
    highest_leverage_interventions: list[str] = Field(default_factory=list)
    decisions_required: list[str] = Field(default_factory=list)
    projects_needing_no_action: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


# ── Policy ───────────────────────────────────────────────────────────


class RuntimeResearchBudget(BaseModel):
    """Bounds the self-research loop.

    Without a bound, low confidence funds its own recursion: research
    yields a scrap, the scrap triggers a recompute, the recompute is
    still uncertain, and the loop never closes.
    """
    maximum_research_rounds_per_causal_event: int = 3
    maximum_queries_per_round: int = 5
    minimum_expected_information_gain: float = 0.10

    @model_validator(mode="after")
    def _check(self) -> "RuntimeResearchBudget":
        if self.maximum_research_rounds_per_causal_event < 0:
            raise ValueError("Research rounds cannot be negative.")
        if self.maximum_queries_per_round < 0:
            raise ValueError("Queries per round cannot be negative.")
        if not (0.0 <= self.minimum_expected_information_gain <= 1.0):
            raise ValueError(
                "minimum_expected_information_gain must be within [0, 1]."
            )
        return self


class RuntimeFanoutPolicy(BaseModel):
    """How much of a wide blast radius is handled in one pass.

    Exceeding the budget defers work explicitly; it never drops it.
    """
    maximum_projects_per_immediate_batch: int = 50
    allow_priority_tiering: bool = True
    high_priority_first: bool = True

    @model_validator(mode="after")
    def _check(self) -> "RuntimeFanoutPolicy":
        if self.maximum_projects_per_immediate_batch < 1:
            raise ValueError(
                "An immediate batch must admit at least one project."
            )
        return self


class RuntimeTopology(BaseModel):
    """How the organization is wired, as far as the runtime needs to know.

    Supplied by the caller rather than rebuilt here, so the orchestrator
    stays a scheduler and the portfolio layer stays the authority on
    cross-project structure.
    """
    projects_by_shared_resource: dict[str, list[str]] = Field(
        default_factory=dict
    )
    projects_by_kpi: dict[str, list[str]] = Field(default_factory=dict)
    projects_by_entity: dict[str, list[str]] = Field(default_factory=dict)
    high_priority_project_ids: list[str] = Field(default_factory=list)
    all_project_ids: list[str] = Field(default_factory=list)

    def projects_touching(
        self,
        *,
        resource_ids: list[str] | None = None,
        kpi_ids: list[str] | None = None,
        entity_ids: list[str] | None = None,
    ) -> set[str]:
        found: set[str] = set()
        for resource_id in resource_ids or []:
            found.update(self.projects_by_shared_resource.get(resource_id, []))
        for kpi_id in kpi_ids or []:
            found.update(self.projects_by_kpi.get(kpi_id, []))
        for entity_id in entity_ids or []:
            found.update(self.projects_by_entity.get(entity_id, []))
        return found


class RuntimePolicy(BaseModel):
    """The knobs the orchestration pass reads."""
    organization_id: str | None = None
    scenario_materiality: ScenarioMaterialityPolicy = Field(
        default_factory=ScenarioMaterialityPolicy
    )
    fanout: RuntimeFanoutPolicy = Field(default_factory=RuntimeFanoutPolicy)
    research_budget: RuntimeResearchBudget = Field(
        default_factory=RuntimeResearchBudget
    )
    topology: RuntimeTopology = Field(default_factory=RuntimeTopology)
    severe_outcome_labels: list[str] = Field(
        default_factory=lambda: [
            "severe_slip", "launch_missed", "integration_failed",
            "portfolio_multiple_projects_delayed",
        ]
    )
    severe_outcome_alert_thresholds: dict[str, float] = Field(
        default_factory=dict
    )
    skip_unchanged_artifacts: bool = True


class RuntimeMetrics(BaseModel):
    """Operational counters for the runtime itself.

    Every counter here measures TPLLM's own behaviour — how much work it
    absorbed, how much it coalesced, how often it stayed quiet. None of
    them measures a person.
    """
    events_received: int = 0
    events_deduplicated: int = 0
    late_events_processed: int = 0
    projects_affected: int = 0
    recompute_requests_generated: int = 0
    recompute_requests_coalesced: int = 0
    artifacts_recomputed: int = 0
    artifacts_skipped_unchanged: int = 0
    candidate_revisions: int = 0
    successful_revisions: int = 0
    failed_revisions: int = 0
    material_changes_detected: int = 0
    non_material_changes_suppressed: int = 0
    notifications_compressed: int = 0
    recommendations_invalidated: int = 0
    stale_autonomous_executions_blocked: int = 0
    self_research_rounds_triggered: int = 0

    def incremented(self, **counters: int) -> "RuntimeMetrics":
        return self.model_copy(update={
            name: getattr(self, name) + amount
            for name, amount in counters.items()
        })


class LiveOrchestrationResult(BaseModel):
    """What one orchestration pass did — including doing nothing."""
    revision_id: str | None = None
    events_processed: int = 0
    events_deduplicated: int = 0
    projects_affected: list[str] = Field(default_factory=list)
    artifacts_recomputed: list[str] = Field(default_factory=list)
    artifacts_skipped: list[str] = Field(default_factory=list)
    failures: list[RecomputeFailure] = Field(default_factory=list)
    project_updates: list[ProjectIntelligenceUpdate] = Field(
        default_factory=list
    )
    portfolio_update: PortfolioIntelligenceUpdate | None = None
    causal_bundles: list[CausalUpdateBundle] = Field(default_factory=list)
    attention_actions: list[RuntimeAttentionAction] = Field(
        default_factory=list
    )
    autonomous_actions_blocked: list[str] = Field(default_factory=list)
    complete: bool = False
    deferred_project_ids: list[str] = Field(default_factory=list)
    no_action_reasons: list[NoActionReason] = Field(default_factory=list)
    lineage_only_event_ids: list[str] = Field(default_factory=list)
    contested_subject_keys: list[str] = Field(default_factory=list)
    metrics: RuntimeMetrics = Field(default_factory=RuntimeMetrics)

    @property
    def stayed_silent(self) -> bool:
        return all(
            action == RuntimeAttentionAction.SILENT
            for action in self.attention_actions
        )

    @property
    def human_attention_required(self) -> bool:
        return any(
            action in (
                RuntimeAttentionAction.DECISION_REQUEST,
                RuntimeAttentionAction.HIGH_PRIORITY_ALERT,
            )
            for action in self.attention_actions
        )


# ── Fingerprints ─────────────────────────────────────────────────────


def build_project_state_fingerprint(
    *,
    project_id: str,
    understanding_snapshot_id: str | None = None,
    workstream_state_ids: list[str] | None = None,
    kpi_state_ids: list[str] | None = None,
    active_decision_ids: list[str] | None = None,
    active_blocker_ids: list[str] | None = None,
    launch_readiness_id: str | None = None,
) -> str:
    """Semantic, not identity-based: two structurally identical states
    fingerprint the same regardless of when they were built."""
    return build_state_fingerprint({
        "project": project_id,
        "understanding": understanding_snapshot_id or "",
        "workstreams": sorted(workstream_state_ids or []),
        "kpis": sorted(kpi_state_ids or []),
        "decisions": sorted(active_decision_ids or []),
        "blockers": sorted(active_blocker_ids or []),
        "readiness": launch_readiness_id or "",
    })


def build_decision_context_fingerprint(context: DecisionContext) -> str:
    """Fingerprint the inputs a recommendation actually rests on.

    Supporting evidence ids are deliberately excluded. A fourth message
    confirming a known fact must not invalidate a sound recommendation.
    """
    return build_state_fingerprint({
        "decision": context.decision_id,
        "project": context.project_id,
        "critical_kpis": sorted(
            f"{k}={v}" for k, v in context.critical_kpi_states.items()
        ),
        "constraints": sorted(context.hard_constraints),
        "options": sorted(context.option_ids),
        "authority": context.decision_authority_id or "",
        "unknowns": sorted(context.critical_unknown_ids),
        "contradictions": sorted(context.contradiction_ids),
        "commitments": sorted(context.commitment_ids),
        "scope": context.scope_summary,
        "scenario": sorted(
            f"{k}={round(v, 4)}"
            for k, v in context.scenario_outcome_probabilities.items()
        ),
        "resolved": str(context.decision_resolved),
    })


def build_scenario_input_fingerprint(
    *,
    project_id: str,
    temporal_state_fingerprint: str,
    kpi_state_ids: list[str] | None = None,
    workstream_state_ids: list[str] | None = None,
    evidence_cutoff_at: datetime | None = None,
) -> str:
    return build_state_fingerprint({
        "project": project_id,
        "temporal": temporal_state_fingerprint,
        "kpis": sorted(kpi_state_ids or []),
        "workstreams": sorted(workstream_state_ids or []),
        "cutoff": evidence_cutoff_at.isoformat() if evidence_cutoff_at else "",
    })


def build_portfolio_state_fingerprint(
    *,
    project_ids: list[str],
    shared_resource_ids: list[str] | None = None,
    bottleneck_ids: list[str] | None = None,
    capacity_signature: str = "",
) -> str:
    return build_state_fingerprint({
        "projects": sorted(project_ids),
        "resources": sorted(shared_resource_ids or []),
        "bottlenecks": sorted(bottleneck_ids or []),
        "capacity": capacity_signature,
    })


def build_revision_fingerprint(
    *,
    evidence_cutoff_at: datetime,
    project_ids: list[str],
    root_evidence_ids: list[str],
    required_artifact_types: list[IntelligenceArtifactType],
) -> str:
    return build_state_fingerprint({
        "cutoff": evidence_cutoff_at.isoformat(),
        "projects": sorted(project_ids),
        "evidence": sorted(root_evidence_ids),
        "artifacts": sorted(t.value for t in required_artifact_types),
    })
