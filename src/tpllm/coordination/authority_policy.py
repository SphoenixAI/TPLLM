"""Organizational decision authority policy.

The recommendation layer lets the agent recommend. This decides who
may turn a
recommendation into a conclusive organizational decision.

This is a separate authority domain from calibration
governance — organizational project decisions are not calibration rule
application, and ``is_application_eligible`` is deliberately not
reused.

Invariants:
- Human validation is the default; autonomy is never inferred, only
  explicitly configured (never from headcount).
- Autonomy is scoped by decision type, project/workstream, risk
  ceiling, confidence, option margin, reversibility, and a daily
  budget. Every check fails closed.
- Certain decision categories (personnel, legal, compensation,
  security credentials, major finance) can never be autonomous,
  regardless of confidence.
- The agent can never expand its own authority.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from tpllm.coordination.decision_capsules import DecisionCapsule
from tpllm.coordination.models import WorkstreamCommitment
from tpllm.coordination.recommendations import (
    AgentDecisionRecommendation,
    AgentRecommendationStatus,
)


class OrganizationalDecisionMode(str, Enum):
    ADVISORY_ONLY = "advisory_only"
    HUMAN_VALIDATED = "human_validated"
    BOUNDED_AUTONOMOUS = "bounded_autonomous"


class OrganizationalDecisionRisk(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


_RISK_ORDER = {
    OrganizationalDecisionRisk.LOW: 0,
    OrganizationalDecisionRisk.MODERATE: 1,
    OrganizationalDecisionRisk.HIGH: 2,
    OrganizationalDecisionRisk.CRITICAL: 3,
}


def risk_at_most(
    risk: OrganizationalDecisionRisk, ceiling: OrganizationalDecisionRisk
) -> bool:
    return _RISK_ORDER[risk] <= _RISK_ORDER[ceiling]


# Categories the agent may never finalize autonomously, at any
# confidence. Matched against the decision question and capsule text.
NEVER_AUTONOMOUS_MARKERS: tuple[str, ...] = (
    "terminate employment", "termination", "fire ", "layoff", "lay off",
    "hiring", "hire ", "compensation", "salary", "promotion",
    "disciplinary", "discipline",
    "legal admission", "liability", "regulatory waiver", "compliance waiver",
    "security exception", "credential grant", "grant access",
    "wire transfer", "financial transfer", "acquisition",
)

_CRITICAL_MARKERS = NEVER_AUTONOMOUS_MARKERS

_HIGH_MARKERS: tuple[str, ...] = (
    "launch scope", "customer commitment", "partner commitment",
    "irreversible", "migration cutover", "contract", "sign ",
    "resource commitment", "headcount",
)

_MODERATE_MARKERS: tuple[str, ...] = (
    "architecture", "technical approach", "rollout", "configuration",
    "schema",
)


class OrganizationalDecisionAuthorityPolicy(BaseModel):
    """Explicit, scoped, expiring delegation. Nothing here is inferred."""
    id: str = "policy-default"
    organization_id: str
    mode: OrganizationalDecisionMode = (
        OrganizationalDecisionMode.HUMAN_VALIDATED
    )
    autonomous_allowed_decision_types: list[str] = Field(default_factory=list)
    autonomous_allowed_project_ids: list[str] = Field(default_factory=list)
    autonomous_allowed_workstream_ids: list[str] = Field(default_factory=list)
    maximum_autonomous_risk: OrganizationalDecisionRisk = (
        OrganizationalDecisionRisk.LOW
    )
    minimum_autonomous_confidence: float = 0.90
    minimum_option_margin: float = 0.15
    require_reversible_decision: bool = True
    maximum_autonomous_decisions_per_day: int = 20
    auto_expiry_hours: int = 24
    human_review_required_after_execution: bool = True
    created_at: datetime
    expires_at: datetime | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "OrganizationalDecisionAuthorityPolicy":
        if not (0.0 <= self.minimum_autonomous_confidence <= 1.0):
            raise ValueError(
                "minimum_autonomous_confidence must be within [0, 1]."
            )
        if self.maximum_autonomous_risk == OrganizationalDecisionRisk.CRITICAL:
            raise ValueError(
                "A policy may never authorize autonomous CRITICAL "
                "decisions."
            )
        return self


class OrganizationalAutonomyState(BaseModel):
    """Organizational kill switch, separate from the calibration
    kill switch. Checked before everything else."""
    enabled: bool = True
    kill_switch_engaged: bool = False
    reason: str | None = None
    updated_at: datetime


class OrganizationalAutonomyBudget(BaseModel):
    policy_id: str
    max_decisions_per_day: int
    current_day: date
    decisions_used: int = 0

    @property
    def exhausted(self) -> bool:
        return self.decisions_used >= self.max_decisions_per_day


class DecisionAuthorityAssessment(BaseModel):
    recommendation_id: str
    mode: OrganizationalDecisionMode
    decision_risk: OrganizationalDecisionRisk
    autonomous_eligible: bool
    blockers: list[str] = Field(default_factory=list)
    authority_reason: str
    policy_id: str | None = None


# ── Risk classification ──────────────────────────────────────────────

def _capsule_text(
    capsule: DecisionCapsule, recommendation: AgentDecisionRecommendation
) -> str:
    parts = [
        capsule.decision_question,
        recommendation.decision_question,
        capsule.impact_if_decided,
        capsule.impact_if_delayed,
        " ".join(o.description for o in capsule.options),
        " ".join(o.label for o in capsule.options),
    ]
    return " ".join(parts).lower()


def classify_organizational_decision_risk(
    *,
    capsule: DecisionCapsule,
    recommendation: AgentDecisionRecommendation,
    commitments: list[WorkstreamCommitment],
    affected_kpis: list[object],
) -> OrganizationalDecisionRisk:
    """Transparent, deterministic classification from reversibility,
    blast radius, external commitments, launch scope, and
    security/legal/financial implications."""
    text = _capsule_text(capsule, recommendation)

    if any(marker in text for marker in _CRITICAL_MARKERS):
        return OrganizationalDecisionRisk.CRITICAL

    external = [
        c for c in commitments
        if c.commitment_type.value in ("customer", "partner", "regulatory")
    ]
    if external:
        return OrganizationalDecisionRisk.HIGH

    recommended = next(
        (
            a for a in recommendation.assessments
            if a.option_id == recommendation.recommended_option_id
        ),
        None,
    )
    if recommended is not None and recommended.reversible is False:
        return OrganizationalDecisionRisk.HIGH

    if any(marker in text for marker in _HIGH_MARKERS):
        return OrganizationalDecisionRisk.HIGH

    # Blast radius: many affected milestones or KPIs raises the class.
    if len(capsule.affected_milestones) >= 2 or len(affected_kpis) >= 2:
        return OrganizationalDecisionRisk.MODERATE
    if any(marker in text for marker in _MODERATE_MARKERS):
        return OrganizationalDecisionRisk.MODERATE

    return OrganizationalDecisionRisk.LOW


def is_never_autonomous_category(
    *,
    capsule: DecisionCapsule,
    recommendation: AgentDecisionRecommendation,
) -> str | None:
    """Return the matched marker when the decision falls in a category
    the agent may never finalize, else None."""
    text = _capsule_text(capsule, recommendation)
    for marker in NEVER_AUTONOMOUS_MARKERS:
        if marker in text:
            return marker.strip()
    return None


# ── Eligibility ──────────────────────────────────────────────────────

def can_agent_finalize_decision(
    *,
    recommendation: AgentDecisionRecommendation,
    capsule: DecisionCapsule,
    policy: OrganizationalDecisionAuthorityPolicy,
    decision_risk: OrganizationalDecisionRisk,
    recent_autonomous_decisions: list[object],
    now: datetime,
    autonomy_state: OrganizationalAutonomyState | None = None,
    budget: OrganizationalAutonomyBudget | None = None,
    decision_type: str | None = None,
) -> DecisionAuthorityAssessment:
    """Fail closed: every unmet condition is recorded as a blocker and
    autonomy is denied."""
    blockers: list[str] = []

    def assess(reason: str) -> DecisionAuthorityAssessment:
        return DecisionAuthorityAssessment(
            recommendation_id=recommendation.id,
            mode=policy.mode,
            decision_risk=decision_risk,
            autonomous_eligible=not blockers,
            blockers=blockers,
            authority_reason=reason,
            policy_id=policy.id,
        )

    # 0. Kill switch always wins, checked first.
    if autonomy_state is not None and (
        autonomy_state.kill_switch_engaged or not autonomy_state.enabled
    ):
        blockers.append(
            f"Organizational autonomy kill switch engaged"
            + (f": {autonomy_state.reason}" if autonomy_state.reason else "")
        )
        return assess("Autonomy halted; decision returns to humans.")

    # 1. Category ban precedes every other consideration.
    banned = is_never_autonomous_category(
        capsule=capsule, recommendation=recommendation,
    )
    if banned:
        blockers.append(
            f"Decision category '{banned}' can never be finalized "
            f"autonomously, at any confidence."
        )

    # 2. Mode.
    if policy.mode != OrganizationalDecisionMode.BOUNDED_AUTONOMOUS:
        blockers.append(
            f"Organization mode is {policy.mode.value}; autonomous "
            f"finalization requires an explicit bounded_autonomous policy."
        )

    # 3. Policy freshness.
    if policy.expires_at is not None and policy.expires_at <= now:
        blockers.append("Authority policy has expired.")

    # 4. Decision type scope.
    if decision_type is not None:
        if decision_type not in policy.autonomous_allowed_decision_types:
            blockers.append(
                f"Decision type '{decision_type}' is not in the policy's "
                f"allowed types."
            )
    elif not policy.autonomous_allowed_decision_types:
        blockers.append("Policy authorizes no decision types.")

    # 5. Project / workstream scope.
    if policy.autonomous_allowed_project_ids and (
        recommendation.project_id not in policy.autonomous_allowed_project_ids
    ):
        blockers.append(
            f"Project '{recommendation.project_id}' is outside the policy "
            f"scope."
        )
    if policy.autonomous_allowed_workstream_ids and (
        recommendation.workstream_id
        not in policy.autonomous_allowed_workstream_ids
    ):
        blockers.append(
            f"Workstream '{recommendation.workstream_id}' is outside the "
            f"policy scope."
        )

    # 6. Risk ceiling.
    if not risk_at_most(decision_risk, policy.maximum_autonomous_risk):
        blockers.append(
            f"Decision risk {decision_risk.value} exceeds the policy "
            f"ceiling {policy.maximum_autonomous_risk.value}."
        )

    # 7. Recommendation must actually exist.
    if recommendation.status != (
        AgentRecommendationStatus.RECOMMENDATION_AVAILABLE
    ):
        blockers.append(
            f"Recommendation status is {recommendation.status.value}; "
            f"there is nothing to finalize."
        )

    # 8. Confidence.
    if recommendation.confidence < policy.minimum_autonomous_confidence:
        blockers.append(
            f"Confidence {recommendation.confidence:.2f} is below the "
            f"autonomous threshold "
            f"{policy.minimum_autonomous_confidence:.2f}."
        )

    # 9. Option margin.
    recommended = next(
        (
            a for a in recommendation.assessments
            if a.option_id == recommendation.recommended_option_id
        ),
        None,
    )
    margin_note = recommendation.rationale.summary
    if "ahead of the next option by" in margin_note:
        try:
            margin = float(
                margin_note.split("ahead of the next option by")[1]
                .split(",")[0].strip()
            )
        except (ValueError, IndexError):
            margin = None
        if margin is not None and margin < policy.minimum_option_margin:
            blockers.append(
                f"Option margin {margin:.2f} is below the required "
                f"{policy.minimum_option_margin:.2f}."
            )

    # 10. Unresolved critical unknowns.
    if recommendation.rationale.unresolved_unknowns:
        blockers.append(
            f"{len(recommendation.rationale.unresolved_unknowns)} unresolved "
            f"critical unknown(s) remain."
        )

    # 11. Hard-constraint contradictions among eligible options.
    if recommended is not None and recommended.violated_hard_constraints:
        blockers.append(
            "The recommended option violates a hard constraint."
        )

    # 12. Reversibility.
    if policy.require_reversible_decision:
        if recommended is None or recommended.reversible is not True:
            blockers.append(
                "Policy requires a reversible decision; reversibility is "
                "not established."
            )

    # 13. Daily budget.
    if budget is not None and budget.exhausted:
        blockers.append(
            f"Daily autonomy budget exhausted "
            f"({budget.decisions_used}/{budget.max_decisions_per_day})."
        )
    elif budget is None and len(recent_autonomous_decisions) >= (
        policy.maximum_autonomous_decisions_per_day
    ):
        blockers.append("Daily autonomy budget exhausted.")

    if blockers:
        return assess(
            "Not autonomously eligible; the recommendation and its context "
            "go to the human decision authority."
        )
    return assess(
        f"Autonomously eligible under policy {policy.id}: "
        f"{decision_risk.value} risk, confidence "
        f"{recommendation.confidence:.2f}, within scope and budget."
    )
