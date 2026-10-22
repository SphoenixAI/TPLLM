"""Recommendation and artifact validity, and the autonomy guard
.

A recommendation is an answer to a question asked of a particular state
of the world. When that state moves, the answer may no longer be an
answer at all. This module makes that explicit rather than hoping the
recommendation is still fine.

The sharpest case is the race:

    T0  the agent recommends Option B, and is eligible to act
    T1  a critical KPI fails
    T2  autonomous execution finalizes the T0 recommendation

At T2 the recommendation is an artifact of a world that no longer
exists. ``guarded_autonomous_finalization`` closes that window by doing
validation and finalization in one call, in a fixed fail-closed order:
kill switch, required artifacts, staleness, decision authority,
scenario confidence — and only then the decision.

Nothing here widens authority. Every check can only refuse.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from lltpm.coordination.authority_policy import (
    OrganizationalAutonomyBudget,
    OrganizationalAutonomyState,
    OrganizationalDecisionAuthorityPolicy,
    OrganizationalDecisionRisk,
    can_agent_finalize_decision,
)
from lltpm.coordination.autonomous_decisions import finalize_agent_decision
from lltpm.coordination.scenario_decisions import (
    ScenarioDecisionPolicy,
    scenario_recommendation_is_autonomy_eligible,
)
from lltpm.runtime.models import (
    ArtifactValidity,
    DecisionContext,
    IntelligenceArtifactType,
    RecommendationValidityAssessment,
    RecommendationValidityStatus,
    RuntimeBlockReason,
    build_decision_context_fingerprint,
)

# Inputs whose movement means the recommendation must be rebuilt, not
# merely refreshed. These are the things a recommendation reasons *from*.
CRITICAL_CONTEXT_INPUTS = (
    "critical_kpi_states",
    "hard_constraints",
    "option_ids",
    "decision_authority_id",
    "critical_unknown_ids",
    "contradiction_ids",
    "commitment_ids",
    "scope_summary",
    "scenario_outcome_probabilities",
)

# Inputs that may change without disturbing a sound recommendation: more
# evidence for something already known, or a KPI nobody was reasoning
# about.
NONCRITICAL_CONTEXT_INPUTS = (
    "noncritical_kpi_states",
    "supporting_evidence_ids",
)


def _changed_inputs(
    before: DecisionContext, after: DecisionContext,
) -> list[str]:
    changed: list[str] = []
    for field in CRITICAL_CONTEXT_INPUTS + NONCRITICAL_CONTEXT_INPUTS:
        if getattr(before, field) != getattr(after, field):
            changed.append(field)
    return changed


def assess_recommendation_validity(
    *,
    recommendation: object,
    current_decision_context: DecisionContext,
    recommendation_context: DecisionContext | None = None,
) -> RecommendationValidityAssessment:
    """Does this recommendation still describe the current situation?

    The comparison is over semantic inputs, not evidence counts. A fourth
    message confirming a known fact leaves the fingerprint untouched and
    the recommendation current — throwing away good reasoning because
    someone said the same thing again is its own kind of failure.
    """
    recommendation_id = str(getattr(recommendation, "id", "unknown"))
    prior = recommendation_context or getattr(
        recommendation, "decision_context", None,
    )
    stored_fingerprint = str(
        getattr(recommendation, "state_fingerprint", "") or ""
    )
    if prior is not None:
        stored_fingerprint = build_decision_context_fingerprint(prior)

    current_fingerprint = build_decision_context_fingerprint(
        current_decision_context
    )
    changed = _changed_inputs(prior, current_decision_context) if prior else []

    def assessment(
        status: RecommendationValidityStatus,
        *,
        requires_recompute: bool,
        explanation: str,
    ) -> RecommendationValidityAssessment:
        return RecommendationValidityAssessment(
            recommendation_id=recommendation_id,
            status=status,
            recommendation_state_fingerprint=stored_fingerprint,
            current_state_fingerprint=current_fingerprint,
            changed_inputs=changed,
            requires_recompute=requires_recompute,
            explanation=explanation,
        )

    if current_decision_context.decision_resolved:
        return assessment(
            RecommendationValidityStatus.RESOLVED,
            requires_recompute=False,
            explanation=(
                "The decision has already been made; the recommendation has "
                "served its purpose and needs no further attention."
            ),
        )

    superseding = current_decision_context.superseded_by_recommendation_id
    if superseding and superseding != recommendation_id:
        return assessment(
            RecommendationValidityStatus.SUPERSEDED,
            requires_recompute=False,
            explanation=(
                f"Recommendation '{superseding}' has replaced this one."
            ),
        )

    if stored_fingerprint == current_fingerprint:
        return assessment(
            RecommendationValidityStatus.CURRENT,
            requires_recompute=False,
            explanation=(
                "Every input this recommendation reasons from is unchanged."
            ),
        )

    critical = [c for c in changed if c in CRITICAL_CONTEXT_INPUTS]
    if critical:
        return assessment(
            RecommendationValidityStatus.INVALIDATED,
            requires_recompute=True,
            explanation=(
                f"Input(s) the recommendation reasons from changed: "
                f"{', '.join(critical)}. The recommendation must be rebuilt "
                f"before it can be acted on."
            ),
        )

    return assessment(
        RecommendationValidityStatus.STALE,
        requires_recompute=True,
        explanation=(
            f"Peripheral input(s) changed ({', '.join(changed) or 'unknown'});"
            f" the recommendation should be refreshed but its reasoning is "
            f"not contradicted."
        ),
    )


def assess_artifact_validity(
    *,
    artifact_id: str,
    artifact_type: IntelligenceArtifactType,
    revision_id: str,
    current_revision_id: str | None,
    failed_artifact_types: set[IntelligenceArtifactType] | None = None,
    stale_dependency_types: set[IntelligenceArtifactType] | None = None,
) -> ArtifactValidity:
    """Generalize validity beyond recommendations.

    A readiness scorecard built before a blocker was resolved is stale
    for the same reason a recommendation is: its inputs moved.
    """
    failed = failed_artifact_types or set()
    stale_dependencies = stale_dependency_types or set()

    if artifact_type in failed:
        return ArtifactValidity(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            revision_id=revision_id,
            status="unavailable",
            stale_reason=(
                f"{artifact_type.value} failed to recompute in revision "
                f"{revision_id} and is not available."
            ),
        )
    if stale_dependencies:
        return ArtifactValidity(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            revision_id=revision_id,
            status="stale",
            stale_reason=(
                f"Depends on "
                f"{', '.join(sorted(t.value for t in stale_dependencies))}, "
                f"which changed."
            ),
        )
    if current_revision_id is not None and revision_id != current_revision_id:
        return ArtifactValidity(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            revision_id=revision_id,
            status="stale",
            stale_reason=(
                f"Belongs to revision {revision_id}; the current revision is "
                f"{current_revision_id}."
            ),
        )
    return ArtifactValidity(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        revision_id=revision_id,
        status="current",
    )


# ── The autonomy guard ───────────────────────────────────────────────


class GuardedExecutionResult(BaseModel):
    """The outcome of one guarded autonomous finalization attempt."""
    recommendation_id: str
    executed: bool = False
    block_reason: RuntimeBlockReason | None = None
    explanation: str = ""
    validity: RecommendationValidityAssessment | None = None
    authority_blockers: list[str] = Field(default_factory=list)
    decision_id: str | None = None
    execution_id: str | None = None
    requires_fresh_recommendation: bool = False

    @property
    def blocked(self) -> bool:
        return not self.executed


def guarded_autonomous_finalization(
    *,
    recommendation: object,
    capsule: object,
    policy: OrganizationalDecisionAuthorityPolicy,
    decision_risk: OrganizationalDecisionRisk,
    current_decision_context: DecisionContext,
    recommendation_context: DecisionContext | None = None,
    recent_autonomous_decisions: list[object] | None = None,
    now: datetime,
    autonomy_state: OrganizationalAutonomyState | None = None,
    budget: OrganizationalAutonomyBudget | None = None,
    decision_type: str | None = None,
    scenario_recommendation: object = None,
    scenario_policy: ScenarioDecisionPolicy | None = None,
    required_artifact_types: set[IntelligenceArtifactType] | None = None,
    available_artifact_types: set[IntelligenceArtifactType] | None = None,
) -> tuple[GuardedExecutionResult, tuple | None]:
    """Validate and finalize as one operation.

    Splitting these leaves a window in which the world can move between
    the check and the act. Everything below runs in fixed order inside a
    single call, and every branch refuses rather than proceeds:

    1. the kill switch, before anything else
    2. the artifacts the decision depends on actually exist
    3. the recommendation still describes the current state
    4. decision authority, re-checked against this moment
    5. scenario confidence, when scenario analysis drove the choice

    Returns the result and, only on success, the finalization
    tuple.
    """
    recommendation_id = str(getattr(recommendation, "id", "unknown"))

    def blocked(
        reason: RuntimeBlockReason,
        explanation: str,
        *,
        validity: RecommendationValidityAssessment | None = None,
        authority_blockers: list[str] | None = None,
        requires_fresh: bool = False,
    ) -> tuple[GuardedExecutionResult, None]:
        return (
            GuardedExecutionResult(
                recommendation_id=recommendation_id,
                executed=False,
                block_reason=reason,
                explanation=explanation,
                validity=validity,
                authority_blockers=list(authority_blockers or []),
                requires_fresh_recommendation=requires_fresh,
            ),
            None,
        )

    # 1. Kill switch short-circuits everything, always first.
    if autonomy_state is not None and (
        autonomy_state.kill_switch_engaged or not autonomy_state.enabled
    ):
        return blocked(
            RuntimeBlockReason.KILL_SWITCH_ENGAGED,
            "Organizational autonomy is halted; the decision returns to "
            "humans regardless of how current the recommendation is."
            + (f" Reason: {autonomy_state.reason}"
               if autonomy_state.reason else ""),
        )

    # 2. A decision must not be finalized against a missing artifact.
    required = required_artifact_types or set()
    available = available_artifact_types
    if available is not None:
        missing = required - available
        if missing:
            return blocked(
                RuntimeBlockReason.REQUIRED_ARTIFACT_UNAVAILABLE,
                f"Required artifact(s) unavailable: "
                f"{', '.join(sorted(t.value for t in missing))}. Acting "
                f"without them would mean deciding on an incomplete picture.",
                requires_fresh=True,
            )

    # 3. Staleness — the race this guard exists for.
    validity = assess_recommendation_validity(
        recommendation=recommendation,
        current_decision_context=current_decision_context,
        recommendation_context=recommendation_context,
    )
    if validity.status == RecommendationValidityStatus.RESOLVED:
        return blocked(
            RuntimeBlockReason.DECISION_ALREADY_RESOLVED,
            "The decision has already been made; there is nothing to "
            "finalize.",
            validity=validity,
        )
    if validity.status != RecommendationValidityStatus.CURRENT:
        return blocked(
            RuntimeBlockReason.STALE_RECOMMENDATION,
            f"Recommendation state has moved since it was produced "
            f"(was {validity.recommendation_state_fingerprint}, now "
            f"{validity.current_state_fingerprint}). "
            f"{validity.explanation} Recompute the recommendation and "
            f"re-check authority before any autonomous action.",
            validity=validity,
            requires_fresh=True,
        )

    # 4. The authority policy decides; this layer only asks again.
    assessment = can_agent_finalize_decision(
        recommendation=recommendation,
        capsule=capsule,
        policy=policy,
        decision_risk=decision_risk,
        recent_autonomous_decisions=list(recent_autonomous_decisions or []),
        now=now,
        autonomy_state=autonomy_state,
        budget=budget,
        decision_type=decision_type,
    )
    if not assessment.autonomous_eligible:
        return blocked(
            RuntimeBlockReason.AUTHORITY_DENIED,
            f"Authority policy denied autonomous finalization: "
            f"{assessment.authority_reason}",
            validity=validity,
            authority_blockers=list(assessment.blockers),
        )

    # 5. Scenario confidence can only narrow eligibility further.
    if scenario_recommendation is not None:
        eligible, reason = scenario_recommendation_is_autonomy_eligible(
            recommendation=scenario_recommendation,
            policy=scenario_policy or ScenarioDecisionPolicy(),
        )
        if not eligible:
            return blocked(
                RuntimeBlockReason.SCENARIO_CONFIDENCE_TOO_LOW,
                f"Scenario gate refused autonomous action: {reason}",
                validity=validity,
            )

    finalization = finalize_agent_decision(
        recommendation=recommendation,
        capsule=capsule,
        authority_assessment=assessment,
        policy=policy,
        now=now,
    )
    record, _snapshot, execution = finalization
    return (
        GuardedExecutionResult(
            recommendation_id=recommendation_id,
            executed=True,
            explanation=(
                "Recommendation was current, required artifacts were "
                "available, and authority was re-checked at the moment of "
                "execution."
            ),
            validity=validity,
            decision_id=record.id,
            execution_id=execution.id,
        ),
        finalization,
    )
